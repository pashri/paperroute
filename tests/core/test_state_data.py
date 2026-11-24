"""Tests for storing map data."""

from dataclasses import replace
from datetime import datetime
from typing import Final

import pytest

from paperroute_testkit.core.factories import START
from paperroute_transit.core import strip_state
from paperroute_transit.core.messages import (
    LoadPatterns,
    MapDataFailed,
    PatternsLoaded,
    PositionsFetched,
)
from paperroute_transit.core.positions import (
    ParkedAge,
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.state_data import on_map_event
from paperroute_transit.core.state_model import State
from tests.core.state.helpers import fresh


def vehicle(*, timestamp: int) -> VehiclePosition:
    """
    Build a position heard at a time.

    Parameters
    ----------
    timestamp : int
        When the vehicle was last heard, in epoch seconds.

    Returns
    -------
    VehiclePosition
        A position at the origin.
    """
    return VehiclePosition(
        trip_id=str(timestamp),
        route_id='r',
        label='x',
        point=(0.0, 0.0),
        bearing=None,
        timestamp=timestamp,
        start_date=None,
    )


SNAPSHOT: Final[PositionsSnapshot] = PositionsSnapshot(
    fetched_at=datetime(2026, 10, 16, tzinfo=START.tzinfo),
    header_time=1000,
    vehicles=tuple(
        vehicle(timestamp=stamp) for stamp in (1000, 400, 399, 0, 2000)
    ),
)
FETCHED: Final[PositionsFetched] = PositionsFetched(
    feed='f', snapshot=SNAPSHOT,
)


def parked(*, age: ParkedAge) -> State:
    """
    Build a started state with a parked-vehicle age.

    Parameters
    ----------
    age : ParkedAge
        The age to set.

    Returns
    -------
    State
        A fresh state with the age in its board settings.
    """
    state = fresh()
    board = replace(state.settings.board, parked_age=age)
    return replace(state, settings=replace(state.settings, board=board))


def test_on_map_event_keeps_every_vehicle_when_parked_age_is_off() -> None:
    """On map event keeps every vehicle when parked age is off."""
    state, _ = on_map_event(state=fresh(), event=FETCHED)
    assert state.map.positions['f'] is SNAPSHOT


def test_on_map_event_drops_parked_vehicles_when_set() -> None:
    """On map event drops parked vehicles when set."""
    state, _ = on_map_event(
        state=parked(age=ParkedAge.TEN), event=FETCHED,
    )
    stamps = [v.timestamp for v in state.map.positions['f'].vehicles]
    assert stamps == [1000, 400, 0, 2000]


def pending(*, trip_ids: frozenset[str]) -> State:
    """
    Build a state waiting on pattern loads.

    Parameters
    ----------
    trip_ids : frozenset[str]
        The trips still pending.

    Returns
    -------
    State
        A fresh state with those patterns pending.
    """
    state = fresh()
    return replace(
        state, map=replace(state.map, pending_patterns=trip_ids),
    )


def counted(*, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Record each strip request made while settling.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Patcher.

    Returns
    -------
    list[str]
        Gains an entry per `request_strips` call.
    """
    calls: list[str] = []
    real = strip_state.request_strips

    def counting(*, state: State):  # type: ignore[no-untyped-def]
        """Count, then call the real request."""
        calls.append('strips')
        return real(state=state)

    monkeypatch.setattr(
        'paperroute_transit.core.state_views.request_strips', counting,
    )
    return calls


def loaded(*, trip_ids: frozenset[str]) -> PatternsLoaded:
    """
    Build an answer holding no patterns.

    Parameters
    ----------
    trip_ids : frozenset[str]
        The trips answered.

    Returns
    -------
    PatternsLoaded
        The empty answer.
    """
    return PatternsLoaded(trip_ids=trip_ids, patterns={}, shapes={})


def test_reduce_patterns_loaded_waits_for_the_last_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strip steps wait while patterns are pending; others still run."""
    calls = counted(monkeypatch=monkeypatch)
    state = pending(trip_ids=frozenset({'a', 'b'}))
    state, _ = on_map_event(
        state=state, event=loaded(trip_ids=frozenset({'a'})),
    )
    assert not calls
    assert state.map.pending_patterns == frozenset({'b'})


def test_reduce_patterns_loaded_last_rebuilds_the_strip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last pattern loading rebuilds the strip once."""
    calls = counted(monkeypatch=monkeypatch)
    on_map_event(
        state=pending(trip_ids=frozenset({'a'})),
        event=loaded(trip_ids=frozenset({'a'})),
    )
    assert len(calls) == 1


def test_reduce_patterns_failed_last_rebuilds_the_strip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed last pattern load rebuilds the strip once."""
    calls = counted(monkeypatch=monkeypatch)
    state = pending(trip_ids=frozenset({'a'}))
    event = MapDataFailed(
        request=LoadPatterns(trip_ids=frozenset({'a'}), day=START.date()),
    )
    state, _ = on_map_event(state=state, event=event)
    assert len(calls) == 1
    assert not state.map.pending_patterns
