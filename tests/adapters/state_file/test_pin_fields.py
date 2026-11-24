"""Tests for state file: pin fields."""

import json
import logging
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from paperroute_device.core.device_settings import DeviceSettings
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.connection_factories import WORK, WORK_CHANGE
from paperroute_testkit.core.factories import pin_at
from paperroute_transit.core.auto_model import AutoState, Before, Occurrence
from paperroute_transit.core.model import AutoWindow, Way
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Quota,
    Settings,
)
from paperroute_transit.core.state_views import saved_of
from tests.adapters.board_states import BoardStates
from tests.adapters.state_file.helpers import (
    ROTATED,
    sample,
    settings_without,
    stored_with,
)
from tests.adapters.state_layout import flat_v11
from tests.core.test_auto_pins import is_shown, started, ticked, trusted

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
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=saved)
    assert repository.load().saved == saved
    stored = json.loads(path.read_text())['board']['pins'][0]['windows'][0]
    assert stored['days'] == ['mon', 'tue', 'wed', 'thu', 'fri']
    assert stored['start'] == '07:30'


def test_migrates_v7_to_v8(path: Path) -> None:
    """A version 7 file gains empty windows and no auto pin state."""
    saved = replace(sample(), unpinned=sample().pins[0])
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=saved)
    data = flat_v11(data=json.loads(path.read_text()))
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
    BoardStates(path=path, city=SYDNEY).save(
        saved=replace(sample(), settings=settings, quota=quota),
    )
    data = json.loads(path.read_text())
    data['board']['pins'][1]['windows'] = [
        GOOD_WINDOW,
        {'days': ['mon'], 'start': '07:00', 'end': '07:00'},
        {'days': ['funday'], 'start': '07:00', 'end': '08:00'},
    ]
    path.write_text(json.dumps(data))
    caplog.clear()
    saved = BoardStates(path=path, city=SYDNEY).load().saved
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
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    data = json.loads(path.read_text())
    data['board']['auto'] = {'occurrence': 3}
    path.write_text(json.dumps(data))
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert loaded.saved.auto == AutoState()
    assert loaded.saved.pins == sample().pins
    assert any(
        record.levelno == logging.WARNING for record in caplog.records
    )


def test_windows_not_a_list(path: Path) -> None:
    """Windows that are not a list leave the pin with none."""
    stored_with(path=path, pin_fields={'windows': 'x'})
    loaded = BoardStates(path=path, city=SYDNEY).load()
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
    repository = BoardStates(path=tmp_path / 's.json', city=SYDNEY)
    repository.save(saved=saved_of(state=state))
    now = datetime(2026, 4, 5, 2, 10, tzinfo=SYDNEY_ZONE)
    state = trusted(state=initial_state(
        saved=repository.load().saved, now=now,
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
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=saved)
    assert repository.load().saved == saved
    way = json.loads(path.read_text())['board']['pins'][0]['ways'][0]
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
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=saved)
    data = flat_v11(data=json.loads(path.read_text()))
    data['schema_version'] = 8
    del data['settings']['connection_map']
    for pin in (
        *data['pins'],
        data['unpinned'],
        data['auto']['before']['place'],
    ):
        del pin['ways']
        del pin['never']
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == saved
    assert all(not pin.ways for pin in loaded.saved.pins)
    assert loaded.saved.settings.board.connection_map is ConnectionMap.CHANGES
    repository.save(saved=loaded.saved)
    assert json.loads(path.read_text())['schema_version'] == 13


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
    BoardStates(path=path, city=SYDNEY).save(saved=saved)
    data: dict[str, Any] = json.loads(path.read_text())
    data['board']['pins'][1]['ways'][0]['changes'][0].update(change)
    path.write_text(json.dumps(data))


def test_bad_change_drops_way_alone(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A change that can't be read drops its way with one warning."""
    settings = ROTATED
    BoardStates(path=path, city=SYDNEY).save(
        saved=replace(
            sample(),
            pins=(sample().pins[0], WORK),
            settings=settings,
        ),
    )
    data = json.loads(path.read_text())
    data['board']['pins'][1]['ways'][0]['changes'][0] = {'alight': 3}
    path.write_text(json.dumps(data))
    caplog.clear()
    saved = BoardStates(path=path, city=SYDNEY).load().saved
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
    pin = BoardStates(path=path, city=SYDNEY).load().saved.pins[1]
    assert pin == replace(
        WORK, ways=(Way(changes=(replace(WORK_CHANGE, allow=kept),)),),
    )


def test_allow_not_a_number_drops_way(path: Path) -> None:
    """An allowance that is not a number drops that way alone."""
    stored_change(path=path, change={'allow': 'x'})
    assert BoardStates(path=path, city=SYDNEY).load().saved.pins == (
        sample().pins[0], replace(WORK, ways=()),
    )


def test_file_without_swap_knobs_leaves_the_knobs(path: Path) -> None:
    """A state.json from before the swap switch loads with it off."""
    assert settings_without(path=path, setting='swap_knobs') == Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS),
        device=DeviceSettings(rotate=True, swap_knobs=False),
    )


def test_round_trips_swap_knobs(path: Path) -> None:
    """The swap switch survives a save and a load."""
    repository = BoardStates(path=path, city=SYDNEY)
    changed = Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS),
        device=DeviceSettings(swap_knobs=True),
    )
    repository.save(saved=replace(sample(), settings=changed))
    assert repository.load().saved.settings == changed
