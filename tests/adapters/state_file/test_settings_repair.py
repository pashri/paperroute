"""Tests for state file: settings repair."""

import json
import logging
from dataclasses import replace
from datetime import time
from pathlib import Path

import pytest

from paperroute_device.core.device_settings import DeviceSettings, Window
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.saved import (
    DEVICE_FIELDS,
    BoardSettings,
    Lookahead,
    Rate,
    Saved,
    Settings,
    default_saved,
)
from tests.adapters.board_states import BoardStates
from tests.adapters.state_file.helpers import (
    ROTATED,
    SCHEMA,
    sample,
    settings_without,
    stored_with,
)
from tests.adapters.state_layout import flat_v11


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
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    for name, value in changes.items():
        section = 'device' if name in DEVICE_FIELDS else 'board'
        data[section]['settings'][name] = value
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
                board=BoardSettings(
                    peak_windows=PEAK_WINDOWS,
                    offpeak_rate=Rate.STEADY,
                ),
            ),
        ),
        (
            {
                'my_hours': {'start': '7am', 'end': '05:30'},
                'larger_text': True,
            },
            Settings(
                board=BoardSettings(peak_windows=PEAK_WINDOWS),
                device=DeviceSettings(rotate=True, larger_text=True),
            ),
        ),
        (
            {'connection_map': 'first', 'larger_text': True},
            Settings(
                board=BoardSettings(peak_windows=PEAK_WINDOWS),
                device=DeviceSettings(rotate=True, larger_text=True),
            ),
        ),
        (
            {'clean_after': 45, 'larger_text': True},
            Settings(
                board=BoardSettings(peak_windows=PEAK_WINDOWS),
                device=DeviceSettings(rotate=True, larger_text=True),
            ),
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
    assert settings_without(path=path, setting='clean_after') == Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS),
        device=DeviceSettings(rotate=True, clean_after=30),
    )


def test_round_trips_clean_after(path: Path) -> None:
    """A cleaning choice survives a save and a load."""
    repository = BoardStates(path=path, city=SYDNEY)
    changed = Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS),
        device=DeviceSettings(clean_after=120),
    )
    repository.save(saved=replace(sample(), settings=changed))
    assert repository.load().saved.settings == changed


def test_migrates_to_pin_fields(path: Path) -> None:
    """A version 5 file gains an empty nickname and filter."""
    repository = BoardStates(path=path, city=SYDNEY)
    saved = replace(sample(), unpinned=sample().pins[0])
    repository.save(saved=saved)
    data = flat_v11(data=json.loads(path.read_text()))
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
    assert stored['schema_version'] == SCHEMA


def test_migrates_v6_to_v7_with_lookahead(path: Path) -> None:
    """A version 6 file keeps its settings and gains the lookahead."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = flat_v11(data=json.loads(path.read_text()))
    data['schema_version'] = 6
    del data['settings']['alert_lookahead']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.pins == sample().pins
    assert loaded.saved.settings.device.rotate is True
    assert loaded.saved.settings.board.alert_lookahead == Lookahead.WEEK
    repository.save(saved=loaded.saved)
    assert json.loads(path.read_text())['schema_version'] == SCHEMA


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
    settings = Settings(
        board=BoardSettings(
            peak_windows=PEAK_WINDOWS,
            alert_lookahead=Lookahead.ALL,
        ),
    )
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=settings))
    assert repository.load().saved.settings == settings


def test_round_trips_nickname_and_filter(path: Path) -> None:
    """A nickname and route filter survive a save and a load."""
    pin = Pin(
        origin=StopPlace(stop_id='a', name='A', point=None),
        nickname='Work',
        only=('606', '607X'),
    )
    saved = replace(default_saved(city=SYDNEY), pins=(pin,), unpinned=pin)
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=saved)
    assert repository.load().saved.pins == (pin,)


@pytest.mark.parametrize('bad', [5, ['x'], {}])
def test_bad_nickname_resets_only_itself(path: Path, bad: object) -> None:
    """An unreadable nickname becomes None; the pin keeps the rest."""
    stored_with(path=path, pin_fields={'nickname': bad, 'only': ['1']})
    pins = BoardStates(path=path, city=SYDNEY).load().saved.pins
    assert pins[1] == replace(sample().pins[1], only=('1',))
    assert pins[0] == sample().pins[0]


def test_long_nickname_is_trimmed(path: Path) -> None:
    """A nickname over the limit is cleaned and cut."""
    stored_with(path=path, pin_fields={'nickname': ' ' + 'a' * 40})
    pin = BoardStates(path=path, city=SYDNEY).load().saved.pins[1]
    assert pin.nickname == 'a' * 24


@pytest.mark.parametrize('bad', [5, 'ab', [1], None])
def test_bad_only_resets_only_itself(path: Path, bad: object) -> None:
    """An unreadable route filter becomes empty; the nickname stays."""
    stored_with(path=path, pin_fields={'nickname': 'Work', 'only': bad})
    pin = BoardStates(path=path, city=SYDNEY).load().saved.pins[1]
    assert pin.only == ()
    assert pin.nickname == 'Work'


def test_broken_pin_is_dropped_and_the_rest_kept(path: Path) -> None:
    """A pin that stays invalid is dropped on its own."""
    stored_with(path=path, pin_fields={'kind': 'spaceship'})
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert loaded.saved.pins == (sample().pins[0],)


def test_broken_unpinned_place_is_dropped(path: Path) -> None:
    """An unusable unpinned place goes; the pins stay."""
    data = stored_with(path=path, pin_fields={})
    data['board']['unpinned'] = {'origin': 5}
    path.write_text(json.dumps(data))
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.saved.pins == sample().pins


def test_v5_pin_with_fields_survives_migration(path: Path) -> None:
    """A version 5 pin that already has the new fields keeps them."""
    stored_with(
        path=path,
        pin_fields={
            'nickname': 'Work', 'only': ['606'], 'destination': None,
        },
    )
    data = flat_v11(data=json.loads(path.read_text()))
    data['schema_version'] = 5
    path.write_text(json.dumps(data))
    pin = BoardStates(path=path, city=SYDNEY).load().saved.pins[1]
    assert (pin.nickname, pin.only) == ('Work', ('606',))
