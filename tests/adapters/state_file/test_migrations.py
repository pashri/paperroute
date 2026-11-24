"""Tests for state file: migrations."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY, SydneyMode
from paperroute_transit.adapters.state_migrations import regional_to_coach
from paperroute_transit.core.saved import BoardSettings, Quota, Settings
from tests.adapters.board_states import BoardStates
from tests.adapters.state_file.helpers import ROTATED, SCHEMA, sample
from tests.adapters.state_layout import flat_v11
from tests.helpers import changed_settings


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
    loaded = BoardStates(path=path, city=SYDNEY).load()
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
    loaded = BoardStates(path=path, city=SYDNEY).load()
    assert loaded.stored
    assert loaded.saved.unpinned is None
    assert loaded.saved.pins[0].kind == SydneyMode.COACH


def test_regional_to_coach_leaves_a_malformed_pin_list_alone() -> None:
    """Data the migration cannot read is passed on for validation to fail."""
    data: dict[str, object] = {'pins': 'oops', 'unpinned': 3}
    assert regional_to_coach(data, city=SYDNEY) == data


def test_migrates_v2_with_auto_rebuild_on(path: Path) -> None:
    """A version 2 file loads with the rebuild on and saves as current."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = flat_v11(data=json.loads(path.read_text()))
    del data['settings']
    data['schema_version'] = 2
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == sample()
    assert loaded.saved.settings.board.auto_rebuild is True
    repository.save(saved=loaded.saved)
    assert json.loads(path.read_text())['schema_version'] == SCHEMA


def test_round_trips_settings(path: Path) -> None:
    """A switched-off rebuild survives a save and a load."""
    repository = BoardStates(path=path, city=SYDNEY)
    manual = Settings(
        board=BoardSettings(auto_rebuild=False, peak_windows=PEAK_WINDOWS),
    )
    saved = replace(sample(), settings=manual)
    repository.save(saved=saved)
    assert repository.load().saved.settings.board.auto_rebuild is False


def test_migrates_v3_to_v4_with_empty_quota(path: Path) -> None:
    """A version 3 file loads with no calls counted and saves as current."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = flat_v11(data=json.loads(path.read_text()))
    del data['quota']
    data['schema_version'] = 3
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == sample()
    assert loaded.saved.quota == Quota(day=None, calls=0)
    repository.save(saved=loaded.saved)
    stored = json.loads(path.read_text())
    assert stored['schema_version'] == SCHEMA
    assert stored['board']['quota'] == {'day': None, 'calls': 0}


def test_round_trips_quota(path: Path) -> None:
    """The day and the calls counted survive a save and a load."""
    repository = BoardStates(path=path, city=SYDNEY)
    quota = Quota(day=date(2026, 10, 21), calls=1234)
    repository.save(saved=replace(sample(), quota=quota))
    assert json.loads(path.read_text())['board']['quota'] == {
        'day': '2026-10-21',
        'calls': 1234,
    }
    assert repository.load().saved.quota == quota


def test_negative_calls_reset_the_quota_only(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A count below zero resets the quota and keeps the pins."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['board']['quota']['calls'] = -1
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved == sample()
    assert loaded.saved.quota == Quota()
    assert caplog.records


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
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    data = flat_v11(data=json.loads(path.read_text()))
    data['schema_version'] = 4
    data['settings'] = {'auto_rebuild': auto_rebuild}
    path.write_text(json.dumps(data))


def test_migrates_v4_to_v5_with_defaults(path: Path) -> None:
    """A version 4 file keeps its rebuild setting and saves as current."""
    write_v4(path=path, auto_rebuild=False)
    repository = BoardStates(path=path, city=SYDNEY)
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.settings == Settings(
        board=BoardSettings(auto_rebuild=False, peak_windows=PEAK_WINDOWS),
    )
    assert loaded.saved.pins == sample().pins
    repository.save(saved=loaded.saved)
    stored = json.loads(path.read_text())
    assert stored['schema_version'] == SCHEMA
    assert stored['board']['settings']['peak_windows'] == [
        {'start': '06:30', 'end': '10:00'},
        {'start': '15:00', 'end': '19:00'},
    ]


def test_round_trips_all_settings(path: Path) -> None:
    """Every setting off its default survives a save and a load."""
    changed = changed_settings()
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=changed))
    assert repository.load().saved.settings == changed


def test_bad_time_falls_back(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A time that can't be read resets that setting and keeps the pins."""
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    data['board']['settings']['peak_windows'][0]['start'] = '25:00'
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    assert loaded.saved.pins == sample().pins
    assert loaded.saved.settings == ROTATED
    assert caplog.records
