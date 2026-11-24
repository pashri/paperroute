"""Tests for stop view: horizon and order."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from paperroute_device.core.text import clock_text
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyFeed
from paperroute_testkit.core.factories import START, departure
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import CarryOn, TripFacts
from paperroute_transit.core.stop_view import (
    HORIZON,
    LARGE_ROWS_UNDER_BANNER,
    LARGE_VISIBLE_ROWS,
    ROWS_UNDER_BANNER,
    VISIBLE_ROWS,
    Pill,
    StopInputs,
    StopView,
    arrival_line,
    build_stop_view,
    to_row,
)
from tests.core.stop_view.helpers import hero, live, many, view


def test_horizon_is_three_hours() -> None:
    """The horizon the empty message quotes is three hours."""
    assert HORIZON == timedelta(hours=3)


def fall_back(*, minutes: int, ride: int) -> LiveDeparture:
    """
    A departure leaving some real minutes after 02:00 AEDT on fall-back night.

    Parameters
    ----------
    minutes : int
        Real minutes after 02:00 AEDT.
    ride : int
        Real ride minutes.

    Returns
    -------
    LiveDeparture
        Departure.
    """
    start = datetime(2026, 4, 5, 2, 0, tzinfo=SYDNEY_ZONE).astimezone(UTC)
    moment = start + timedelta(minutes=minutes)
    arrives = moment + timedelta(minutes=ride)
    dep = departure(departs=moment.astimezone(SYDNEY_ZONE), ride=ride)
    return LiveDeparture(
        departure=dep,
        departs=moment.astimezone(SYDNEY_ZONE),
        arrives=arrives.astimezone(SYDNEY_ZONE),
        live=True,
        cancelled=False,
    )


def fall_back_view(
    *, departures: tuple[LiveDeparture, ...], now: datetime
) -> StopView:
    """
    Build a view on fall-back night.

    Parameters
    ----------
    departures : tuple[LiveDeparture, ...]
        Departures.
    now : datetime
        Current time.

    Returns
    -------
    StopView
        View.
    """
    return build_stop_view(
        inputs=StopInputs(
            origin='A',
            destination='B',
            departures=departures,
            now=now,
            scroll=0,
            live_since=now,
            has_feed=True,
            stale_after=timedelta(minutes=2),
            zone=SYDNEY_ZONE,
            city=SYDNEY,
        ),
    )


def test_view_ride_minutes_over_fall_back() -> None:
    """A ride spanning the clocks going back counts real minutes."""
    now = datetime(2026, 4, 5, 1, 50, tzinfo=SYDNEY_ZONE)
    result = fall_back_view(
        departures=(fall_back(minutes=0, ride=90),), now=now
    )
    assert hero(result=result).ride_minutes == 90


def test_view_drops_departure_gone_after_fall_back() -> None:
    """A departure 5 real minutes ago is gone even if its clock reads later."""
    gone = fall_back(minutes=58, ride=10)
    now = datetime(2026, 4, 5, 2, 3, fold=1, tzinfo=SYDNEY_ZONE)
    result = fall_back_view(departures=(gone,), now=now)
    assert result.hero is None
    assert result.empty is not None


def test_view_faster_compares_real_arrival_order() -> None:
    """FASTER uses real time when arrivals straddle the clocks going back."""
    slow = fall_back(minutes=30, ride=50)
    quick = fall_back(minutes=40, ride=10)
    now = datetime(2026, 4, 5, 1, 50, tzinfo=SYDNEY_ZONE)
    result = fall_back_view(departures=(slow, quick), now=now)
    assert [row.pill for row in result.rows] == [Pill.FASTER]


def test_view_lists_in_live_order() -> None:
    """A late bus leaving after an on-time one moves behind it."""
    result = view(
        departures=(
            live(minutes=2, late=10),
            live(minutes=5),
            live(minutes=8),
        )
    )
    assert hero(result=result).countdown == '5'
    assert [row.countdown for row in result.rows] == ['8', '12']


def test_view_faster_judged_in_live_order() -> None:
    """FASTER compares a service with the one leaving just ahead of it live."""
    result = view(
        departures=(
            live(minutes=2, ride=40, late=10),
            live(minutes=5, ride=50),
        )
    )
    assert hero(result=result).countdown == '5'
    assert hero(result=result).pill is None
    assert [(row.countdown, row.pill) for row in result.rows] == [
        ('12', Pill.FASTER)
    ]


def test_view_cancelled_keeps_timetabled_place() -> None:
    """Live order moves running services; a cancelled one stays put."""
    result = view(
        departures=(
            live(minutes=2, late=10),
            live(minutes=4, cancelled=True),
            live(minutes=6),
        )
    )
    assert hero(result=result).countdown == '6'
    assert [(row.countdown, row.pill) for row in result.rows] == [
        ('4', Pill.CANCELLED),
        ('12', None),
    ]


def test_view_stops_at_the_horizon() -> None:
    """Departures loaded past now plus HORIZON aren't listed."""
    result = view(
        departures=(
            live(minutes=10),
            live(minutes=180),
            live(minutes=170, late=15),
            live(minutes=181),
        )
    )
    assert hero(result=result).leaves_at == '8:10'
    assert [row.leaves_at for row in result.rows] == ['11:00']


def test_view_cancelled_placed_by_timetabled_time() -> None:
    """A cancelled 8:04 sits between live 8:03 and 8:06 wherever it was."""
    result = view(
        departures=(
            live(minutes=1, late=5),
            live(minutes=3),
            live(minutes=4, cancelled=True),
        )
    )
    assert hero(result=result).leaves_at == '8:03'
    assert [(row.leaves_at, row.pill) for row in result.rows] == [
        ('8:04', Pill.CANCELLED),
        ('8:06', None),
    ]


def test_no_destination_has_a_hero_and_lists_the_rest() -> None:
    """The first departure is the hero; a bus and a light rail follow."""
    result = view(
        departures=(
            live(minutes=9, mode=SydneyFeed.LIGHT_RAIL),
            live(minutes=3, mode=SydneyFeed.TRAIN),
            live(minutes=6),
        ),
        destination=None,
    )
    assert hero(result=result).vehicle == 'train'
    assert [row.countdown for row in result.rows] == ['6', '9']
    assert [row.vehicle for row in result.rows] == ['bus', 'light rail']
    assert result.empty is None


def test_no_destination_scroll_clamps() -> None:
    """Scrolling past the end shows the last full page; 0 is the first."""
    first = view(departures=many(), destination=None)
    assert len(first.rows) == VISIBLE_ROWS
    assert first.rows[0].countdown == '4'
    past = view(departures=many(), destination=None, scroll=99)
    assert past.scroll == len(many()) - 1 - VISIBLE_ROWS
    assert past.rows[-1].leaves_at == '8:56'
    under = view(departures=many(), destination=None, scroll=99, age=None)
    assert len(under.rows) == ROWS_UNDER_BANNER
    assert under.scroll == len(many()) - 1 - ROWS_UNDER_BANNER


def test_no_destination_rows_keep_cancelled_but_never_faster() -> None:
    """A cancelled trip carries CANCELLED; with no Destination, no FASTER."""
    result = view(
        departures=(
            live(minutes=3, cancelled=True),
            live(minutes=6, ride=40),
            live(minutes=9, ride=20),
        ),
        destination=None,
    )
    assert hero(result=result).countdown == '6'
    assert [row.pill for row in result.rows] == [Pill.CANCELLED, None]


def test_row_carries_on_tag() -> None:
    """A row holds its departure's carry-on, with or without a destination."""
    on = CarryOn(route='811', headsign='Parramatta Stn')
    for destination in ('Parramatta Station', None):
        result = view(
            departures=(
                live(minutes=3, facts=TripFacts(then=on)),
                live(minutes=6),
            ),
            destination=destination,
        )
        rows = (*([result.hero] if result.hero else []), *result.rows)
        assert [row.then for row in rows] == [on, None]


def test_train_row_carries_on_to_another_line() -> None:
    """A T1 departure that runs on as a T9 has the T9 in its row."""
    result = view(
        departures=(
            live(
                minutes=3,
                mode=SydneyFeed.TRAIN,
                facts=TripFacts(then=CarryOn(route='T9', headsign='Hornsby')),
            ),
        ),
        destination=None,
    )
    then = hero(result=result).then
    assert then is not None
    assert then.route == 'T9'


def test_row_for_carry_on() -> None:
    """A carry-on row names the route it becomes and where that goes."""
    onto = CarryOn(route='T2', headsign='Leppington Station')
    item = live(minutes=3, ride=40, facts=TripFacts(onto=onto))
    row = to_row(
        item=item,
        now=START,
        pill=None,
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert row.onto == 'T2'
    assert row.route == '606'
    assert row.destination == 'Leppington'
    assert row.arrives_at == clock_text(moment=item.arrives, zone=SYDNEY_ZONE)
    same = CarryOn(route='606', headsign='Revesby Station')
    row = to_row(
        item=live(minutes=3, facts=TripFacts(onto=same)), now=START, pill=None,
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert row.onto is None
    assert row.destination == 'Revesby'


def test_arrival_line_counts_stops() -> None:
    """The small line adds the stop count, singular for one."""
    dep = replace(departure(departs=START), stops=6)
    row = to_row(
        item=LiveDeparture(
            departure=dep, departs=dep.departs, arrives=dep.arrives,
            live=False, cancelled=False,
        ),
        now=START,
        pill=None,
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert row.stops == 6
    assert arrival_line(row=row) == f'arr {row.arrives_at} \u00b7 6 stops'
    assert arrival_line(row=replace(row, stops=1)).endswith(' \u00b7 1 stop')
    assert arrival_line(row=replace(row, stops=None)) == (
        f'arr {row.arrives_at}'
    )


def test_large_shows_six_rows() -> None:
    """Larger text fits six rows, five under a banner, and clamps."""
    items = tuple(live(minutes=minute) for minute in range(0, 60, 5))
    assert len(items) == 12
    plain = view(departures=items, destination=None, large=True)
    assert len(plain.rows) == LARGE_VISIBLE_ROWS == 6
    banner = view(departures=items, destination=None, large=True, age=None)
    assert len(banner.rows) == LARGE_ROWS_UNDER_BANNER == 5
    far = view(departures=items, destination=None, large=True, scroll=99)
    assert far.scroll == len(items) - 1 - 6
    assert far.large
