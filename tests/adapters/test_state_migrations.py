"""Tests for the steps from state.json schema 9 to 11."""

import json
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from typing import Any, Final

import pytest

from paperroute.adapters.state_file import (
    JsonStateRepository,
    from_model,
    state_model_of,
)
from paperroute.adapters.state_migrations import (
    SCHEMA_VERSION,
    add_ways,
    drop_zoom,
    v9_ref,
)
from paperroute.core.model import (
    SYDNEY,
    AutoWindow,
    Change,
    Pin,
    RouteKind,
    StopPlace,
    Way,
    pin_ref,
)
from paperroute.core.saved import DEFAULT_SAVED, ConnectionMap, Saved
from paperroute_testkit.core.connection_factories import (
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    PARRAMATTA,
    TWO_WAY,
    WESTMEAD,
    WORK,
)

WINDOW: Final[dict[str, object]] = {
    'days': ['mon', 'tue'], 'start': '07:00', 'end': '08:30',
}
START: Final[datetime] = datetime(2026, 10, 27, 7, tzinfo=SYDNEY)


def stop(stop_id: str, name: str) -> dict[str, object]:
    """
    Give a stored stop.

    Parameters
    ----------
    stop_id : str
        Stop ID.
    name : str
        Name.

    Returns
    -------
    dict[str, object]
        The stop with no point.
    """
    return {'stop_id': stop_id, 'name': name, 'x': None, 'y': None}


def v9_pin(
    *,
    origin: dict[str, object],
    destination: dict[str, object] | None,
    kind: str | None,
    routes: list[str],
    change: dict[str, object] | None = None,
) -> dict[str, object]:
    """
    Give a pin as schema 9 stored it.

    Parameters
    ----------
    origin : dict[str, object]
        The From.
    destination : dict[str, object] | None
        The To.
    kind : str | None
        The kind.
    routes : list[str]
        The routes.
    change : dict[str, object] | None
        The change.

    Returns
    -------
    dict[str, object]
        The stored pin, with one window.
    """
    return {
        'origin': origin,
        'destination': destination,
        'kind': kind,
        'routes': routes,
        'nickname': None,
        'only': [],
        'windows': [WINDOW],
        'change': change,
    }


CAMPBELLTOWN_CHANGE: Final[dict[str, object]] = {
    'alight': stop('2560', 'Campbelltown Station'),
    'board': stop('2560', 'Campbelltown Station'),
    'allow': 5,
    'only': ['T8'],
}
PIN_1: Final[dict[str, object]] = v9_pin(
    origin=stop('a1', 'Home'), destination=stop('b1', 'Shops'),
    kind='bus', routes=['711'],
)
PIN_2: Final[dict[str, object]] = v9_pin(
    origin=stop('t2', 'Seven Hills'), destination=None,
    kind='train', routes=['T1'],
)
PIN_3: Final[dict[str, object]] = v9_pin(
    origin=stop('a3', 'Hollis'), destination=stop('b3', 'Westmead'),
    kind='bus', routes=['606'],
)
PIN_4: Final[dict[str, object]] = v9_pin(
    origin=stop('t4', 'Parramatta'), destination=None,
    kind='train', routes=['T1'],
)
PIN_5: Final[dict[str, object]] = v9_pin(
    origin=stop('a5', 'Narellan'), destination=stop('b5', 'Central'),
    kind='bus', routes=['894X'], change=CAMPBELLTOWN_CHANGE,
)


def v9_file(*, path: Path, **fields: object) -> dict[str, Any]:
    """
    Write a schema 9 file holding the five real pins.

    Parameters
    ----------
    path : Path
        The state file.
    **fields : object
        Top-level fields to overwrite.

    Returns
    -------
    dict[str, Any]
        The data written.
    """
    JsonStateRepository(path=path).save(saved=DEFAULT_SAVED)
    data: dict[str, Any] = json.loads(path.read_text())
    data.update(
        schema_version=9,
        pins=[PIN_1, PIN_2, PIN_3, PIN_4, PIN_5],
        current=0,
        unpinned=None,
    )
    data['settings']['connection_map'] = 'change'
    data.update(fields)
    path.write_text(json.dumps(data))
    return data


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


def test_migrates_v9_to_v10(path: Path) -> None:
    """Destination pins lose routes; pin 5's change becomes one way."""
    v9_file(path=path)
    pins = JsonStateRepository(path=path).load().saved.pins
    assert [pin.routes for pin in pins] == [(), ('T1',), (), ('T1',), ()]
    campbelltown = StopPlace(
        stop_id='2560', name='Campbelltown Station', point=None,
    )
    assert pins[4].ways == (
        Way(
            changes=(
                Change(alight=campbelltown, board=campbelltown, allow=5),
            ),
            ticked=True,
        ),
    )
    assert all(pin.only == () and pin.never == () for pin in pins)
    assert pins[1].kind is RouteKind.TRAIN


def test_migration_keeps_auto_refs(path: Path) -> None:
    """Occurrences keyed by v9 refs follow pins 1 and 5 to new refs."""
    occurrence = {
        'ref': v9_ref(pin=PIN_5),
        'start': START.isoformat(),
        'end': START.replace(hour=9).isoformat(),
    }
    v9_file(
        path=path,
        auto={'occurrence': occurrence, 'before': None, 'overridden_at': None},
    )
    saved = JsonStateRepository(path=path).load().saved
    assert saved.auto.occurrence is not None
    assert saved.auto.occurrence.ref == pin_ref(pin=saved.pins[4])
    assert saved.auto.occurrence.ref != occurrence['ref']
    window = AutoWindow(frozenset({0, 1}), time(7), time(8, 30))
    assert all(saved.pins[index].windows == (window,) for index in (0, 2, 4))
    occurrence['ref'] = v9_ref(pin=PIN_1)
    v9_file(
        path=path,
        auto={'occurrence': occurrence, 'before': None, 'overridden_at': None},
    )
    saved = JsonStateRepository(path=path).load().saved
    assert saved.auto.occurrence is not None
    assert saved.auto.occurrence.ref == pin_ref(pin=saved.pins[0])


def test_unknown_ref_kept() -> None:
    """A ref matching no v9 pin is left as it is."""
    occurrence = {'ref': 'abcdefabcdef', 'start': 'x', 'end': 'y'}
    data = add_ways(
        {
            'pins': [PIN_1],
            'unpinned': None,
            'auto': {'occurrence': occurrence},
            'settings': {},
        },
    )
    assert data['auto'] == {'occurrence': occurrence}


def test_migrates_unpinned_and_before(path: Path) -> None:
    """The unpinned place and the auto before place become one-way pins."""
    before = {'place': PIN_5, 'pinned': False}
    v9_file(
        path=path,
        unpinned=PIN_5,
        auto={'occurrence': None, 'before': before, 'overridden_at': None},
    )
    saved = JsonStateRepository(path=path).load().saved
    assert saved.unpinned is not None
    assert saved.auto.before is not None
    assert saved.unpinned == saved.pins[4]
    assert saved.auto.before.place == saved.pins[4]
    assert len(saved.unpinned.ways) == 1


def test_connection_map_change_becomes_changes(path: Path) -> None:
    """The v9 'change' map setting becomes 'changes'."""
    v9_file(path=path)
    settings = JsonStateRepository(path=path).load().saved.settings
    assert settings.board.connection_map is ConnectionMap.CHANGES


def test_connection_map_whole_stays() -> None:
    """The v9 'whole' map setting is kept."""
    data = add_ways({'settings': {'connection_map': 'whole'}})
    assert data['settings'] == {'connection_map': 'whole'}


def saved_ways(*, path: Path, pin: Pin) -> dict[str, Any]:
    """
    Save one pin and read the file back.

    Parameters
    ----------
    path : Path
        The state file.
    pin : Pin
        The pin.

    Returns
    -------
    dict[str, Any]
        The stored data.
    """
    saved = replace(DEFAULT_SAVED, pins=(pin,), current=0)
    JsonStateRepository(path=path).save(saved=saved)
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def test_bad_way_drops_way_alone(path: Path) -> None:
    """An unreadable way is dropped; the pin keeps its other ways."""
    pin = replace(WORK, ways=(*WORK.ways, TWO_WAY))
    data = saved_ways(path=path, pin=pin)
    data['pins'][0]['ways'][1]['changes'][0]['alight'] = 3
    path.write_text(json.dumps(data))
    assert JsonStateRepository(path=path).load().saved.pins == (WORK,)


@pytest.mark.parametrize('changes', [[], 'x', [{}] * 3])
def test_way_with_bad_changes_dropped(
    path: Path, changes: object,
) -> None:
    """A way with no changes or too many becomes a plain pin's way."""
    data = saved_ways(path=path, pin=WORK)
    data['pins'][0]['ways'][0]['changes'] = changes
    path.write_text(json.dumps(data))
    pins = JsonStateRepository(path=path).load().saved.pins
    assert pins == (replace(WORK, ways=()),)


def test_ways_not_a_list_gives_plain_pin(path: Path) -> None:
    """Ways that are not a list leave a plain pin with its To."""
    data = saved_ways(path=path, pin=WORK)
    data['pins'][0]['ways'] = 'x'
    path.write_text(json.dumps(data))
    pins = JsonStateRepository(path=path).load().saved.pins
    assert pins == (replace(WORK, ways=()),)


def test_allow_clamped_in_way(path: Path) -> None:
    """An allowance outside 1-60 is clamped within its way."""
    data = saved_ways(path=path, pin=WORK)
    data['pins'][0]['ways'][0]['changes'][0]['allow'] = 90
    path.write_text(json.dumps(data))
    pin = JsonStateRepository(path=path).load().saved.pins[0]
    assert pin.ways[0].changes[0].allow == 60


@pytest.mark.parametrize('bad', [5, 'ab', [1], None])
def test_bad_never_resets_to_empty(path: Path, bad: object) -> None:
    """A never list that is not a list of names becomes empty."""
    data = saved_ways(path=path, pin=replace(WORK, never=('665',)))
    data['pins'][0]['never'] = bad
    path.write_text(json.dumps(data))
    assert JsonStateRepository(path=path).load().saved.pins == (WORK,)


def test_round_trip_two_changes(path: Path) -> None:
    """A pin with an unticked two-change way and a never list round-trips."""
    pin = replace(
        WORK,
        destination=PARRAMATTA,
        ways=(*WORK.ways, replace(TWO_WAY, ticked=False)),
        never=('665', '660'),
    )
    data = saved_ways(path=path, pin=pin)
    assert 'change' not in data['pins'][0]
    assert data['pins'][0]['ways'][1]['ticked'] is False
    assert JsonStateRepository(path=path).load().saved.pins == (pin,)
    assert (HAMMERS, CONSTITUTION, WESTMEAD, HOLLIS) == (
        pin.ways[1].changes[0].alight,
        pin.ways[1].changes[0].board,
        pin.ways[1].changes[1].board,
        pin.origin,
    )


def test_saved_pin_holds_no_change_field(path: Path) -> None:
    """A saved plain pin stores no ways and an empty never list."""
    saved: Saved = replace(DEFAULT_SAVED)
    JsonStateRepository(path=path).save(saved=saved)
    data = json.loads(path.read_text())
    assert data['schema_version'] == SCHEMA_VERSION
    assert data['unpinned']['ways'] == []
    assert data['unpinned']['never'] == []


def test_pins_sharing_identity_merged() -> None:
    """
    Pins that come to share a ref merge into the first, windows kept.

    Pins that already shared one are left alone; the shown pin follows.
    """
    other = {**WINDOW, 'start': '17:00'}
    twin = {
        **PIN_3, 'routes': ['660'], 'windows': [other],
        'change': CAMPBELLTOWN_CHANGE,
    }
    data = add_ways(
        {'pins': [PIN_3, twin, PIN_1, PIN_1], 'current': 2, 'settings': {}},
    )
    pins = data['pins']
    assert isinstance(pins, list)
    assert len(pins) == 3
    assert data['current'] == 1
    assert pins[0]['windows'] == [WINDOW, other]
    assert len(pins[0]['ways']) == 1
    assert pins[1]['origin'] == PIN_1['origin']


def v10_pin(
    *,
    origin: dict[str, object],
    destination: dict[str, object] | None,
    kind: str | None,
    ways: list[object] | None = None,
) -> dict[str, object]:
    """
    Give a pin as schema 10 stored it.

    Parameters
    ----------
    origin : dict[str, object]
        The From.
    destination : dict[str, object] | None
        The To.
    kind : str | None
        The kind.
    ways : list[object] | None
        The ways; none when not given.

    Returns
    -------
    dict[str, object]
        The stored pin.
    """
    return {
        'origin': origin,
        'destination': destination,
        'kind': kind,
        'routes': ['T1'] if destination is None else [],
        'nickname': None,
        'only': [],
        'windows': [],
        'ways': ways or [],
        'never': [],
    }


def v10_data() -> dict[str, object]:
    """
    Give a schema 10 file shaped like a real one saved by the spike.

    Returns
    -------
    dict[str, object]
        Six pins, the last a connection, at zoom 4 on the Stop view.
    """
    parramatta = stop('215020', 'Parramatta Station')
    change = {'alight': parramatta, 'board': parramatta, 'allow': 2}
    return {
        'schema_version': 10,
        'pins': [
            v10_pin(
                origin=stop('G2145618', 'Constitution Hill Library'),
                destination=stop('214520', 'Wentworthville Station'),
                kind='bus',
            ),
            v10_pin(origin=parramatta, destination=None, kind='train'),
            v10_pin(
                origin=stop('G2145615', 'Hollis St'),
                destination=parramatta,
                kind='bus',
            ),
            v10_pin(
                origin=stop('201510', 'Redfern Station'),
                destination=None,
                kind='train',
            ),
            v10_pin(
                origin=stop('G257060', 'Old Hume Hwy'),
                destination=stop('200070', 'Town Hall Station'),
                kind='bus',
            ),
            v10_pin(
                origin=stop('G2145615', 'Hollis St'),
                destination=stop('200070', 'Town Hall Station'),
                kind=None,
                ways=[{'changes': [change], 'ticked': True}],
            ),
        ],
        'current': 5,
        'unpinned': None,
        'view': 'stop',
        'zoom': 4,
        'settings': {
            'auto_rebuild': True,
            'peak_rate': '30s',
            'offpeak_rate': '2min',
            'quiet_mode': 'my_hours',
            'my_hours': {'start': '23:00', 'end': '05:30'},
            'connection_map': 'first_leg',
        },
        'quota': {'day': '2026-10-29', 'calls': 859},
        'auto': {'occurrence': None, 'before': None, 'overridden_at': None},
    }


def test_v10_zoom_is_dropped() -> None:
    """A schema 10 file with a zoom loads at 11 without it."""
    model = state_model_of(text=json.dumps(v10_data()))
    assert model.schema_version == SCHEMA_VERSION == 11
    assert 'zoom' not in model.model_dump()
    saved = from_model(model=model)
    assert len(saved.pins) == 6
    assert saved.current == 5
    assert len(saved.pins[5].ways) == 1


def test_drop_zoom_without_zoom() -> None:
    """Data with no zoom passes through unchanged."""
    data: dict[str, object] = {'view': 'stop'}
    assert drop_zoom(data) == {'view': 'stop'}
    assert drop_zoom({'zoom': 4, 'view': 'map'}) == {'view': 'map'}
