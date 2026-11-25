"""Tests for buses drawn approaching a both-sides From."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

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
from paperroute_transit.core.model import CarryOn, Departure
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RowKind,
    RunTail,
    StripKey,
    build_reach,
)
from paperroute_transit.core.sides import KerbPair
from paperroute_transit.core.strip_approach import (
    approach_of,
    approach_stations,
    facing_of,
    facing_spot,
    facing_station,
    stop_reached,
)
from paperroute_transit.core.strip_badges import HALF_WIDE, SLOT_SPAN, Spot
from paperroute_transit.core.strip_model import TRACK_X, StripInputs, StripRow
from paperroute_transit.core.strip_view import build_strip_view, trips_of

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


def facing_reach(
    *, facing: dict[str, str], branches: Sequence[RunTail] = (),
) -> Reach:
    """
    Build A3 to A1 above C, B1 to B3 below, the kerb across x2 mapped to C.

    Parameters
    ----------
    facing : dict[str, str]
        The station across the road from each stop before the From.
    branches : Sequence[RunTail]
        More ways through the kerb across, laid out above the From.

    Returns
    -------
    Reach
        The reach.
    """
    here = tail('B1 B2 B3', 6, before='P5 P4 P3 P2 P1')
    there = (tail('A1 A2 A3', 6, before='Q2 Q1'), *branches)
    places = {
        row.place.stop_id: row.place
        for row in reach_of(tails=[here, *there]).rows
    }
    return build_reach(data=ReachInput(
        origin=places['C'], tails=(here,), places=places,
        station_of={'x2': 'C'}, across_tails=there, facing=facing,
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
    *,
    minutes: int,
    facing: dict[str, str] | None = None,
    key: StripKey = BOTH,
    stations: Sequence[str] = HERE,
    branches: Sequence[RunTail] = (),
) -> StripInputs:
    """
    Make a bus calling every 3 minutes from 8:00, at P5 to C by default.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00 now.
    facing : dict[str, str] | None
        The station across the road from each stop before the From.
    key : StripKey
        The strip's key.
    stations : Sequence[str]
        The bus's stops, C among them; it leaves C at its call there.
    branches : Sequence[RunTail]
        More ways through the kerb across, laid out above the From.

    Returns
    -------
    StripInputs
        The inputs.
    """
    return replace(inputs_of(
        reach=facing_reach(facing=facing or {}, branches=branches),
        departures=[live(item=train(
            minutes=3 * list(stations).index('C'), trip_id='y',
        ))],
        patterns={
            'y': line_pattern(trip_id='y', stations=stations, minutes=0),
        },
        now=START + timedelta(minutes=minutes),
    ), key=key)


def heard_at(*, point: Point, minutes: int) -> StripInputs:
    """
    Make the same bus heard at a point.

    Parameters
    ----------
    point : Point
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
            point=point,
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


def test_stop_reached_counts_a_hop_nearly_run_as_reached() -> None:
    """From `REACHED` of a hop on, the bus is at its next stop."""
    assert stop_reached(hop=(2, 0.94)) == 2
    assert stop_reached(hop=(2, 0.95)) == 3
    assert stop_reached(hop=(2, 0.9999999999999999)) == 3


def test_facing_station_prefers_the_partner_then_the_count() -> None:
    """A partner on the lane wins; else the row as many stops away."""
    lane = ('A1', 'A2', 'A3')
    assert facing_station(
        lane=lane, side=(), facing={'P2': 'A1'}, station='P2', count=2,
    ) == 'A1'
    assert facing_station(
        lane=lane, side=(), facing={}, station='P2', count=2,
    ) == 'A2'
    assert facing_station(
        lane=lane, side=(), facing={'P2': 'Z9'}, station='P2', count=2,
    ) == 'A2'
    assert facing_station(
        lane=lane, side=(), facing={}, station='P4', count=4,
    ) is None


def test_approaching_bus_sits_on_the_row_facing_its_last_stop() -> None:
    """At P2 at 8:09, the bus is on P2's partner's row, heading down."""
    (marker,), rows = badges_of(
        inputs=approaching(minutes=9, facing={'P2': 'A1'}),
    )
    assert (marker.x, marker.y, marker.bearing) == (
        TRACK_X, rows['A1'].y, 180.0,
    )


def test_approaching_bus_without_a_partner_counts_its_stops() -> None:
    """With no pairs, P2, two stops out, faces the second row up."""
    (marker,), rows = badges_of(inputs=approaching(minutes=9))
    assert (marker.y, marker.bearing) == (rows['A2'].y, 180.0)


def test_approaching_bus_at_a_stop_sits_facing_it() -> None:
    """At P1 at 8:12 the bus faces the first row up."""
    (marker,), rows = badges_of(inputs=approaching(minutes=12))
    assert marker.y == rows['A1'].y


def test_approaching_bus_with_neither_sits_between_rows() -> None:
    """P4 has no row: a third of P4 to P3 is 2/3 of the way P5 to P3."""
    (marker,), rows = badges_of(
        inputs=approaching(minutes=4, facing={'P5': 'A3', 'P3': 'A2'}),
    )
    assert marker.y == pytest.approx(
        rows['A3'].y + (rows['A2'].y - rows['A3'].y) * 2 / 3,
    )


def test_approaching_bus_beyond_the_rows_is_not_drawn() -> None:
    """At P5, five stops out with three rows, nothing is drawn."""
    markers, _ = badges_of(inputs=approaching(minutes=1))
    assert not markers


def test_single_kerb_strip_draws_no_approaching_bus() -> None:
    """A strip fed by one kerb places approaching buses as today."""
    markers, _ = badges_of(inputs=approaching(minutes=10, key=TRAIN))
    assert not markers


def test_bus_approaching_the_kerb_across_comes_up_from_below() -> None:
    """A third of the way from Q1 to x2, it rises from B1 towards C."""
    inputs = replace(inputs_of(
        reach=facing_reach(facing={}),
        departures=[live(item=from_across(minutes=6, trip_id='x'))],
        patterns={'x': line_pattern(trip_id='x', stations=THERE, minutes=0)},
        now=START + timedelta(minutes=4),
    ), key=BOTH)
    (marker,), rows = badges_of(inputs=inputs)
    assert marker.bearing == 0.0
    assert marker.y == pytest.approx(
        rows['B1'].y + (rows['C'].y - rows['B1'].y) / 3,
    )


def test_heard_bus_at_a_stop_sits_facing_that_stop() -> None:
    """A live bus at P2's point faces the second row up."""
    (marker,), rows = badges_of(inputs=heard_at(point=ALONG['P2'], minutes=9))
    assert (marker.y, marker.live, marker.bearing) == (
        rows['A2'].y, True, 180.0,
    )


def test_heard_bus_at_the_from_sits_on_its_disc() -> None:
    """A live bus at the From's point is drawn on the From."""
    (marker,), rows = badges_of(inputs=heard_at(point=ALONG['C'], minutes=15))
    assert (marker.x, marker.y, marker.bearing) == (
        TRACK_X, rows['C'].y, 180.0,
    )


def test_heard_bus_a_hair_short_of_the_from_sits_on_its_disc() -> None:
    """A live bus at 0.9999999999999999 of the hop into C is at C."""
    inputs = heard_at(point=(0.0, 1e-13), minutes=15)
    (marker,), rows = badges_of(inputs=inputs)
    assert (marker.x, marker.y, marker.bearing) == (
        TRACK_X, rows['C'].y, 180.0,
    )


def test_bus_run_past_the_last_row_below_draws_no_approach() -> None:
    """Between B3 and D1, past every row, nothing is drawn."""
    markers, _ = badges_of(inputs=approaching(
        minutes=13, stations=('P1', 'C', 'B1', 'B2', 'B3', 'D1', 'D2'),
    ))
    assert not markers


def test_bus_at_the_from_bound_off_the_rows_is_not_approaching() -> None:
    """At C, its next stop D9 not a row, the bus is not drawn facing."""
    markers, _ = badges_of(inputs=approaching(
        minutes=6, stations=('P2', 'P1', 'C', 'D9'),
    ))
    assert not markers


def test_reached_branch_row_above_the_from_takes_that_row() -> None:
    """At Z1, a branch row next to C, the bus sits on Z1."""
    (marker,), rows = badges_of(inputs=approaching(
        minutes=3, stations=('P3', 'Z1', 'P1', 'C', 'B1'),
        branches=(tail('Z1 Z2', 2),),
    ))
    assert rows['Z1'].kind is RowKind.BRANCH
    assert (marker.y, marker.bearing) == (rows['Z1'].y, 180.0)


def test_reached_main_row_beats_its_partner() -> None:
    """At A2, partnered with A1, the bus sits on A2."""
    (marker,), rows = badges_of(inputs=approaching(
        minutes=3, stations=('P3', 'A2', 'P1', 'C', 'B1'),
        facing={'A2': 'A1'},
    ))
    assert marker.y == rows['A2'].y


def test_reached_row_not_drawn_gives_way_to_its_partners_row() -> None:
    """At A2, scrolled off, the bus sits on A1, its partner's row."""
    inputs = approaching(
        minutes=3, stations=('P3', 'A2', 'P1', 'C', 'B1'),
        facing={'A2': 'A1'},
    )
    approach = approach_of(reach=inputs.reach, key=inputs.key)
    assert approach is not None
    departure = inputs.departures[0].departure
    built = inputs.patterns['y']
    assert built is not None
    spot = Spot(x=TRACK_X, y=50.0)
    assert facing_spot(
        departure=departure, pattern=built, hop=(1, 0.0),
        trips=trips_of(inputs=inputs),
        approach=approach,
        spots={'A1': spot},
    ) == spot


def test_reached_row_across_the_from_is_not_taken() -> None:
    """At B2, a row below C, a bus from above counts its stops instead."""
    (marker,), rows = badges_of(inputs=approaching(
        minutes=3, stations=('P3', 'B2', 'P1', 'C', 'B1'),
    ))
    assert marker.y == rows['A2'].y


def test_partner_on_a_branch_row_falls_back_to_the_count() -> None:
    """P2's partner Z1 is a branch row; two stops out, it faces A2."""
    (marker,), rows = badges_of(inputs=approaching(
        minutes=9, facing={'P2': 'Z1'}, branches=(tail('A1 Z1', 2),),
    ))
    assert rows['Z1'].kind is RowKind.BRANCH
    assert marker.y == rows['A2'].y


def test_stop_under_a_station_faces_that_stations_partner() -> None:
    """Stop p2 under P2 takes P2's partner A1."""
    inputs = approaching(
        minutes=9, facing={'P2': 'A1'},
        stations=('P5', 'P4', 'P3', 'p2', 'P1', 'C', 'B1', 'B2'),
    )
    assert inputs.reach is not None
    inputs = replace(inputs, reach=replace(
        inputs.reach, station_of={**inputs.reach.station_of, 'p2': 'P2'},
    ))
    (marker,), rows = badges_of(inputs=inputs)
    assert marker.y == rows['A1'].y


def test_carry_on_through_the_from_is_not_drawn_approaching() -> None:
    """After y ends, its run on as z towards C again draws nothing."""
    inputs = approaching(minutes=25)
    (item,) = inputs.departures
    then = CarryOn(route='T1', headsign='C', trip_id='z')
    inputs = replace(
        inputs,
        departures=(replace(item, departure=replace(
            item.departure,
            facts=replace(item.departure.facts, then=then),
        )),),
        patterns={
            **inputs.patterns,
            'z': line_pattern(
                trip_id='z', stations=('P2', 'P1', 'C', 'B1'), minutes=24,
            ),
        },
    )
    markers, _ = badges_of(inputs=inputs)
    assert not markers


def departed_and_approaching() -> StripInputs:
    """
    Make a bus at P1 at 8:12 and one just gone from x2 towards A1.

    Returns
    -------
    StripInputs
        The inputs.
    """
    inputs = approaching(minutes=12)
    return replace(
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


def test_approach_marker_sits_between_the_facing_rows_by_progress() -> None:
    """A third of P2 to P1, it is a third of the way from A2 to A1."""
    (marker,), rows = badges_of(
        inputs=approaching(minutes=10, facing={'P2': 'A2'}),
    )
    assert (marker.x, marker.bearing) == (TRACK_X, 180.0)
    assert marker.y == pytest.approx(
        rows['A2'].y + (rows['A1'].y - rows['A2'].y) / 3,
    )


def test_approach_marker_never_pushes_a_name() -> None:
    """Meeting a this-side bus, the names stay where an on-track badge puts
    them."""
    _, rows = badges_of(inputs=departed_and_approaching())
    assert all(
        row.name_after in (None, TRACK_X + HALF_WIDE)
        for row in rows.values()
    )


def test_far_kerb_and_this_side_badges_fold_into_a_two_ended_count(
) -> None:
    """The far-kerb bus at A1 folds into the one gone from x2."""
    (marker,), rows = badges_of(inputs=departed_and_approaching())
    assert (marker.route, marker.x, marker.y, marker.ends) == (
        '+2', TRACK_X, rows['A1'].y + 12, True,
    )


def test_facing_station_uses_the_kerb_pair_for_hills_road() -> None:
    """With 063 paired to 060, 063's row is 060, not the count's row."""
    pair = KerbPair(station='063', other=place('060'), busy=False)
    assert facing_station(
        lane=('061', '060'), side=(), facing=facing_of(pairs={'063': pair}),
        station='063', count=1,
    ) == '060'
