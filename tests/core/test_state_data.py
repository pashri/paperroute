"""Tests for storing map data."""

from dataclasses import replace
from datetime import datetime
from typing import Final

from paperroute_testkit.core.factories import START
from paperroute_transit.core.messages import PositionsFetched
from paperroute_transit.core.positions import (
    ParkedAge,
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.state_data import on_map_event
from paperroute_transit.core.state_model import State
from tests.core.test_state import fresh


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
