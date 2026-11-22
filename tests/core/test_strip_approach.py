"""Tests for buses drawn approaching a both-sides From."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    TRAIN,
    calls,
    inputs_of,
    line_pattern,
    live,
    pattern,
    place,
    snapshot,
    train,
    vehicle,
)
from paperroute_testkit.core.reach_factories import reach_of, tail
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.model import Departure
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    StripKey,
    build_reach,
)
from paperroute_transit.core.sides import KerbPair
from paperroute_transit.core.strip_approach import (
    approach_stations,
    facing_of,
    facing_station,
    stop_reached,
)
from paperroute_transit.core.strip_badges import HALF_WIDE, SLOT_SPAN
from paperroute_transit.core.strip_model import TRACK_X, StripInputs, StripRow
from paperroute_transit.core.strip_view import build_strip_view

BOTH: Final[StripKey] = replace(TRAIN, across='x2')
HERE: Final[tuple[str, ...]] = ('P5', 'P4', 'P3', 'P2', 'P1', 'C', 'B1', 'B2')
THERE: Final[tuple[str, ...]] = ('Q2', 'Q1', 'x2', 'A1', 'A2')
ALONG: Final[dict[str, Point]] = {
    station: (0.0, 1000.0 * (5 - index))
    for index, station in enumerate(HERE)
}


def test_approach_stations_are_the_stops_before_the_from() -> None:
    """Both kerbs' stops before the From are asked for."""
    data = ReachInput(
        origin=place('C'), tails=(tail('B1', 6, before='P2 P1'),),
        places={}, station_of={},
        across_tails=(tail('A1', 6, before='Q1'),),
    )
    assert approach_stations(data=data) == {'P2', 'P1', 'Q1'}


def test_facing_of_names_each_stops_partner() -> None:
    """Each pair gives the station across the road."""
    pair = KerbPair(station='P1', other=place('A1'), busy=False)
    assert facing_of(pairs={'P1': pair}) == {'P1': 'A1'}


def facing_reach(*, facing: dict[str, str]) -> Reach:
    """
    Build A3 to A1 above C, B1 to B3 below, the kerb across x2 mapped to C.

    Parameters
    ----------
    facing : dict[str, str]
        The station across the road from each stop before the From.

    Returns
    -------
    Reach
        The reach.
    """
    here = tail('B1 B2 B3', 6, before='P5 P4 P3 P2 P1')
    there = tail('A1 A2 A3', 6, before='Q2 Q1')
    places = {
        row.place.stop_id: row.place
        for row in reach_of(tails=[here, there]).rows
    }
    return build_reach(data=ReachInput(
        origin=places['C'], tails=(here,), places=places,
        station_of={'x2': 'C'}, across_tails=(there,), facing=facing,
    ))


def from_across(*, minutes: int, trip_id: str) -> Departure:
    """
    Make a departure from the kerb across.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00.
    trip_id : str
        Trip ID.

    Returns
    -------
    Departure
        The departure.
    """
    item = train(minutes=minutes, trip_id=trip_id)
    return replace(item, facts=replace(item.facts, across=True))


def approaching(
    *, minutes: int, facing: dict[str, str] | None = None,
    key: StripKey = BOTH,
) -> StripInputs:
    """
    Make a bus at P5 at 8:00, then every 3 minutes, leaving C at 8:15.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00 now.
    facing : dict[str, str] | None
        The station across the road from each stop before the From.
    key : StripKey
        The strip's key.

    Returns
    -------
    StripInputs
        The inputs.
    """
    return replace(inputs_of(
        reach=facing_reach(facing=facing or {}),
        departures=[live(item=train(minutes=15, trip_id='y'))],
        patterns={'y': line_pattern(trip_id='y', stations=HERE, minutes=0)},
        now=START + timedelta(minutes=minutes),
    ), key=key)


def heard_at(*, station: str, minutes: int) -> StripInputs:
    """
    Make the same bus heard at a station's point.

    Parameters
    ----------
    station : str
        Where it is heard.
    minutes : int
        Minutes after 8:00 now.

    Returns
    -------
    StripInputs
        The inputs.
    """
    now = START + timedelta(minutes=minutes)
    return replace(
        approaching(minutes=minutes),
        patterns={'y': pattern(
            trip_id='y', route='T1',
            stops=calls(stations=HERE, start=EIGHT, step=180, points=ALONG),
        )},
        positions={'trains': snapshot(fetched_at=now, vehicles=(vehicle(
            timestamp=int(now.timestamp()), trip_id='y',
            point=ALONG[station],
        ),))},
    )


def badges_of(
    *, inputs: StripInputs,
) -> tuple[tuple[Marker, ...], dict[str, StripRow]]:
    """
    Build the Strip and give its badges and its rows by station.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.

    Returns
    -------
    tuple[tuple[Marker, ...], dict[str, StripRow]]
        The badges, and the rows by station.
    """
    view = build_strip_view(inputs=inputs)
    return view.markers, {row.station: row for row in view.rows}


def test_stop_reached_counts_a_hop_run_to_its_end() -> None:
    """A hop run to its end has reached its next stop."""
    assert stop_reached(hop=(2, 0.0)) == 2
    assert stop_reached(hop=(2, 0.5)) == 2
    assert stop_reached(hop=(2, 1.0)) == 3


def test_facing_station_prefers_the_partner_then_the_count() -> None:
    """A partner on the lane wins; else the row as many stops away."""
    lane = ('A1', 'A2', 'A3')
    assert facing_station(
        lane=lane, facing={'P2': 'A1'}, station='P2', count=2,
    ) == 'A1'
    assert facing_station(
        lane=lane, facing={}, station='P2', count=2,
    ) == 'A2'
    assert facing_station(
        lane=lane, facing={'P2': 'Z9'}, station='P2', count=2,
    ) == 'A2'
    assert facing_station(
        lane=lane, facing={}, station='P4', count=4,
    ) is None


def test_approaching_bus_sits_on_the_row_facing_its_last_stop() -> None:
    """Past P2 at 8:10, the bus is on P2's partner's row, heading down."""
    (marker,), rows = badges_of(
        inputs=approaching(minutes=10, facing={'P2': 'A1'}),
    )
    assert (marker.x, marker.y, marker.bearing) == (
        TRACK_X, rows['A1'].y, 180.0,
    )


def test_approaching_bus_without_a_partner_counts_its_stops() -> None:
    """With no pairs, P2, two stops out, faces the second row up."""
    (marker,), rows = badges_of(inputs=approaching(minutes=10))
    assert (marker.y, marker.bearing) == (rows['A2'].y, 180.0)


def test_approaching_bus_at_a_stop_sits_facing_it() -> None:
    """At P1 at 8:12 the bus faces the first row up."""
    (marker,), rows = badges_of(inputs=approaching(minutes=12))
    assert marker.y == rows['A1'].y


def test_approaching_bus_with_neither_sits_between_rows() -> None:
    """P4 has no partner and no fourth row: midway between its neighbours."""
    (marker,), rows = badges_of(
        inputs=approaching(minutes=4, facing={'P5': 'A3', 'P3': 'A2'}),
    )
    assert marker.y == (rows['A2'].y + rows['A3'].y) / 2


def test_approaching_bus_beyond_the_rows_is_not_drawn() -> None:
    """At P5, five stops out with three rows, nothing is drawn."""
    markers, _ = badges_of(inputs=approaching(minutes=1))
    assert markers == ()


def test_single_kerb_strip_draws_no_approaching_bus() -> None:
    """A strip fed by one kerb places approaching buses as today."""
    markers, _ = badges_of(inputs=approaching(minutes=10, key=TRAIN))
    assert markers == ()


def test_bus_approaching_the_kerb_across_comes_up_from_below() -> None:
    """A bus one stop short of x2 faces the first row down, heading up."""
    inputs = replace(inputs_of(
        reach=facing_reach(facing={}),
        departures=[live(item=from_across(minutes=6, trip_id='x'))],
        patterns={'x': line_pattern(trip_id='x', stations=THERE, minutes=0)},
        now=START + timedelta(minutes=4),
    ), key=BOTH)
    (marker,), rows = badges_of(inputs=inputs)
    assert (marker.y, marker.bearing) == (rows['B1'].y, 0.0)


def test_heard_bus_at_a_stop_sits_facing_that_stop() -> None:
    """A live bus at P2's point faces the second row up."""
    (marker,), rows = badges_of(inputs=heard_at(station='P2', minutes=9))
    assert (marker.y, marker.live, marker.bearing) == (
        rows['A2'].y, True, 180.0,
    )


def test_heard_bus_at_the_from_sits_on_its_disc() -> None:
    """A live bus at the From's point is drawn on the From."""
    (marker,), rows = badges_of(inputs=heard_at(station='C', minutes=15))
    assert (marker.x, marker.y, marker.bearing) == (
        TRACK_X, rows['C'].y, 180.0,
    )


def test_approaching_bus_goes_beside_a_departed_one_on_its_row() -> None:
    """A bus just gone from x2 keeps its hop; the one at P1 goes beside."""
    inputs = approaching(minutes=12)
    inputs = replace(
        inputs,
        departures=(
            *inputs.departures,
            live(item=from_across(minutes=10, trip_id='x')),
        ),
        patterns={
            **inputs.patterns,
            'x': line_pattern(
                trip_id='x', stations=('x2', 'A1', 'A2'), minutes=10,
            ),
        },
    )
    markers, rows = badges_of(inputs=inputs)
    assert [(each.x, each.y, each.bearing) for each in markers] == [
        (TRACK_X, rows['A1'].y + 12, 0.0),
        (TRACK_X + SLOT_SPAN, rows['A1'].y, 180.0),
    ]
    assert rows['A1'].name_after == TRACK_X + SLOT_SPAN + HALF_WIDE
