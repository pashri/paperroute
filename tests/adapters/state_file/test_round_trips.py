"""Tests for state file: round trips."""

import json
import logging
import os
import stat
import tempfile
from collections.abc import Iterator
from datetime import time
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.pin_choices import clean_nickname
from paperroute_transit.core.saved import (
    BoardSettings,
    LiveView,
    Rate,
    Saved,
    Settings,
    default_saved,
)
from tests.adapters.board_states import BoardStates
from tests.adapters.state_file.helpers import SCHEMA, sample
from tests.golden.test_golden_state import RICH_SAVED

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
            auto_rebuild=draw(st.booleans()),
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


@settings(max_examples=60, deadline=None)
@given(saved=saved_values())
def test_round_trip(saved: Saved) -> None:
    """What is saved is what loads."""
    with tempfile.TemporaryDirectory() as directory:
        repository = BoardStates(
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
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.saved == default_saved(city=SYDNEY)
    assert not loaded.stored
    assert not caplog.records


def test_truncated_file_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A cut-off file gives defaults and an error log."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    path.write_text(path.read_text()[:40])
    loaded = repository.load()
    assert loaded.saved == default_saved(city=SYDNEY)
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
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.saved == default_saved(city=SYDNEY)
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_wrong_field_type_gives_defaults(path: Path) -> None:
    """A field of the wrong type is rejected."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['board']['current'] = 'big'
    path.write_text(json.dumps(data))
    assert repository.load().saved == default_saved(city=SYDNEY)


def test_unusable_file_is_kept_aside(path: Path) -> None:
    """A newer file survives a load and a save under a ``.bad-`` name."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = SCHEMA + 1
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
    BoardStates(path=path, city=SYDNEY).load()
    kept = sorted(p.name for p in path.parent.glob('state.json.bad-*'))
    assert len(kept) == 2
    assert 'state.json.bad-100' in kept
    assert not path.exists()


def test_current_out_of_range_is_clamped(path: Path) -> None:
    """A current index past the last pin becomes the last pin."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['board']['current'] = 9
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.saved.current == 1
    assert loaded.stored


def test_no_pins_and_no_unpinned_gives_default_place(path: Path) -> None:
    """An empty pin list still shows a place."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['board']['pins'] = []
    path.write_text(json.dumps(data))
    loaded = repository.load().saved
    assert loaded.unpinned == default_saved(city=SYDNEY).unpinned
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
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield path
    directory.chmod(stat.S_IRWXU)


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores permissions')
def test_failed_write_keeps_old_file(locked: Path) -> None:
    """A write that can't happen leaves the old file and no temp file."""
    before = locked.read_text()
    repository = BoardStates(path=locked, city=SYDNEY)
    with pytest.raises(OSError):
        repository.save(saved=default_saved(city=SYDNEY))
    assert locked.read_text() == before
    assert [p.name for p in locked.parent.iterdir()] == ['state.json']


def test_state_file_round_trip_has_no_zoom(path: Path) -> None:
    """The map zoom is not saved, and a saved state loads back the same."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    assert 'zoom' not in json.loads(path.read_text())
    assert not hasattr(sample(), 'zoom')
    assert repository.load().saved == sample()


FILE_KEYS: Final[frozenset[str]] = frozenset({
    'schema_version', 'device', 'board',
    'settings', 'quiet_mode', 'my_hours', 'rotate', 'larger_text',
    'inverted_lists', 'clean_after', 'swap_knobs',
    'auto_rebuild', 'peak_rate', 'offpeak_rate', 'peak_windows',
    'alert_lookahead', 'connection_map', 'skip_school_buses',
    'skip_school_on_route_pins', 'parked_age', 'map_framing', 'stop_sides',
    'start', 'end',
    'pins', 'current', 'unpinned', 'view', 'quota', 'auto',
    'origin', 'destination', 'kind', 'routes', 'nickname', 'only',
    'opposite', 'towards',
    'windows', 'ways', 'never', 'stop_id', 'name', 'x', 'y', 'days',
    'changes', 'ticked', 'alight', 'allow',
    'day', 'calls', 'occurrence', 'before', 'overridden_at', 'ref',
    'place', 'pinned',
})


def key_names(*, data: object) -> set[str]:
    """
    Collect every key in decoded JSON, at any depth.

    Parameters
    ----------
    data : object
        Decoded JSON.

    Returns
    -------
    set[str]
        The keys of every object in it.
    """
    if isinstance(data, dict):
        return set(data).union(
            *(key_names(data=value) for value in data.values()),
        )
    if isinstance(data, list):
        return set().union(*(key_names(data=item) for item in data))
    return set()


def test_file_holds_no_secret_keys(path: Path) -> None:
    """Only the documented keys are written, anywhere in the kit's file."""
    BoardStates(path=path, city=SYDNEY).save(saved=RICH_SAVED)
    assert key_names(data=json.loads(path.read_text())) == FILE_KEYS
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    assert set(data) == {'schema_version', 'device', 'board'}
    assert set(data['device']) == {'schema_version', 'settings'}
    assert set(data['board']) == {
        'schema_version',
        'pins',
        'current',
        'unpinned',
        'view',
        'settings',
        'quota',
        'auto',
    }
    assert set(data['board']['pins'][0]) == {
        'origin',
        'destination',
        'kind',
        'routes',
        'nickname',
        'only',
        'windows',
        'ways',
        'never',
        'opposite',
        'towards',
    }


def test_written_json_shape(path: Path) -> None:
    """The version is current and stops carry their metre coordinates."""
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    assert data['schema_version'] == SCHEMA
    assert data['board']['settings']['auto_rebuild'] is True
    assert 'auto_rebuild' not in data['device']['settings']
    assert data['board']['pins'][0]['origin'] == {
        'stop_id': 'a',
        'name': 'A',
        'x': 1.5,
        'y': -2.5,
    }
    assert data['board']['pins'][0]['destination']['x'] is None


def test_load_removes_stale_temp_files(path: Path) -> None:
    """A temp file left by a power cut is deleted on load."""
    stale = path.parent / '.state.json.abc123'
    stale.write_text('half')
    other = path.parent / 'keep.txt'
    other.write_text('x')
    BoardStates(path=path, city=SYDNEY).load()
    assert not stale.exists()
    assert other.exists()


def test_failed_rename_removes_temp_file(path: Path) -> None:
    """When the rename fails, the temp file is cleaned up."""
    path.mkdir()
    (path / 'inner').write_text('x')
    with pytest.raises(OSError):
        BoardStates(path=path, city=SYDNEY).save(saved=sample())
    assert [p.name for p in path.parent.iterdir()] == ['state.json']


def test_written_file_is_world_readable(path: Path) -> None:
    """The file holds no secrets and is mode 0644."""
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_directory_at_path_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Something that isn't a file at the path gives defaults and a log."""
    path.mkdir()
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.saved == default_saved(city=SYDNEY)
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_deeply_nested_json_gives_defaults(path: Path) -> None:
    """JSON nested past the recursion limit gives defaults."""
    path.write_text('[' * 100000)
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.saved == default_saved(city=SYDNEY)
    assert not loaded.stored
