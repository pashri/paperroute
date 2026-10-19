"""Tests for the Strip and Strip + map view-models."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

import pytest

from paperroute.core.geo import Point
from paperroute.core.live import LiveDeparture
from paperroute.core.map_view import Marker
from paperroute.core.model import CarryOn, Departure, RouteKind, StopPlace
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.reach import (
    Reach,
    ReachInput,
    ReachRow,
    RowKind,
    RunTail,
    StripKey,
    build_reach,
)
from paperroute.core.stop_view import (
    NOT_PINNED,
    StopInputs,
    StopView,
    build_stop_view,
)
from paperroute.core.strip_badges import DISC_SPAN, HALF_WIDE
from paperroute.core.strip_model import (
    LOADING,
    NEEDS_UPDATE,
    NO_STRIP,
    PANE_TRACK_X,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    StripInputs,
    StripRow,
)
from paperroute.core.strip_times import strip_trips
from paperroute.core.strip_view import (
    build_strip_pane,
    build_strip_view,
    line_reach,
)
from tests.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    pattern,
    snapshot,
    vehicle,
)

TRAIN: Final[StripKey] = StripKey(
    station='C', kind=RouteKind.TRAIN, routes=(),
)
POINTS: Final[dict[str, Point]] = {
    'C': (0.0, 0.0),
    'E1': (0.0, -1000.0),
    'E2': (0.0, -2000.0),
    'J': (0.0, 1000.0),
    'X': (5000.0, 0.0),
}
EAST: Final[tuple[str, ...]] = ('C', 'E1', 'E2')
WEST: Final[tuple[str, ...]] = ('C', 'J', 'W1', 'W2')
SOUTH: Final[tuple[str, ...]] = ('C', 'J', 'R1', 'R2', 'R3')


def example_reach() -> Reach:
    """
    Build the reach W2 W1 R3 R2 R1 J C E1 E2, with C the From.

    Returns
    -------
    Reach
        The reach; `W1_1` is a platform of W1.
    """
    keys = ('C', 'J', 'W1', 'W2', 'R1', 'R2', 'R3', 'E1', 'E2')
    places = {
        key: StopPlace(
            stop_id=key, name=f'{key} Station', point=POINTS.get(key),
        )
        for key in keys
    }
    return build_reach(data=ReachInput(
        origin=places['C'],
        tails=(
            RunTail(before=(), after=('J', 'W1', 'W2'), trips=6),
            RunTail(before=(), after=('J', 'R1', 'R2', 'R3'), trips=3),
            RunTail(before=(), after=('E1', 'E2'), trips=8),
        ),
        places=places,
        station_of={'W1_1': 'W1', **{key: key for key in keys}},
    ))


def long_reach() -> Reach:
    """
    Build a 40-row depth-0 reach with the From at row 20.

    Returns
    -------
    Reach
        Rows S0 to S39, tracks running away from S20.
    """
    return Reach(
        rows=tuple(
            ReachRow(
                place=StopPlace(stop_id=f'S{i}', name=f'S{i}', point=None),
                kind=RowKind.ORIGIN if i == 20 else RowKind.MAIN,
                depth=0,
                above=i < 20,
                parent=None if i == 20 else (i + 1 if i < 20 else i - 1),
            )
            for i in range(40)
        ),
        stations=39,
        station_of={},
    )


def train(
    *, minutes: int, trip_id: str, kind: RouteKind = RouteKind.TRAIN,
) -> Departure:
    """
    Make a departure from C some minutes after 8:00.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00.
    trip_id : str
        Trip ID.
    kind : RouteKind
        Kind of route.

    Returns
    -------
    Departure
        The departure.
    """
    return departure(
        departs=START + timedelta(minutes=minutes),
        trip_id=trip_id,
        route='T1',
        kind=kind,
    )


def line_pattern(
    *, trip_id: str, stations: Sequence[str], minutes: int,
) -> TripPattern:
    """
    Make a pattern calling every 3 minutes from some minutes after 8:00.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    stations : Sequence[str]
        Stations in order.
    minutes : int
        Minutes after 8:00 at the first station.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return pattern(
        trip_id=trip_id,
        route='T1',
        stops=calls(
            stations=stations,
            start=EIGHT + minutes * 60,
            step=180,
            points=POINTS,
        ),
    )


def live(
    *, item: Departure, late: int = 0, cancelled: bool = False,
) -> LiveDeparture:
    """
    Wrap a departure with a delay.

    Parameters
    ----------
    item : Departure
        Departure.
    late : int
        Minutes late.
    cancelled : bool
        Whether it is cancelled.

    Returns
    -------
    LiveDeparture
        The departure, shifted by the delay.
    """
    return LiveDeparture(
        departure=item,
        departs=item.departs + timedelta(minutes=late),
        arrives=item.arrives + timedelta(minutes=late),
        live=late != 0,
        cancelled=cancelled,
    )


def stop_view_of(
    *,
    departures: Sequence[LiveDeparture],
    now: datetime,
    destination: str | None = None,
) -> StopView:
    """
    Build the Stop view of some departures from C.

    Parameters
    ----------
    departures : Sequence[LiveDeparture]
        Departures.
    now : datetime
        Current time.
    destination : str | None
        Destination name.

    Returns
    -------
    StopView
        The view.
    """
    return build_stop_view(inputs=StopInputs(
        origin='C Station',
        destination=destination,
        departures=tuple(departures),
        now=now,
        scroll=0,
        live_since=now,
        has_feed=True,
    ))


def inputs_of(
    *,
    reach: Reach | None,
    departures: Sequence[LiveDeparture] = (),
    patterns: Mapping[str, TripPattern | None] | None = None,
    positions: Mapping[str, PositionsSnapshot] | None = None,
    now: datetime = START - timedelta(minutes=2),
    destination: str | None = None,
) -> StripInputs:
    """
    Make Strip inputs with the usual defaults.

    Parameters
    ----------
    reach : Reach | None
        The reach.
    departures : Sequence[LiveDeparture]
        Departures from C.
    patterns : Mapping[str, TripPattern | None] | None
        Loaded patterns.
    positions : Mapping[str, PositionsSnapshot] | None
        Positions downloads.
    now : datetime
        Current time.
    destination : str | None
        Destination stop or station ID.

    Returns
    -------
    StripInputs
        The inputs.
    """
    return StripInputs(
        reach=reach,
        unavailable=None,
        departures=tuple(departures),
        patterns=patterns or {},
        positions=positions or {},
        now=now,
        scroll=0,
        cursor=None,
        picking=None,
        destination=destination,
        tag=NOT_PINNED,
        stop_view=stop_view_of(departures=departures, now=now),
        key=TRAIN,
    )


def three_trains() -> StripInputs:
    """
    Make inputs with one train each way from C at 8:00, 8:03 and 8:04.

    Returns
    -------
    StripInputs
        The inputs on the example reach, at 7:58.
    """
    trips = {'e': (EAST, 0), 'w': (WEST, 3), 'r': (SOUTH, 4)}
    return inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=minutes, trip_id=trip))
            for trip, (_, minutes) in trips.items()
        ],
        patterns={
            trip: line_pattern(trip_id=trip, stations=line, minutes=minutes)
            for trip, (line, minutes) in trips.items()
        },
    )


def row_named(*, rows: Sequence[StripRow], name: str) -> StripRow:
    """
    Find a row by name.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Rows.
    name : str
        Station name without ' Station'.

    Returns
    -------
    StripRow
        The row.
    """
    return next(row for row in rows if row.name == name)


def slot_of(*, row: StripRow) -> int:
    """
    Find the visible slot a row sits in.

    Parameters
    ----------
    row : StripRow
        Row.

    Returns
    -------
    int
        Slot from the top, from 0.
    """
    return int((row.y - STRIP_TOP - ROW_H / 2) // ROW_H)


def times(*, inputs: StripInputs) -> dict[str, str]:
    """
    Read each row's time.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.

    Returns
    -------
    dict[str, str]
        Time by row name.
    """
    view = build_strip_view(inputs=inputs)
    return {row.name: row.time for row in view.rows}


def test_from_sits_on_third_row() -> None:
    """With no scroll FROM is on the third of twelve rows."""
    view = build_strip_view(inputs=inputs_of(reach=long_reach()))
    assert len(view.rows) == 12
    assert view.rows[2].name == 'S20'
    assert view.rows[2].bold
    assert view.rows[2].y == STRIP_TOP + 2 * ROW_H + ROW_H / 2
    assert view.band is None


def test_short_reach_starts_at_the_top() -> None:
    """A reach that fits shows every row from the top, names trimmed."""
    view = build_strip_view(inputs=inputs_of(reach=example_reach()))
    assert [row.name for row in view.rows] == [
        'W2', 'W1', 'R3', 'R2', 'R1', 'J', 'C', 'E1', 'E2',
    ]
    spur = row_named(rows=view.rows, name='R1')
    assert (spur.depth, spur.parent_depth) == (1, 0)
    assert spur.parent_y == row_named(rows=view.rows, name='J').y
    assert row_named(rows=view.rows, name='C').parent_y is None


def test_band_shows_when_from_scrolls_off() -> None:
    """The band counts the fewest detents back that show FROM."""
    base = inputs_of(reach=long_reach())
    view = build_strip_view(inputs=replace(base, scroll=20))
    assert view.band is not None
    assert view.band.top
    assert view.band.text == 'FROM S20 · 18 turns away'
    assert len(view.rows) == 11
    assert view.rows[0].y == STRIP_TOP + ROW_H + ROW_H / 2
    assert build_strip_view(inputs=replace(base, scroll=2)).band is None
    assert build_strip_view(inputs=replace(base, scroll=3)).band is not None


def names_shown(*, destination: str, scroll: int = 0) -> list[str]:
    """
    List the rows shown on the long reach with a Destination.

    Parameters
    ----------
    destination : str
        Destination row's station.
    scroll : int
        Rows scrolled down.

    Returns
    -------
    list[str]
        Row names, top to bottom.
    """
    inputs = inputs_of(reach=long_reach(), destination=destination)
    view = build_strip_view(inputs=replace(inputs, scroll=scroll))
    return [row.name for row in view.rows]


def test_destination_ride_shown_when_it_fits() -> None:
    """The window moves the least that shows FROM to the Destination."""
    assert names_shown(destination='S26')[2] == 'S20'
    assert names_shown(destination='S31')[::11] == ['S20', 'S31']
    assert names_shown(destination='S12')[::8] == ['S12', 'S20']


def test_long_ride_starts_at_from_toward_the_destination() -> None:
    """A ride longer than the strip starts at FROM and runs its way."""
    assert names_shown(destination='S35')[0] == 'S20'
    assert names_shown(destination='S5')[-1] == 'S20'
    inputs = inputs_of(reach=long_reach(), destination='S35')
    view = build_strip_view(inputs=replace(inputs, scroll=1))
    assert view.band is not None
    assert view.band.text == 'FROM S20 · 1 turn away'
    assert view.band.top


def test_band_at_the_bottom_when_scrolled_up() -> None:
    """Scrolling up past FROM puts the band in the bottom slot."""
    base = inputs_of(reach=long_reach())
    view = build_strip_view(inputs=replace(base, scroll=-12))
    assert view.band is not None
    assert not view.band.top
    assert view.band.text == 'FROM S20 · 3 turns away'
    assert slot_of(row=view.rows[-1]) == 10
    assert build_strip_view(inputs=replace(base, scroll=-9)).band is None


def test_band_says_turn_for_one() -> None:
    """One detent away reads in the singular."""
    view = build_strip_view(inputs=replace(
        inputs_of(reach=long_reach()), scroll=3,
    ))
    assert view.band is not None
    assert view.band.text == 'FROM S20 · 1 turn away'


def test_band_counts_cursor_distance_while_picking() -> None:
    """While picking, the band counts rows from the cursor to FROM."""
    view = build_strip_view(inputs=replace(
        inputs_of(reach=long_reach()), picking='TO', cursor=35,
    ))
    assert view.band is not None
    assert view.band.text == 'FROM S20 · 15 turns away'


def test_cursor_keeps_two_rows_margin() -> None:
    """The cursor row stays two rows from either edge."""
    base = replace(inputs_of(reach=long_reach()), picking='TO')
    for cursor in range(2, 38):
        view = build_strip_view(inputs=replace(base, cursor=cursor))
        row = next(row for row in view.rows if row.cursor)
        assert row.name == f'S{cursor}'
        assert 2 <= slot_of(row=row) <= 9
        assert row.tag == ('ALL' if cursor == 20 else 'TO?')


def test_cursor_on_from_while_picking_to_reads_all() -> None:
    """Pressing on the From row commits all departures, so it reads ALL."""
    view = build_strip_view(inputs=replace(
        inputs_of(reach=long_reach()), picking='TO', cursor=20,
    ))
    row = next(row for row in view.rows if row.cursor)
    assert row.name == 'S20'
    assert row.tag == 'ALL'


def test_ride_rows_between_from_and_to() -> None:
    """The ride runs from FROM to TO, both bold."""
    view = build_strip_view(
        inputs=replace(three_trains(), destination='W1'),
    )
    ride = [row.name for row in view.rows if row.ride]
    assert ride == ['W1', 'J', 'C']
    assert [row.name for row in view.rows if row.bold] == ['W1', 'C']
    assert {row.tag for row in view.rows} == {''}


def test_times_only_on_ride_rows_with_destination() -> None:
    """With a destination only the ridden rows after FROM have times."""
    assert times(inputs=replace(three_trains(), destination='W1')) == {
        'W2': '', 'W1': '8:09', 'R3': '', 'R2': '', 'R1': '', 'J': '8:06',
        'C': '8:03', 'E1': '', 'E2': '',
    }


def test_times_on_every_reached_row_no_destination() -> None:
    """With no destination every reached row has its first arrival."""
    assert times(inputs=three_trains()) == {
        'W2': '8:12', 'W1': '8:09', 'R3': '8:16', 'R2': '8:13',
        'R1': '8:10', 'J': '8:06', 'C': '8:00', 'E1': '8:03', 'E2': '8:06',
    }
    assert not any(
        row.ride or row.bold
        for row in build_strip_view(inputs=three_trains()).rows
        if row.name != 'C'
    )


def test_times_skip_departed_and_cancelled() -> None:
    """A cancelled train and one long gone give no times."""
    inputs = three_trains()
    gone = replace(inputs, now=START + timedelta(minutes=2))
    assert times(inputs=gone)['E1'] == ''
    cancelled = replace(
        inputs,
        departures=tuple(
            live(item=item.departure, cancelled=True)
            for item in inputs.departures
        ),
    )
    assert set(times(inputs=cancelled).values()) == {''}


def test_times_shift_by_live_delay() -> None:
    """A departure 4 min late shows each row's time 4 min later."""
    inputs = three_trains()
    late = replace(
        inputs,
        departures=tuple(
            live(item=item.departure, late=4) for item in inputs.departures
        ),
    )
    assert times(inputs=late)['E1'] == '8:07'
    assert times(inputs=late)['W2'] == '8:16'


def test_times_ignore_other_kinds() -> None:
    """A bus calling at a strip station gives it no time."""
    inputs = three_trains()
    bus = train(minutes=0, trip_id='b', kind=RouteKind.BUS)
    view = replace(
        inputs,
        departures=(live(item=bus),),
        patterns={'b': line_pattern(trip_id='b', stations=EAST, minutes=0)},
    )
    assert set(times(inputs=view).values()) == {''}


def test_times_need_a_key_and_a_call_at_from() -> None:
    """No key, or a pattern that never calls at FROM, gives no times.

    The From's own time comes from the departures, not the patterns.
    """
    inputs = three_trains()
    assert set(times(inputs=replace(inputs, key=None)).values()) == {''}
    elsewhere = replace(
        inputs,
        patterns={
            trip: line_pattern(trip_id=trip, stations=('E1', 'E2'), minutes=0)
            for trip in ('e', 'w', 'r')
        },
    )
    shown = times(inputs=elsewhere)
    assert shown.pop('C') == '8:00'
    assert set(shown.values()) == {''}


def test_platform_destination_matches_its_station() -> None:
    """A destination platform W1_1 marks the ride to the W1 row."""
    view = build_strip_view(
        inputs=replace(three_trains(), destination='W1_1'),
    )
    assert row_named(rows=view.rows, name='W1').bold
    assert row_named(rows=view.rows, name='J').ride


def test_whole_stop_buses_never_crowd_out_train_markers() -> None:
    """A Train strip's trips are its trains, the panel the whole stop."""
    buses = [
        departure(
            departs=START + timedelta(seconds=20 * i),
            trip_id=f'bus{i}',
            kind=RouteKind.BUS,
        )
        for i in range(60)
    ]
    trains = [train(minutes=5 + i, trip_id=f'train{i}') for i in range(5)]
    everything = sorted([*buses, *trains], key=lambda item: item.departs)
    assert strip_trips(
        departures=everything, key=TRAIN, now=START,
    ) == {f'train{i}' for i in range(5)}
    inputs = inputs_of(
        reach=example_reach(),
        departures=[live(item=item) for item in everything],
        now=START,
    )
    panel = build_strip_view(inputs=inputs).panel
    assert panel.rows == inputs.stop_view.head_rows[:4]
    assert {row.route for row in panel.rows} == {'606'}


def test_no_destination_panel_has_four_departures_and_no_destination_line(
) -> None:
    """With no destination the panel shows four of the whole stop."""
    inputs = inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=i, trip_id=f't{i}')) for i in range(6)
        ],
    )
    panel = build_strip_view(inputs=inputs).panel
    assert panel.destination is None
    assert panel.no_destination
    assert len(panel.rows) == 4
    assert panel.title == 'C Station'
    assert panel.note == '8 stations from here'
    assert panel.hint is None


def test_destination_panel_has_destination_line() -> None:
    """With a destination the panel says where to."""
    inputs = three_trains()
    stop = stop_view_of(
        departures=inputs.departures, now=inputs.now, destination='Wx',
    )
    panel = build_strip_view(inputs=replace(inputs, stop_view=stop)).panel
    assert panel.destination == 'Wx'
    assert not panel.no_destination
    assert panel.rows == stop.head_rows[:4]


def test_panel_row_carries_on() -> None:
    """A panel row keeps the departure's carry-on."""
    then = CarryOn(route='T9', headsign='Hornsby')
    item = live(item=replace(
        train(minutes=1, trip_id='a'),
        facts=replace(train(minutes=1, trip_id='a').facts, then=then),
    ))
    inputs = inputs_of(reach=example_reach(), departures=[item])
    assert build_strip_view(inputs=inputs).panel.rows[0].then == then


def test_hint_leaves_three_panel_rows() -> None:
    """While picking TO the hint shows and only three rows fit."""
    inputs = inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=i, trip_id=f't{i}')) for i in range(6)
        ],
    )
    panel = build_strip_view(
        inputs=replace(inputs, picking='TO', cursor=7),
    ).panel
    assert panel.hint == 'Press: set TO · press FROM: all departures'
    assert len(panel.rows) == 3


def east_inputs(
    *, at: Point | None, reported: datetime, now: datetime,
) -> StripInputs:
    """
    Make inputs with one eastbound train leaving C at 8:00.

    Parameters
    ----------
    at : Point | None
        Where its vehicle reported, or None for no vehicle.
    reported : datetime
        When the vehicle reported.
    now : datetime
        Current time.

    Returns
    -------
    StripInputs
        The inputs.
    """
    vehicles = () if at is None else (vehicle(
        timestamp=int(reported.timestamp()), trip_id='e', point=at,
    ),)
    return inputs_of(
        reach=example_reach(),
        departures=[live(item=train(minutes=0, trip_id='e'))],
        patterns={
            'e': line_pattern(trip_id='e', stations=EAST, minutes=0),
        },
        positions={'trains': snapshot(fetched_at=now, vehicles=vehicles)},
        now=now,
    )


def only_marker(*, inputs: StripInputs) -> Marker:
    """
    Build the view and return its one marker.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.

    Returns
    -------
    Marker
        The marker.
    """
    (marker,) = build_strip_view(inputs=inputs).markers
    return marker


def test_live_vehicle_projected_between_rows() -> None:
    """A fresh position halfway from E1 to E2 sits halfway between rows."""
    now = START + timedelta(minutes=1)
    inputs = east_inputs(at=(30.0, -1500.0), reported=now, now=now)
    rows = build_strip_view(inputs=inputs).rows
    first, second = (row_named(rows=rows, name=key) for key in ('E1', 'E2'))
    marker = only_marker(inputs=inputs)
    assert marker.y == pytest.approx((first.y + second.y) / 2)
    assert marker.x == TRACK_X
    assert marker.bearing == 180
    assert marker.live
    assert marker.age is None
    assert marker.route == 'T1'


def test_live_vehicle_heading_up_points_up() -> None:
    """A westbound train's wedge points up the strip."""
    now = START + timedelta(minutes=4)
    inputs = replace(
        east_inputs(at=(0.0, 800.0), reported=now, now=now),
        departures=(live(item=train(minutes=3, trip_id='e')),),
        patterns={
            'e': line_pattern(trip_id='e', stations=WEST, minutes=3),
        },
    )
    marker = only_marker(inputs=inputs)
    assert marker.bearing == 0
    assert marker.x == TRACK_X


def test_stale_vehicle_hollow_at_timetable_hop_with_age() -> None:
    """A stale report puts a hollow badge at the timetable hop, aged."""
    now = START + timedelta(minutes=4, seconds=30)
    inputs = east_inputs(
        at=(0.0, 0.0), reported=now - timedelta(seconds=310), now=now,
    )
    rows = build_strip_view(inputs=inputs).rows
    first, second = (row_named(rows=rows, name=key) for key in ('E1', 'E2'))
    marker = only_marker(inputs=inputs)
    assert marker.y == pytest.approx((first.y + second.y) / 2)
    assert not marker.live
    assert marker.bearing is None
    assert marker.age == 300


def test_cancelled_trip_not_drawn() -> None:
    """A cancelled trip has no badge, even with a fresh vehicle."""
    now = START + timedelta(minutes=1)
    inputs = east_inputs(at=(0.0, -1500.0), reported=now, now=now)
    cancelled = replace(
        inputs,
        departures=tuple(
            live(item=item.departure, cancelled=True)
            for item in inputs.departures
        ),
    )
    assert build_strip_view(inputs=cancelled).markers == ()


def test_platform_stops_place_badges_on_their_station_rows() -> None:
    """Pattern stops under a strip station map to its row."""
    now = START + timedelta(minutes=1)
    inputs = east_inputs(at=(0.0, -1500.0), reported=now, now=now)
    reach = example_reach()
    platforms = replace(
        inputs,
        reach=replace(
            reach, station_of={**reach.station_of, 'E1_2': 'E1', 'E2_1': 'E2'},
        ),
        patterns={
            'e': pattern(
                trip_id='e',
                route='T1',
                stops=calls(
                    stations=('C', 'E1_2', 'E2_1'),
                    step=180,
                    points={
                        'C': POINTS['C'],
                        'E1_2': POINTS['E1'],
                        'E2_1': POINTS['E2'],
                    },
                ),
            ),
        },
    )
    rows = build_strip_view(inputs=platforms).rows
    first, second = (row_named(rows=rows, name=key) for key in ('E1', 'E2'))
    marker = only_marker(inputs=platforms)
    assert marker.y == pytest.approx((first.y + second.y) / 2)


def test_unreported_trip_hollow_without_age() -> None:
    """A trip that never reported is hollow with no age."""
    now = START + timedelta(minutes=4, seconds=30)
    marker = only_marker(inputs=east_inputs(at=None, reported=now, now=now))
    assert not marker.live
    assert marker.age is None


def test_vehicle_off_strip_not_drawn() -> None:
    """A vehicle on a hop to a station off the strip isn't drawn."""
    now = START + timedelta(minutes=1)
    inputs = replace(
        east_inputs(at=(2500.0, 0.0), reported=now, now=now),
        patterns={
            'e': line_pattern(trip_id='e', stations=('C', 'X'), minutes=0),
        },
    )
    assert build_strip_view(inputs=inputs).markers == ()


def test_overlapping_badges_pushed_down() -> None:
    """Two trains at one spot are drawn 22 px apart, in y order."""
    now = START + timedelta(minutes=4)
    inputs = east_inputs(at=None, reported=now, now=now)
    twin = live(item=train(minutes=0, trip_id='f'))
    inputs = replace(
        inputs,
        departures=(*inputs.departures, twin),
        patterns={
            **inputs.patterns,
            'f': line_pattern(trip_id='f', stations=EAST, minutes=0),
        },
    )
    first, second = build_strip_view(inputs=inputs).markers
    assert second.y - first.y == 22
    assert first.x == second.x


def test_crowded_hop_folds_into_a_count() -> None:
    """Six trains on one hop show one badge and a '+5' count."""
    now = START + timedelta(minutes=4)
    trips = [f't{i}' for i in range(6)]
    inputs = inputs_of(
        reach=example_reach(),
        departures=[live(item=train(minutes=0, trip_id=t)) for t in trips],
        patterns={
            t: line_pattern(trip_id=t, stations=EAST, minutes=0)
            for t in trips
        },
        now=now,
    )
    first, count = build_strip_view(inputs=inputs).markers
    assert (first.route, count.route) == ('T1', '+5')
    assert count.y - first.y == 22


def test_close_badges_pushed_to_one_step_apart() -> None:
    """A badge 12 px below another is pushed to 22 px below it."""
    now = START + timedelta(minutes=4)
    inputs = east_inputs(at=None, reported=now, now=now)
    inputs = replace(
        inputs,
        departures=(
            *inputs.departures, live(item=train(minutes=1, trip_id='f')),
        ),
        patterns={
            **inputs.patterns,
            'f': line_pattern(trip_id='f', stations=EAST, minutes=1),
        },
    )
    first, second = build_strip_view(inputs=inputs).markers
    origin = row_named(rows=build_strip_view(inputs=inputs).rows, name='C')
    assert first.y == pytest.approx(origin.y + ROW_H)
    assert second.y == pytest.approx(first.y + 22)


def test_spur_badge_sits_on_the_spur_track() -> None:
    """A vehicle heading into a spur is drawn on the spur's track."""
    now = START + timedelta(minutes=1)
    points = {**POINTS, 'R1': (1000.0, 1000.0)}
    inputs = replace(
        east_inputs(at=(500.0, 1000.0), reported=now, now=now),
        patterns={
            'e': pattern(
                trip_id='e',
                route='T1',
                stops=calls(stations=SOUTH, step=180, points=points),
            ),
        },
    )
    assert only_marker(inputs=inputs).x == TRACK_X + 16


def test_times_count_from_the_call_that_leaves_from() -> None:
    """A loop calling at FROM twice counts from the departure's own call."""
    inputs = replace(
        three_trains(),
        departures=(live(item=train(minutes=9, trip_id='loop')),),
        patterns={
            'loop': line_pattern(
                trip_id='loop',
                stations=('C', 'E1', 'E2', 'C', 'J'),
                minutes=0,
            ),
        },
    )
    shown = times(inputs=inputs)
    assert (shown['J'], shown['E1']) == ('8:12', '')


def test_unavailable_and_loading_texts() -> None:
    """No reach says loading, or why; an empty reach has no strip."""
    loading = build_strip_view(inputs=inputs_of(reach=None))
    assert (loading.empty, loading.rows) == (LOADING, ())
    old = build_strip_view(inputs=replace(
        inputs_of(reach=None), unavailable=NEEDS_UPDATE,
    ))
    assert old.empty == NEEDS_UPDATE
    lonely = build_reach(data=ReachInput(
        origin=StopPlace(stop_id='C', name='C', point=None),
        tails=(),
        places={},
        station_of={},
    ))
    bare = build_strip_view(inputs=inputs_of(reach=lonely))
    assert (bare.empty, bare.rows, bare.markers) == (NO_STRIP, (), ())
    assert build_strip_pane(inputs=inputs_of(reach=None)).rows == ()


def test_pane_puts_from_on_third_row_and_never_bands() -> None:
    """The pane keeps FROM on its third row whatever the scroll."""
    for scroll in (0, 20, -12):
        pane = build_strip_pane(inputs=replace(
            inputs_of(reach=long_reach()), scroll=scroll,
        ))
        assert len(pane.rows) == 12
        assert pane.rows[2].name == 'S20'
    short = build_strip_pane(inputs=three_trains())
    assert [row.name for row in short.rows] == ['R1', 'J', 'C', 'E1', 'E2']
    assert short.rows[2].bold


def test_pane_markers_sit_on_the_pane_track() -> None:
    """The pane's markers use the pane's track x."""
    now = START + timedelta(minutes=1)
    pane = build_strip_pane(
        inputs=east_inputs(at=(0.0, -1500.0), reported=now, now=now),
    )
    (marker,) = pane.markers
    assert marker.x == 500


def test_train_at_the_from_is_drawn_beside_its_disc() -> None:
    """A train at C stays on C's row, right of its disc, name moved on."""
    now = START + timedelta(minutes=1)
    inputs = east_inputs(at=(0.0, 0.0), reported=now, now=now)
    view = build_strip_view(inputs=inputs)
    pane = build_strip_pane(inputs=inputs)
    for rows, markers, track in (
        (view.rows, view.markers, TRACK_X),
        (pane.rows, pane.markers, PANE_TRACK_X),
    ):
        home = next(row for row in rows if row.bold)
        (marker,) = markers
        assert marker.y == home.y
        assert marker.x == track + DISC_SPAN
        assert home.name_after == marker.x + HALF_WIDE


def test_line_reach_has_no_origin_and_from_cursor_tag() -> None:
    """A towards line is a plain list; the FROM cursor row says FROM?."""
    line = [
        StopPlace(stop_id=key, name=key, point=None)
        for key in ('A', 'B', 'C', 'B')
    ]
    reach = line_reach(line=line)
    assert [row.place.stop_id for row in reach.rows] == ['A', 'B', 'C']
    assert {row.kind for row in reach.rows} == {RowKind.MAIN}
    assert [row.parent for row in reach.rows] == [None, 0, 1]
    assert {row.depth for row in reach.rows} == {0}
    assert reach.stations == 3
    assert not reach.station_of
    view = build_strip_view(inputs=StripInputs(
        reach=reach,
        unavailable=None,
        departures=(),
        patterns={},
        positions={},
        now=START,
        scroll=0,
        cursor=1,
        picking='FROM',
        destination=None,
        tag=NOT_PINNED,
    ))
    assert view.band is None
    assert [row.tag for row in view.rows] == ['', 'FROM?', '']
    assert [row.cursor for row in view.rows] == [False, True, False]
    assert view.panel.rows == ()
    assert view.panel.hint == 'Press: set FROM · top press: back'
    assert view.empty is None
