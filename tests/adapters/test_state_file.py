"""Tests for the state.json repository."""

import json
import logging
import os
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute.adapters.state_file import JsonStateRepository
from paperroute.adapters.state_migrations import (
    SCHEMA_VERSION,
    regional_to_coach,
)
from paperroute.city import SYDNEY, SYDNEY_ZONE, SydneyMode
from paperroute.core.pin_choices import clean_nickname
from paperroute.core.saved import (
    DEFAULT_SAVED,
    BoardSettings,
    ConnectionMap,
    LiveView,
    Lookahead,
    Quota,
    Rate,
    Saved,
    Settings,
)
from paperroute.core.state import initial_state
from paperroute.core.state_views import saved_of
from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_testkit.core.connection_factories import WORK, WORK_CHANGE
from paperroute_testkit.core.factories import pin_at
from paperroute_transit.core.auto_model import AutoState, Before, Occurrence
from paperroute_transit.core.model import AutoWindow, Pin, StopPlace, Way
from tests.core.test_auto_pins import is_shown, started, ticked, trusted

ROTATED: Final[Settings] = Settings(device=DeviceSettings(rotate=True))

NAMES = st.text(
    alphabet=st.characters(exclude_categories=['Cs']),
    max_size=20,
) | st.sampled_from(['Café 🚌', 'A · B'])
COORDINATES = st.floats(allow_nan=False, allow_infinity=False)
POINTS = st.none() | st.tuples(COORDINATES, COORDINATES)
PLACES = st.builds(
    StopPlace,
    stop_id=st.text(min_size=1, max_size=8),
    name=NAMES,
    point=POINTS,
)
PINS = st.builds(
    Pin,
    origin=PLACES,
    destination=st.none() | PLACES,
    kind=st.none() | st.sampled_from(SydneyMode),
    routes=st.lists(st.text(max_size=5), max_size=3).map(tuple),
    nickname=NAMES.map(lambda text: clean_nickname(text=text)),
    only=st.lists(st.text(max_size=5), max_size=3).map(tuple),
)


MINUTES = st.integers(0, 24 * 60 - 1)


def clock(*, minute: int) -> time:
    """
    Give the time of day a minute count stands for.

    Parameters
    ----------
    minute : int
        Minutes after midnight.

    Returns
    -------
    time
        The time of day.
    """
    return time(minute // 60, minute % 60)


@st.composite
def settings_values(draw: st.DrawFn) -> Settings:
    """
    Draw settings the Settings page could store.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Settings
        Any rates, mode and switches; 0-2 separate peak windows; any
        My hours, wrapping or empty.
    """
    count = draw(st.integers(0, 2))
    ends = sorted(
        draw(
            st.lists(
                MINUTES,
                min_size=count * 2,
                max_size=count * 2,
                unique=True,
            ),
        ),
    )
    peak = tuple(
        Window(start=clock(minute=start), end=clock(minute=end))
        for start, end in zip(ends[::2], ends[1::2])
    )
    return Settings(
        device=DeviceSettings(
            auto_rebuild=draw(st.booleans()),
            quiet_mode=draw(st.sampled_from(QuietMode)),
            my_hours=Window(
            start=clock(minute=draw(MINUTES)),
            end=clock(minute=draw(MINUTES)),
        ),
            rotate=draw(st.booleans()),
            larger_text=draw(st.booleans()),
            inverted_lists=draw(st.booleans()),
        ),
        board=BoardSettings(
            peak_rate=draw(st.sampled_from(Rate)),
            offpeak_rate=draw(st.sampled_from(Rate)),
            peak_windows=peak,
        ),
    )


@st.composite
def saved_values(draw: st.DrawFn) -> Saved:
    """
    Draw a Saved value that the reducer could produce.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Saved
        Zero to five pins, a valid current index and any view.
    """
    pins = tuple(draw(st.lists(PINS, max_size=5)))
    unpinned = draw(st.none() | PINS) if pins else draw(PINS)
    return Saved(
        pins=pins,
        current=draw(st.integers(0, max(len(pins) - 1, 0))),
        unpinned=unpinned,
        view=draw(st.sampled_from(LiveView)),
        settings=draw(settings_values()),
    )


def sample() -> Saved:
    """
    Build a small saved state.

    Returns
    -------
    Saved
        One pin with a destination, twice.
    """
    pin = Pin(
        origin=StopPlace(stop_id='a', name='A', point=(1.5, -2.5)),
        destination=StopPlace(stop_id='b', name='B', point=None),
        kind=SydneyMode.BUS,
    )
    return Saved(
        pins=(pin, pin),
        current=1,
        unpinned=None,
        view=LiveView.STRIP,
    )


@pytest.fixture(name='path')
def path_fixture(tmp_path: Path) -> Path:
    """
    The state file's path.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Returns
    -------
    Path
        A path that doesn't exist yet.
    """
    return tmp_path / 'state.json'


@settings(max_examples=60, deadline=None)
@given(saved=saved_values())
def test_round_trip(saved: Saved) -> None:
    """What is saved is what loads."""
    with tempfile.TemporaryDirectory() as directory:
        repository = JsonStateRepository(
            path=Path(directory) / 's.json',
            city=SYDNEY,
        )
        repository.save(saved=saved)
        loaded = repository.load()
    assert loaded.saved == saved
    assert loaded.stored


def test_missing_file_gives_defaults_quietly(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No file is normal: defaults, not stored, nothing logged."""
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert not caplog.records


def test_truncated_file_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A cut-off file gives defaults and an error log."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    path.write_text(path.read_text()[:40])
    loaded = repository.load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


@pytest.mark.parametrize(
    'text',
    ['not json', '[]', '{}', '{"schema_version": "1"}', '\ud800'],
)
def test_garbage_and_wrong_types_give_defaults(
    path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Unusable content gives the defaults."""
    path.write_bytes(text.encode('utf-8', errors='surrogatepass'))
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_wrong_field_type_gives_defaults(path: Path) -> None:
    """A field of the wrong type is rejected."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['current'] = 'big'
    path.write_text(json.dumps(data))
    assert repository.load().saved == DEFAULT_SAVED


def test_future_schema_falls_back(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file from a newer app is not guessed at."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert caplog.records


def test_unusable_file_is_kept_aside(path: Path) -> None:
    """A newer file survives a load and a save under a ``.bad-`` name."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(data))
    repository.save(saved=repository.load().saved)
    kept = list(path.parent.glob('state.json.bad-*'))
    assert len(kept) == 1
    assert json.loads(kept[0].read_text()) == data


def test_only_the_newest_unusable_files_are_kept(path: Path) -> None:
    """Older ``.bad-`` files beyond the newest two are deleted."""
    for stamp in ('100', '20', '3'):
        path.with_name(f'state.json.bad-{stamp}').write_text('old')
    path.write_text('not json')
    JsonStateRepository(path=path, city=SYDNEY).load()
    kept = sorted(p.name for p in path.parent.glob('state.json.bad-*'))
    assert len(kept) == 2
    assert 'state.json.bad-100' in kept
    assert not path.exists()


def test_current_out_of_range_is_clamped(path: Path) -> None:
    """A current index past the last pin becomes the last pin."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['current'] = 9
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.saved.current == 1
    assert loaded.stored


def test_no_pins_and_no_unpinned_gives_default_place(path: Path) -> None:
    """An empty pin list still shows a place."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['pins'] = []
    path.write_text(json.dumps(data))
    loaded = repository.load().saved
    assert loaded.unpinned == DEFAULT_SAVED.unpinned
    assert loaded.current == 0


@pytest.fixture(name='locked')
def locked_fixture(tmp_path: Path) -> Iterator[Path]:
    """
    A state file in a read-only directory.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Yields
    ------
    Path
        The state file, holding saved state.
    """
    directory = tmp_path / 'ro'
    directory.mkdir()
    path = directory / 'state.json'
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield path
    directory.chmod(stat.S_IRWXU)


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores permissions')
def test_failed_write_keeps_old_file(locked: Path) -> None:
    """A write that can't happen leaves the old file and no temp file."""
    before = locked.read_text()
    repository = JsonStateRepository(path=locked, city=SYDNEY)
    with pytest.raises(OSError):
        repository.save(saved=DEFAULT_SAVED)
    assert locked.read_text() == before
    assert [p.name for p in locked.parent.iterdir()] == ['state.json']


def test_state_file_round_trip_has_no_zoom(path: Path) -> None:
    """The map zoom is not saved, and a saved state loads back the same."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    assert 'zoom' not in json.loads(path.read_text())
    assert not hasattr(sample(), 'zoom')
    assert repository.load().saved == sample()


def test_file_holds_no_secret_keys(path: Path) -> None:
    """Only the documented keys are written."""
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    assert set(data) == {
        'schema_version',
        'pins',
        'current',
        'unpinned',
        'view',
        'settings',
        'quota',
        'auto',
    }
    assert set(data['pins'][0]) == {
        'origin',
        'destination',
        'kind',
        'routes',
        'nickname',
        'only',
        'windows',
        'ways',
        'never',
    }


def test_written_json_shape(path: Path) -> None:
    """The version is current and stops carry their metre coordinates."""
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    assert data['schema_version'] == SCHEMA_VERSION
    assert data['settings']['auto_rebuild'] is True
    assert data['pins'][0]['origin'] == {
        'stop_id': 'a',
        'name': 'A',
        'x': 1.5,
        'y': -2.5,
    }
    assert data['pins'][0]['destination']['x'] is None


def test_load_removes_stale_temp_files(path: Path) -> None:
    """A temp file left by a power cut is deleted on load."""
    stale = path.parent / '.state.json.abc123'
    stale.write_text('half')
    other = path.parent / 'keep.txt'
    other.write_text('x')
    JsonStateRepository(path=path, city=SYDNEY).load()
    assert not stale.exists()
    assert other.exists()


def test_failed_rename_removes_temp_file(path: Path) -> None:
    """When the rename fails, the temp file is cleaned up."""
    path.mkdir()
    (path / 'inner').write_text('x')
    with pytest.raises(OSError):
        JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    assert [p.name for p in path.parent.iterdir()] == ['state.json']


def test_written_file_is_world_readable(path: Path) -> None:
    """The file holds no secrets and is mode 0644."""
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_directory_at_path_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Something that isn't a file at the path gives defaults and a log."""
    path.mkdir()
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_deeply_nested_json_gives_defaults(path: Path) -> None:
    """JSON nested past the recursion limit gives defaults."""
    path.write_text('[' * 100000)
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored


def v1_pin(*, kind: str, routes: list[str]) -> dict[str, object]:
    """
    Build a stored version 1 pin.

    Parameters
    ----------
    kind : str
        Stored kind.
    routes : list[str]
        Stored route badges.

    Returns
    -------
    dict[str, object]
        The pin as JSON.
    """
    stop = {'stop_id': 'a', 'name': 'A', 'x': 1.0, 'y': 2.0}
    return {
        'origin': stop, 'destination': None, 'kind': kind, 'routes': routes,
    }


def test_migrate_regional_pin_to_coach(path: Path) -> None:
    """Regional pins load as Coach, or Train for a TrainLink line."""
    path.write_text(json.dumps({
        'schema_version': 1,
        'pins': [
            v1_pin(kind='regional', routes=['C1']),
            v1_pin(kind='regional', routes=['STH']),
            v1_pin(kind='regional', routes=[]),
            v1_pin(kind='bus', routes=['333']),
        ],
        'current': 0,
        'unpinned': v1_pin(kind='regional', routes=['NRC', 'WST']),
        'view': 'strip',
        'zoom': 0,
    }))
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert [pin.kind for pin in loaded.saved.pins] == [
        SydneyMode.COACH, SydneyMode.TRAIN, SydneyMode.COACH, SydneyMode.BUS,
    ]
    assert loaded.saved.unpinned is not None
    assert loaded.saved.unpinned.kind == SydneyMode.TRAIN
    assert not list(path.parent.glob('state.json.bad-*'))


def test_migrate_regional_with_no_unpinned_place(path: Path) -> None:
    """A null unpinned place stays null."""
    path.write_text(json.dumps({
        'schema_version': 1,
        'pins': [v1_pin(kind='regional', routes=['C1'])],
        'current': 0,
        'unpinned': None,
        'view': 'strip',
        'zoom': 0,
    }))
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert loaded.saved.unpinned is None
    assert loaded.saved.pins[0].kind == SydneyMode.COACH


def test_regional_to_coach_leaves_a_malformed_pin_list_alone() -> None:
    """Data the migration cannot read is passed on for validation to fail."""
    data: dict[str, object] = {'pins': 'oops', 'unpinned': 3}
    assert regional_to_coach(data) == data


def test_migrates_v2_with_auto_rebuild_on(path: Path) -> None:
    """A version 2 file loads with the rebuild on and saves as current."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    del data['settings']
    data['schema_version'] = 2
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == sample()
    assert loaded.saved.settings.device.auto_rebuild is True
    repository.save(saved=loaded.saved)
    assert json.loads(path.read_text())['schema_version'] == SCHEMA_VERSION


def test_round_trips_settings(path: Path) -> None:
    """A switched-off rebuild survives a save and a load."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    manual = Settings(device=DeviceSettings(auto_rebuild=False))
    saved = replace(sample(), settings=manual)
    repository.save(saved=saved)
    assert repository.load().saved.settings.device.auto_rebuild is False


def test_migrates_v3_to_v4_with_empty_quota(path: Path) -> None:
    """A version 3 file loads with no calls counted and saves as current."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    del data['quota']
    data['schema_version'] = 3
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == sample()
    assert loaded.saved.quota == Quota(day=None, calls=0)
    repository.save(saved=loaded.saved)
    stored = json.loads(path.read_text())
    assert stored['schema_version'] == SCHEMA_VERSION
    assert stored['quota'] == {'day': None, 'calls': 0}


def test_round_trips_quota(path: Path) -> None:
    """The day and the calls counted survive a save and a load."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    quota = Quota(day=date(2026, 10, 21), calls=1234)
    repository.save(saved=replace(sample(), quota=quota))
    assert json.loads(path.read_text())['quota'] == {
        'day': '2026-10-21',
        'calls': 1234,
    }
    assert repository.load().saved.quota == quota


def test_negative_calls_reset_the_quota_only(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A count below zero resets the quota and keeps the pins."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['quota']['calls'] = -1
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == sample()
    assert loaded.saved.quota == Quota()
    assert caplog.records


def test_future_schema_is_not_guessed_at(path: Path) -> None:
    """A file newer than this app is not guessed at."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(data))
    assert repository.load().saved == DEFAULT_SAVED


def write_v4(*, path: Path, auto_rebuild: bool) -> None:
    """
    Save the sample and rewrite it as a version 4 file.

    Parameters
    ----------
    path : Path
        The state file.
    auto_rebuild : bool
        The stored rebuild setting.
    """
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = 4
    data['settings'] = {'auto_rebuild': auto_rebuild}
    path.write_text(json.dumps(data))


def test_migrates_v4_to_v5_with_defaults(path: Path) -> None:
    """A version 4 file keeps its rebuild setting and saves as current."""
    write_v4(path=path, auto_rebuild=False)
    repository = JsonStateRepository(path=path, city=SYDNEY)
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.settings == Settings(
        device=DeviceSettings(auto_rebuild=False),
    )
    assert loaded.saved.pins == sample().pins
    repository.save(saved=loaded.saved)
    stored = json.loads(path.read_text())
    assert stored['schema_version'] == SCHEMA_VERSION
    assert stored['settings']['peak_windows'] == [
        {'start': '06:30', 'end': '10:00'},
        {'start': '15:00', 'end': '19:00'},
    ]


def test_round_trips_all_settings(path: Path) -> None:
    """Every setting off its default survives a save and a load."""
    changed = Settings(
        device=DeviceSettings(
            auto_rebuild=False,
            quiet_mode=QuietMode.TIMETABLE,
            my_hours=Window(start=time(22, 15), end=time(6)),
            rotate=True,
            larger_text=True,
            inverted_lists=True,
        ),
        board=BoardSettings(
            peak_rate=Rate.SLOW,
            offpeak_rate=Rate.STEADY,
            peak_windows=(Window(start=time(7), end=time(9)),),
        ),
    )
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=changed))
    assert repository.load().saved.settings == changed


def test_bad_time_falls_back(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A time that can't be read resets that setting and keeps the pins."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    data['settings']['peak_windows'][0]['start'] = '25:00'
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.pins == sample().pins
    assert loaded.saved.settings == ROTATED
    assert caplog.records


def load_with_settings(
    *, path: Path, changes: dict[str, object]
) -> Saved:
    """
    Save the sample with rotate on, change its stored settings and load.

    Parameters
    ----------
    path : Path
        The state file.
    changes : dict[str, object]
        Stored settings fields to overwrite.

    Returns
    -------
    Saved
        The loaded state, which must still be stored.
    """
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    data['settings'].update(changes)
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.pins == sample().pins
    return loaded.saved


@pytest.mark.parametrize(
    'windows',
    [
        [{'start': '22:00', 'end': '02:00'}],
        [{'start': '08:00', 'end': '08:00'}],
        [
            {'start': '06:00', 'end': '10:00'},
            {'start': '09:00', 'end': '11:00'},
        ],
    ],
    ids=['wrapped', 'empty', 'overlapping'],
)
def test_bad_peak_windows_reset_only_them(
    path: Path, caplog: pytest.LogCaptureFixture, windows: list[object]
) -> None:
    """Wrapping, empty or overlapping peak windows reset to the default."""
    saved = load_with_settings(path=path, changes={'peak_windows': windows})
    assert saved.settings == ROTATED
    assert saved.settings.device.my_hours == Window(
        start=time(23),
        end=time(5, 30),
    )
    assert caplog.records


@pytest.mark.parametrize(
    'hours',
    [
        {'start': '23:00', 'end': '05:30'},
        {'start': '04:00', 'end': '04:00'},
    ],
    ids=['wrapping', 'equal'],
)
def test_my_hours_may_wrap_or_be_empty(
    path: Path, caplog: pytest.LogCaptureFixture, hours: dict[str, str]
) -> None:
    """My hours load unchanged when they wrap midnight or are empty."""
    saved = load_with_settings(path=path, changes={'my_hours': hours})
    start = time.fromisoformat(hours['start'])
    end = time.fromisoformat(hours['end'])
    assert saved.settings.device.my_hours == Window(start=start, end=end)
    assert not caplog.records


@pytest.mark.parametrize(
    ('changes', 'expected'),
    [
        (
            {'peak_rate': '10s', 'offpeak_rate': '60s'},
            Settings(
                device=DeviceSettings(rotate=True),
                board=BoardSettings(offpeak_rate=Rate.STEADY),
            ),
        ),
        (
            {
                'my_hours': {'start': '7am', 'end': '05:30'},
                'larger_text': True,
            },
            Settings(device=DeviceSettings(rotate=True, larger_text=True)),
        ),
        (
            {'connection_map': 'first', 'larger_text': True},
            Settings(device=DeviceSettings(rotate=True, larger_text=True)),
        ),
        (
            {'clean_after': 45, 'larger_text': True},
            Settings(device=DeviceSettings(rotate=True, larger_text=True)),
        ),
    ],
    ids=['bad-rate', 'bad-my-hours', 'retired-first-map', 'bad-clean'],
)
def test_bad_setting_resets_only_itself(
    path: Path, changes: dict[str, object], expected: Settings
) -> None:
    """A bad or retired setting resets that field; the others survive."""
    saved = load_with_settings(path=path, changes=changes)
    assert saved.settings == expected


def test_file_without_clean_after_cleans_after_thirty(path: Path) -> None:
    """A state.json from before the cleaning choice loads with 30."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    del data['settings']['clean_after']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.settings == Settings(
        device=DeviceSettings(rotate=True, clean_after=30),
    )


def test_round_trips_clean_after(path: Path) -> None:
    """A cleaning choice survives a save and a load."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    changed = Settings(device=DeviceSettings(clean_after=120))
    repository.save(saved=replace(sample(), settings=changed))
    assert repository.load().saved.settings == changed


def test_migrates_to_pin_fields(path: Path) -> None:
    """A version 5 file gains an empty nickname and filter."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    saved = replace(sample(), unpinned=sample().pins[0])
    repository.save(saved=saved)
    data = json.loads(path.read_text())
    data['schema_version'] = 5
    for pin in [*data['pins'], data['unpinned']]:
        del pin['nickname']
        del pin['only']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.unpinned is not None
    for pin in (*loaded.saved.pins, loaded.saved.unpinned):
        assert pin.nickname is None
        assert pin.only == ()
    repository.save(saved=loaded.saved)
    stored = json.loads(path.read_text())
    assert stored['schema_version'] == SCHEMA_VERSION


def test_migrates_v6_to_v7_with_lookahead(path: Path) -> None:
    """A version 6 file keeps its settings and gains the lookahead."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    data['schema_version'] = 6
    del data['settings']['alert_lookahead']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.pins == sample().pins
    assert loaded.saved.settings.device.rotate is True
    assert loaded.saved.settings.board.alert_lookahead == Lookahead.WEEK
    repository.save(saved=loaded.saved)
    assert json.loads(path.read_text())['schema_version'] == SCHEMA_VERSION


def test_bad_lookahead_repaired_alone(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A lookahead nobody offers resets itself; the rest survive."""
    saved = load_with_settings(
        path=path, changes={'alert_lookahead': '2w'},
    )
    assert saved.settings == ROTATED
    assert saved.settings.board.alert_lookahead == Lookahead.WEEK
    assert any(
        record.levelno == logging.WARNING for record in caplog.records
    )


def test_round_trips_lookahead(path: Path) -> None:
    """The longest lookahead survives a save and a load."""
    settings = Settings(board=BoardSettings(alert_lookahead=Lookahead.ALL))
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=settings))
    assert repository.load().saved.settings == settings


def test_round_trips_nickname_and_filter(path: Path) -> None:
    """A nickname and route filter survive a save and a load."""
    pin = Pin(
        origin=StopPlace(stop_id='a', name='A', point=None),
        nickname='Work',
        only=('606', '607X'),
    )
    saved = replace(DEFAULT_SAVED, pins=(pin,), unpinned=pin)
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=saved)
    assert repository.load().saved.pins == (pin,)


def stored_with(*, path: Path, pin_fields: dict[str, object]) -> dict[str, Any]:
    """
    Save the sample, then overwrite fields on its second pin.

    Parameters
    ----------
    path : Path
        The state file.
    pin_fields : dict[str, object]
        Fields to set on the second stored pin.

    Returns
    -------
    dict[str, Any]
        The rewritten file's data.
    """
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    data: dict[str, Any] = json.loads(path.read_text())
    data['pins'][1].update(pin_fields)
    path.write_text(json.dumps(data))
    return data


@pytest.mark.parametrize('bad', [5, ['x'], {}])
def test_bad_nickname_resets_only_itself(path: Path, bad: object) -> None:
    """An unreadable nickname becomes None; the pin keeps the rest."""
    stored_with(path=path, pin_fields={'nickname': bad, 'only': ['1']})
    pins = JsonStateRepository(path=path, city=SYDNEY).load().saved.pins
    assert pins[1] == replace(sample().pins[1], only=('1',))
    assert pins[0] == sample().pins[0]


def test_long_nickname_is_trimmed(path: Path) -> None:
    """A nickname over the limit is cleaned and cut."""
    stored_with(path=path, pin_fields={'nickname': ' ' + 'a' * 40})
    pin = JsonStateRepository(path=path, city=SYDNEY).load().saved.pins[1]
    assert pin.nickname == 'a' * 24


@pytest.mark.parametrize('bad', [5, 'ab', [1], None])
def test_bad_only_resets_only_itself(path: Path, bad: object) -> None:
    """An unreadable route filter becomes empty; the nickname stays."""
    stored_with(path=path, pin_fields={'nickname': 'Work', 'only': bad})
    pin = JsonStateRepository(path=path, city=SYDNEY).load().saved.pins[1]
    assert pin.only == ()
    assert pin.nickname == 'Work'


def test_broken_pin_is_dropped_and_the_rest_kept(path: Path) -> None:
    """A pin that stays invalid is dropped on its own."""
    stored_with(path=path, pin_fields={'kind': 'spaceship'})
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert loaded.saved.pins == (sample().pins[0],)


def test_broken_unpinned_place_is_dropped(path: Path) -> None:
    """An unusable unpinned place goes; the pins stay."""
    data = stored_with(path=path, pin_fields={})
    data['unpinned'] = {'origin': 5}
    path.write_text(json.dumps(data))
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.saved.pins == sample().pins


def test_v5_pin_with_fields_survives_migration(path: Path) -> None:
    """A version 5 pin that already has the new fields keeps them."""
    stored_with(
        path=path,
        pin_fields={
            'nickname': 'Work', 'only': ['606'], 'destination': None,
        },
    )
    data = json.loads(path.read_text())
    data['schema_version'] = 5
    path.write_text(json.dumps(data))
    pin = JsonStateRepository(path=path, city=SYDNEY).load().saved.pins[1]
    assert (pin.nickname, pin.only) == ('Work', ('606',))


WEEKDAY_WINDOW = AutoWindow(frozenset(range(5)), time(7, 30), time(9))
GOOD_WINDOW: dict[str, object] = {
    'days': ['mon'], 'start': '07:00', 'end': '08:00',
}


def test_round_trips_windows_and_auto(path: Path) -> None:
    """Windows and the auto pin state survive a save and a load."""
    pin = replace(sample().pins[0], windows=(WEEKDAY_WINDOW,))
    start = datetime(2026, 10, 16, 7, 30, tzinfo=SYDNEY_ZONE)
    auto = AutoState(
        occurrence=Occurrence(
            ref='abc', start=start, end=start.replace(hour=9),
        ),
        before=Before(place=sample().pins[0], pinned=False),
        overridden_at=start.replace(minute=45),
    )
    saved = replace(sample(), pins=(pin,), current=0, auto=auto)
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=saved)
    assert repository.load().saved == saved
    stored = json.loads(path.read_text())['pins'][0]['windows'][0]
    assert stored['days'] == ['mon', 'tue', 'wed', 'thu', 'fri']
    assert stored['start'] == '07:30'


def test_migrates_v7_to_v8(path: Path) -> None:
    """A version 7 file gains empty windows and no auto pin state."""
    saved = replace(sample(), unpinned=sample().pins[0])
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=saved)
    data = json.loads(path.read_text())
    data['schema_version'] = 7
    del data['auto']
    for pin in (*data['pins'], data['unpinned']):
        del pin['windows']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == saved
    assert all(pin.windows == () for pin in loaded.saved.pins)
    assert loaded.saved.auto == AutoState()


def test_bad_window_dropped_alone(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A window that can't be read goes; the rest of the file stays."""
    settings = ROTATED
    quota = Quota(day=date(2026, 10, 16), calls=3)
    JsonStateRepository(path=path, city=SYDNEY).save(
        saved=replace(sample(), settings=settings, quota=quota),
    )
    data = json.loads(path.read_text())
    data['pins'][1]['windows'] = [
        GOOD_WINDOW,
        {'days': ['mon'], 'start': '07:00', 'end': '07:00'},
        {'days': ['funday'], 'start': '07:00', 'end': '08:00'},
    ]
    path.write_text(json.dumps(data))
    caplog.clear()
    saved = JsonStateRepository(path=path, city=SYDNEY).load().saved
    assert saved.pins[1].windows == (
        AutoWindow(frozenset({0}), time(7), time(8)),
    )
    assert saved.pins[0] == sample().pins[0]
    assert (saved.settings, saved.quota) == (settings, quota)
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 2


def test_bad_auto_reset_alone(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An auto block that can't be read resets; the pins stay."""
    JsonStateRepository(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    data['auto'] = {'occurrence': 3}
    path.write_text(json.dumps(data))
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert loaded.saved.auto == AutoState()
    assert loaded.saved.pins == sample().pins
    assert any(
        record.levelno == logging.WARNING for record in caplog.records
    )


def test_windows_not_a_list(path: Path) -> None:
    """Windows that are not a list leave the pin with none."""
    stored_with(path=path, pin_fields={'windows': 'x'})
    loaded = JsonStateRepository(path=path, city=SYDNEY).load()
    assert loaded.saved.pins == sample().pins


def test_restart_in_repeated_hour_returns(tmp_path: Path) -> None:
    """A restart mid-window in the repeated hour still goes back."""
    window = AutoWindow(frozenset({6}), time(2), time(2, 45))
    early = replace(pin_at('early', x=200.0), windows=(window,))
    home = pin_at('home', x=100.0)
    state = started(
        pins=(home, early), now=datetime(
            2026, 4, 5, 1, 59, tzinfo=SYDNEY_ZONE,
        ),
    )
    state = ticked(
        state=state, times=(datetime(2026, 4, 5, 2, tzinfo=SYDNEY_ZONE),),
    )
    repository = JsonStateRepository(path=tmp_path / 's.json', city=SYDNEY)
    repository.save(saved=saved_of(state=state))
    now = datetime(2026, 4, 5, 2, 10, tzinfo=SYDNEY_ZONE)
    state = trusted(state=initial_state(
        saved=repository.load().saved, now=now, stored=True,
        city=SYDNEY,
    ))
    loaded = state.auto
    state = ticked(state=state, times=(now,))
    assert is_shown(state=state, pin=early)
    assert state.auto == loaded
    state = ticked(
        state=state, times=(datetime(2026, 4, 5, 2, 45, tzinfo=SYDNEY_ZONE),),
    )
    assert is_shown(state=state, pin=home)


def test_round_trips_change(path: Path) -> None:
    """A connection pin saves and loads back equal."""
    saved = replace(sample(), pins=(WORK,), current=0)
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=saved)
    assert repository.load().saved == saved
    way = json.loads(path.read_text())['pins'][0]['ways'][0]
    assert way['ticked'] is True
    assert way['changes'][0]['allow'] == 7
    assert way['changes'][0]['alight']['stop_id'] == 'G215254'


def test_migrates_v8_to_current(path: Path) -> None:
    """A version 8 file gains no ways and the default map setting."""
    before = Before(place=sample().pins[0], pinned=False)
    saved = replace(
        sample(),
        unpinned=sample().pins[0],
        auto=AutoState(before=before),
    )
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=saved)
    data = json.loads(path.read_text())
    data['schema_version'] = 8
    del data['settings']['connection_map']
    for pin in (*data['pins'], data['unpinned'], data['auto']['before']['place']):
        del pin['ways']
        del pin['never']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == saved
    assert all(not pin.ways for pin in loaded.saved.pins)
    assert loaded.saved.settings.board.connection_map is ConnectionMap.CHANGES


def stored_change(*, path: Path, change: dict[str, object]) -> None:
    """
    Save WORK beside the sample pin, then overwrite WORK's stored change.

    Parameters
    ----------
    path : Path
        The state file.
    change : dict[str, object]
        Fields to set on the first change of WORK's stored way.
    """
    saved = replace(sample(), pins=(sample().pins[0], WORK))
    JsonStateRepository(path=path, city=SYDNEY).save(saved=saved)
    data: dict[str, Any] = json.loads(path.read_text())
    data['pins'][1]['ways'][0]['changes'][0].update(change)
    path.write_text(json.dumps(data))


def test_bad_change_drops_way_alone(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A change that can't be read drops its way with one warning."""
    settings = ROTATED
    JsonStateRepository(path=path, city=SYDNEY).save(
        saved=replace(sample(), pins=(sample().pins[0], WORK), settings=settings),
    )
    data = json.loads(path.read_text())
    data['pins'][1]['ways'][0]['changes'][0] = {'alight': 3}
    path.write_text(json.dumps(data))
    caplog.clear()
    saved = JsonStateRepository(path=path, city=SYDNEY).load().saved
    assert saved.pins == (sample().pins[0], replace(WORK, ways=()))
    assert saved.settings == settings
    assert saved.auto == AutoState()
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1


@pytest.mark.parametrize(('allow', 'kept'), [(0, 1), (90, 60)])
def test_allow_out_of_range_clamped(
    path: Path, allow: int, kept: int,
) -> None:
    """An allowance outside 1-60 is clamped; the rest is kept."""
    stored_change(path=path, change={'allow': allow})
    pin = JsonStateRepository(path=path, city=SYDNEY).load().saved.pins[1]
    assert pin == replace(
        WORK, ways=(Way(changes=(replace(WORK_CHANGE, allow=kept),)),),
    )


def test_allow_not_a_number_drops_way(path: Path) -> None:
    """An allowance that is not a number drops that way alone."""
    stored_change(path=path, change={'allow': 'x'})
    assert JsonStateRepository(path=path, city=SYDNEY).load().saved.pins == (
        sample().pins[0], replace(WORK, ways=()),
    )


def test_file_without_swap_knobs_leaves_the_knobs(path: Path) -> None:
    """A state.json from before the swap switch loads with it off."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    del data['settings']['swap_knobs']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.settings == Settings(
        device=DeviceSettings(rotate=True, swap_knobs=False),
    )


def test_round_trips_swap_knobs(path: Path) -> None:
    """The swap switch survives a save and a load."""
    repository = JsonStateRepository(path=path, city=SYDNEY)
    changed = Settings(device=DeviceSettings(swap_knobs=True))
    repository.save(saved=replace(sample(), settings=changed))
    assert repository.load().saved.settings == changed
