"""Tests for the Strip and Strip + map view-models."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from paperroute.core.map_view import Marker
from paperroute.core.stop_view import NOT_PINNED
from paperroute.core.strip_badges import HALF_WIDE
from paperroute.core.strip_model import (
    HINTS,
    INDENT,
    LOADING,
    NEEDS_UPDATE,
    NO_STRIP,
    OPEN_HINT,
    PANE_TRACK_X,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    TRACK_X,
    StripInputs,
    StripRow,
    hook_straight,
)
from paperroute.core.strip_times import strip_trips
from paperroute.core.strip_view import (
    build_strip_pane,
    build_strip_view,
    first_row,
    home_of,
    line_reach,
)
from paperroute_device.core.text import DOT
from paperroute_device.core.wifi_text import setup_footer
from paperroute_testkit.core.factories import (
    EAST,
    POINTS,
    STALE_LIMIT,
    START,
    TRAIN,
    calls,
    departure,
    east_inputs,
    example_reach,
    inputs_of,
    line_pattern,
    live,
    pattern,
    stop_view_of,
    train,
)
from paperroute_testkit.core.reach_factories import reach_of, tail
from paperroute_testkit.core.strip_factories import (
    SOUTH,
    WEST,
    long_reach,
    row_named,
    three_trains,
    times,
)
from paperroute_transit.core.model import CarryOn, RouteKind, StopPlace
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RowKind,
    RunTail,
    build_reach,
)
from paperroute_transit.core.reach_layout import LineExit


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
    """The band counts the stations between FROM and the strip."""
    base = inputs_of(reach=long_reach())
    view = build_strip_view(inputs=replace(base, scroll=20))
    assert view.band is not None
    assert view.band.top
    assert view.band.text == 'FROM S20 · 8 stops away'
    nearer = build_strip_view(inputs=replace(base, scroll=5)).band
    assert nearer is not None
    assert nearer.text == 'FROM S20 · 3 stops away'
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
    assert view.band.text == 'FROM S20 · 1 stop away'
    assert view.band.top


def test_band_at_the_bottom_when_scrolled_up() -> None:
    """Scrolling up past FROM puts the band in the bottom slot."""
    base = inputs_of(reach=long_reach())
    view = build_strip_view(inputs=replace(base, scroll=-12))
    assert view.band is not None
    assert not view.band.top
    assert view.band.text == 'FROM S20 · 3 stops away'
    assert slot_of(row=view.rows[-1]) == 10
    assert build_strip_view(inputs=replace(base, scroll=-9)).band is None


def test_band_says_stop_for_one() -> None:
    """One station between reads in the singular."""
    view = build_strip_view(inputs=replace(
        inputs_of(reach=long_reach()), scroll=3,
    ))
    assert view.band is not None
    assert view.band.text == 'FROM S20 · 1 stop away'


def test_band_counts_stations_to_the_strip_while_picking() -> None:
    """While picking, the band counts stations from FROM to the strip."""
    view = build_strip_view(inputs=replace(
        inputs_of(reach=long_reach()), picking='TO', cursor=35,
    ))
    assert view.band is not None
    assert view.band.text == 'FROM S20 · 6 stops away'


def test_band_skips_spur_rows_off_the_track_back() -> None:
    """Rows of a spur between FROM and the strip are not stops."""
    rows = list(long_reach().rows)
    for index in range(21, 26):
        rows[index] = replace(rows[index], depth=1)
    rows[26] = replace(rows[26], parent=20)
    reach = replace(long_reach(), rows=tuple(rows))
    view = build_strip_view(inputs=replace(
        inputs_of(reach=reach), scroll=10,
    ))
    assert view.rows[0].name == 'S29'
    assert view.band is not None
    assert view.band.text == 'FROM S20 · 3 stops away'


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


def test_times_on_ride_rows_and_on_past_the_destination() -> None:
    """The ridden rows and the line on past TO have times, not branches."""
    assert times(inputs=replace(three_trains(), destination='W1')) == {
        'W2': '8:12', 'W1': '8:09', 'R3': '', 'R2': '', 'R1': '', 'J': '8:06',
        'C': '8:03', 'E1': '', 'E2': '',
    }


def test_times_come_from_the_next_train_no_destination() -> None:
    """With no destination only the next train's calls have times."""
    assert times(inputs=three_trains()) == {
        'W2': '', 'W1': '', 'R3': '', 'R2': '',
        'R1': '', 'J': '', 'C': '8:00', 'E1': '8:03', 'E2': '8:06',
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
    assert panel.rows == inputs.stop_view.head_rows[:5]
    assert {row.route for row in panel.rows} == {'606'}


def test_no_destination_panel_has_five_departures_and_no_destination_line(
) -> None:
    """With no destination the panel shows five of the whole stop."""
    inputs = inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=i, trip_id=f't{i}')) for i in range(6)
        ],
    )
    panel = build_strip_view(inputs=inputs).panel
    assert panel.destination is None
    assert panel.no_destination
    assert len(panel.rows) == 5
    assert panel.title == 'C Station'
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


def test_hint_leaves_four_panel_rows() -> None:
    """While picking TO the hint shows and only four rows fit."""
    inputs = inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=i, trip_id=f't{i}')) for i in range(6)
        ],
    )
    panel = build_strip_view(
        inputs=replace(inputs, picking='TO', cursor=7),
    ).panel
    assert panel.hint == 'Bottom: press to set To \u2022 ALL: any stop'
    assert len(panel.rows) == 4


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


def test_train_at_the_from_is_drawn_on_its_disc() -> None:
    """A train at C sits on C's disc, its name moved past the badge."""
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
        assert marker.x == track
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
        stale_after=STALE_LIMIT,
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
    assert view.panel.hint == (
        'Bottom: press to set From \u2022 Top: press to go back'
    )
    assert view.empty is None


def exit_reach() -> Reach:
    """
    Build the reach R2 R1 W1 X0 J C E1 E2: X0 and W1 exits off J.

    Returns
    -------
    Reach
        The reach; W1 is taken by T1 trains, the busier X0 by T9 ones.
    """
    keys = ('C', 'J', 'R1', 'R2', 'W1', 'X0', 'E1', 'E2')
    places = {
        key: StopPlace(
            stop_id=key, name=f'{key} Station', point=POINTS.get(key),
        )
        for key in keys
    }
    return build_reach(data=ReachInput(
        origin=places['C'],
        tails=(
            RunTail(before=(), after=('J', 'R1', 'R2'), trips=6),
            RunTail(before=(), after=('E1', 'E2'), trips=8),
        ),
        places=places,
        station_of={key: key for key in keys},
        exits=(
            LineExit(junction='J', off='W1', lines=('T1',), trips=3),
            LineExit(junction='J', off='X0', lines=('T9',), trips=5),
        ),
    ))


def west_inputs(*, now: datetime) -> StripInputs:
    """
    Make inputs with one guessed train from C to W2 at 8:00.

    Parameters
    ----------
    now : datetime
        Current time.

    Returns
    -------
    StripInputs
        The inputs over `exit_reach`; the train reaches J at 8:03 and
        W1 at 8:06.
    """
    return inputs_of(
        reach=exit_reach(),
        departures=[live(item=train(minutes=0, trip_id='w'))],
        patterns={
            'w': line_pattern(trip_id='w', stations=WEST, minutes=0),
        },
        now=now,
    )


def test_exit_rows_are_named_with_their_lines() -> None:
    """Exit rows read '<off> · <lines>' with no time or bold."""
    view = build_strip_view(inputs=west_inputs(now=START))
    exits = [each for each in view.rows if each.kind is RowKind.EXIT]
    assert [row.name for row in exits] == ['W1 · T1', 'X0 · T9']
    assert {(row.time, row.tag, row.bold) for row in exits} == {
        ('', '', False),
    }


def test_bound_vehicle_rides_the_exit_stub() -> None:
    """J's track to the row before W1, then W1's stub, names past it."""
    early, late = (
        build_strip_view(inputs=west_inputs(
            now=START + timedelta(minutes=3, seconds=seconds),
        ))
        for seconds in (45, 135)
    )
    junction = row_named(rows=late.rows, name='J')
    stub = row_named(rows=late.rows, name='W1 · T1')
    (riding,) = early.markers
    (marker,) = late.markers
    assert riding.x == TRACK_X
    assert junction.y - ROW_H < riding.y < junction.y
    assert marker.x > TRACK_X + INDENT * junction.depth
    assert marker.y == stub.y
    for row in late.rows:
        if abs(row.y - marker.y) < ROW_H / 2:
            assert row.name_after is not None
            assert row.name_after >= marker.x + HALF_WIDE


def test_exit_spot_is_hook_middle() -> None:
    """A train leaving by W1 sits mid-way along W1's hook straight."""
    view = build_strip_view(inputs=west_inputs(
        now=START + timedelta(minutes=5, seconds=15),
    ))
    junction = row_named(rows=view.rows, name='J')
    (marker,) = view.markers
    left, right = hook_straight(
        track_x=TRACK_X + INDENT * junction.depth,
    )
    assert marker.x == (left + right) / 2
    assert marker.y == row_named(rows=view.rows, name='W1 · T1').y


def test_vehicle_past_the_exit_station_is_gone() -> None:
    """Once the train reaches W1 it leaves the strip."""
    now = START + timedelta(minutes=7)
    assert build_strip_view(inputs=west_inputs(now=now)).markers == ()


def test_scroll_reaches_the_first_stop() -> None:
    """Scrolling up far enough shows the run's first stop at the top."""
    before = ' '.join(f'P{index}' for index in range(40))
    reach = reach_of(tails=[tail('E1', 4, before=before)])
    home = home_of(reach=reach, destination=None, rows=STRIP_ROWS)
    assert first_row(
        scroll=-50, cursor=None, total=len(reach.rows), home=home,
        rows=STRIP_ROWS,
    ) == 0


def test_approach_row_shows_its_time() -> None:
    """Approach rows show when the next departure calls there."""
    inputs = inputs_of(
        reach=reach_of(tails=[tail('E1 E2', 4, before='P2 P1')]),
        departures=[live(item=train(minutes=6, trip_id='a'))],
        patterns={
            'a': line_pattern(
                trip_id='a', stations=('P2', 'P1', 'C', 'E1', 'E2'),
                minutes=0,
            ),
        },
    )
    shown = times(inputs=inputs)
    assert (shown['P2'], shown['P1'], shown['C']) == ('8:00', '8:03', '8:06')


def test_banner_and_hint_leave_three_panel_rows() -> None:
    """Under a live data banner, picking TO leaves room for three rows."""
    inputs = inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=i, trip_id=f't{i}')) for i in range(6)
        ],
    )
    inputs = replace(
        inputs,
        stop_view=replace(inputs.stop_view, banner='No live data yet'),
    )
    panel = build_strip_view(
        inputs=replace(inputs, picking='TO', cursor=7),
    ).panel
    assert len(panel.rows) == 3


def joined_trains() -> StripInputs:
    """
    Make inputs for a train from C to J at 8:03 that runs on as one to W2.

    Returns
    -------
    StripInputs
        The inputs on the example reach, at 7:58, with W1 the TO.
    """
    return inputs_of(
        reach=example_reach(),
        departures=[live(item=train(minutes=3, trip_id='a'))],
        patterns={
            'a': replace(
                line_pattern(trip_id='a', stations=('C', 'J'), minutes=3),
                next_trip='b',
            ),
            'b': line_pattern(
                trip_id='b', stations=('J', 'W1', 'W2'), minutes=6,
            ),
        },
        destination='W1',
    )


def test_times_follow_the_train_through_a_join() -> None:
    """A train joining onto another has times to TO and beyond."""
    found = times(inputs=joined_trains())
    assert {name: found[name] for name in ('C', 'J', 'W1', 'W2')} == {
        'C': '8:03', 'J': '8:06', 'W1': '8:09', 'W2': '8:12',
    }


def test_no_times_until_the_joined_trip_shows_the_to_is_reached() -> None:
    """Without the joined trip's pattern no train is known to reach TO."""
    inputs = joined_trains()
    inputs = replace(
        inputs, patterns={'a': inputs.patterns['a']},
    )
    assert not any(times(inputs=inputs).values())


def test_onward_times_only_where_the_first_train_calls() -> None:
    """Past TO, a station the first train doesn't reach has no time."""
    inputs = joined_trains()
    patterns = dict(inputs.patterns)
    patterns['b'] = line_pattern(
        trip_id='b', stations=('J', 'W1'), minutes=6,
    )
    found = times(inputs=replace(inputs, patterns=patterns))
    assert (found['W1'], found['W2']) == ('8:09', '')


def test_hints_name_the_knob() -> None:
    """Every Strip and Wi-Fi hint names its knob, split by a bullet."""
    footer = setup_footer(minutes_left=10)
    assert HINTS == {
        'TO': 'Bottom: press to set To \u2022 ALL: any stop',
        'FROM': 'Bottom: press to set From \u2022 Top: press to go back',
    }
    assert OPEN_HINT == 'Bottom: press to open \u2022 ALL: any stop'
    assert footer == (
        'New password each time \u2022 closes in 10 min \u2022 '
        'Top: press to cancel'
    )
    for hint in (*HINTS.values(), OPEN_HINT, footer):
        assert DOT not in hint
