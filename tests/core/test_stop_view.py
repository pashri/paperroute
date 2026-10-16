"""Tests for the Stop view view-model."""

from datetime import UTC, datetime, timedelta

from paperroute.core.live import LiveDeparture
from paperroute.core.model import SYDNEY
from paperroute.core.stop_view import (
    HORIZON,
    ROWS_UNDER_BANNER,
    VISIBLE_ROWS,
    Pill,
    Row,
    StopView,
    build_stop_view,
    clock_text,
)
from tests.core.factories import START, departure


def live(
    *,
    minutes: int,
    ride: int = 30,
    cancelled: bool = False,
    is_live: bool = True,
) -> LiveDeparture:
    """
    A resolved departure leaving some minutes after START.

    Parameters
    ----------
    minutes : int
        Minutes after START.
    ride : int
        Ride minutes.
    cancelled : bool
        Cancelled flag.
    is_live : bool
        Live flag.

    Returns
    -------
    LiveDeparture
        Departure.
    """
    dep = departure(departs=START + timedelta(minutes=minutes), ride=ride)
    return LiveDeparture(
        departure=dep,
        departs=dep.departs,
        arrives=dep.arrives,
        live=is_live,
        cancelled=cancelled,
    )


def view(
    *,
    departures: tuple[LiveDeparture, ...] = (),
    scroll: int = 0,
    age: timedelta | None = timedelta(seconds=5),
    has_feed: bool = True,
) -> StopView:
    """
    Build a view with fixed names and time.

    Parameters
    ----------
    departures : tuple[LiveDeparture, ...]
        Departures.
    scroll : int
        Scroll offset.
    age : timedelta | None
        Age of the live data, or None when there is none.
    has_feed : bool
        Whether the pin has a live feed.

    Returns
    -------
    StopView
        View.
    """
    return build_stop_view(
        origin='Hollis St',
        destination='Parramatta Station',
        departures=departures,
        now=START,
        scroll=scroll,
        live_since=None if age is None else START - age,
        has_feed=has_feed,
    )


def hero(*, result: StopView) -> Row:
    """
    The hero row, which must exist.

    Parameters
    ----------
    result : StopView
        View.

    Returns
    -------
    Row
        Hero row.
    """
    assert result.hero is not None
    return result.hero


def many() -> tuple[LiveDeparture, ...]:
    """
    Fifteen departures, four minutes apart.

    Returns
    -------
    tuple[LiveDeparture, ...]
        Departures.
    """
    return tuple(live(minutes=minute) for minute in range(0, 60, 4))


def test_clock_text_has_no_leading_zero() -> None:
    """Clock times look like 8:05 and 13:40."""
    assert clock_text(moment=START + timedelta(minutes=5)) == '8:05'
    later = START + timedelta(hours=5, minutes=40)
    assert clock_text(moment=later) == '13:40'


def test_view_hero_and_rows() -> None:
    """The first non-cancelled departure is the hero; the rest are rows."""
    result = view(
        departures=(live(minutes=3), live(minutes=9), live(minutes=14))
    )
    assert hero(result=result).countdown == '3'
    assert [row.countdown for row in result.rows] == ['9', '14']


def test_view_row_fields() -> None:
    """A row carries the route, times, ride length and live flag."""
    row = hero(result=view(departures=(live(minutes=3, ride=25),)))
    assert (row.route, row.mode.value) == ('606', 'bus')
    assert row.destination == 'Parramatta'
    assert (row.leaves_at, row.arrives_at) == ('8:03', '8:28')
    assert (row.ride_minutes, row.live, row.pill) == (25, True, None)


def test_view_countdown_zero_is_now() -> None:
    """A departure this minute counts down as 'now'."""
    assert hero(result=view(departures=(live(minutes=0),))).countdown == 'now'


def test_view_countdown_over_99_minutes_shows_clock() -> None:
    """Countdowns of 100 minutes or more show the clock time instead."""
    result = view(departures=(live(minutes=130),))
    assert hero(result=result).countdown == '10:10'


def test_view_countdown_99_minutes_stays_minutes() -> None:
    """99 minutes still counts down."""
    result = view(departures=(live(minutes=99),))
    assert hero(result=result).countdown == '99'


def test_view_faster_and_cancelled_pills() -> None:
    """FASTER marks a later arrival-beater; CANCELLED keeps its row."""
    result = view(
        departures=(
            live(minutes=3, ride=40),
            live(minutes=6, ride=20),
            live(minutes=9, cancelled=True),
        )
    )
    assert hero(result=result).pill is None
    assert [row.pill for row in result.rows] == [Pill.FASTER, Pill.CANCELLED]


def test_view_cancelled_first_is_not_the_hero() -> None:
    """A cancelled first departure keeps its row and the next one leads."""
    result = view(
        departures=(live(minutes=3, cancelled=True), live(minutes=9))
    )
    assert hero(result=result).countdown == '9'
    assert [row.pill for row in result.rows] == [Pill.CANCELLED]


def test_view_skips_departed() -> None:
    """Departures already gone aren't shown."""
    result = view(departures=(live(minutes=-2), live(minutes=4)))
    assert hero(result=result).countdown == '4'


def test_view_scroll_is_clamped() -> None:
    """Scrolling can't go past the last full page."""
    items = many()
    last = len(items) - 1 - VISIBLE_ROWS
    assert view(departures=items, scroll=99).scroll == last
    assert view(departures=items, scroll=-5).scroll == 0
    assert view(departures=items, scroll=2).scroll == 2


def test_view_banner_when_no_live_data_yet() -> None:
    """With a feed but no snapshot, the banner says times are scheduled."""
    result = view(departures=(live(minutes=5),), age=None)
    assert result.banner == 'Timetable times \u00b7 no live data yet'


def test_view_banner_when_stale() -> None:
    """Data older than two minutes says when live data was last seen."""
    result = view(departures=(live(minutes=5),), age=timedelta(minutes=4))
    assert result.banner == 'No live data since 7:56'


def test_view_no_banner_when_fresh() -> None:
    """Fresh live data needs no banner and shows a full page."""
    result = view(departures=many())
    assert result.banner is None
    assert len(result.rows) == VISIBLE_ROWS


def test_view_fewer_rows_under_a_banner() -> None:
    """A banner takes a row's height, and scrolling clamps to match."""
    items = many()
    result = view(departures=items, scroll=99, age=None)
    assert len(result.rows) == ROWS_UNDER_BANNER
    assert result.scroll == len(items) - 1 - ROWS_UNDER_BANNER


def test_view_banner_when_mode_has_no_feed() -> None:
    """Pins without a live feed say so."""
    result = view(departures=(live(minutes=5),), age=None, has_feed=False)
    assert result.banner == (
        'Timetable times \u00b7 no live feed for this service'
    )


def test_view_empty_message() -> None:
    """With nothing to show, an empty message names the destination."""
    result = view()
    assert result.hero is None
    assert result.empty == (
        'No direct services to Parramatta Station in the next 3 hours'
    )


def test_view_empty_says_loading_before_the_timetable_loads() -> None:
    """Before the first load the empty message says it is loading."""
    result = build_stop_view(
        origin='Hollis St',
        destination='Parramatta Station',
        departures=(),
        now=START,
        scroll=0,
        live_since=START,
        has_feed=True,
        loaded=False,
    )
    assert result.empty == 'Loading timetable'


def test_view_empty_is_none_with_departures() -> None:
    """The empty message only appears when there is nothing to show."""
    assert view(departures=(live(minutes=5),)).empty is None


def test_horizon_is_three_hours() -> None:
    """The horizon the empty message quotes is three hours."""
    assert HORIZON == timedelta(hours=3)


def test_clock_text_converts_to_sydney() -> None:
    """A UTC moment reads as Sydney wall-clock time."""
    moment = datetime(2026, 10, 15, 21, 5, tzinfo=UTC)
    assert clock_text(moment=moment) == '8:05'


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
    start = datetime(2026, 4, 5, 2, 0, tzinfo=SYDNEY).astimezone(UTC)
    moment = start + timedelta(minutes=minutes)
    arrives = moment + timedelta(minutes=ride)
    dep = departure(departs=moment.astimezone(SYDNEY), ride=ride)
    return LiveDeparture(
        departure=dep,
        departs=moment.astimezone(SYDNEY),
        arrives=arrives.astimezone(SYDNEY),
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
        origin='A',
        destination='B',
        departures=departures,
        now=now,
        scroll=0,
        live_since=now,
        has_feed=True,
    )


def test_view_ride_minutes_over_fall_back() -> None:
    """A ride spanning the clocks going back counts real minutes."""
    now = datetime(2026, 4, 5, 1, 50, tzinfo=SYDNEY)
    result = fall_back_view(
        departures=(fall_back(minutes=0, ride=90),), now=now
    )
    assert hero(result=result).ride_minutes == 90


def test_view_drops_departure_gone_after_fall_back() -> None:
    """A departure 5 real minutes ago is gone even if its clock reads later."""
    gone = fall_back(minutes=58, ride=10)
    now = datetime(2026, 4, 5, 2, 3, fold=1, tzinfo=SYDNEY)
    result = fall_back_view(departures=(gone,), now=now)
    assert result.hero is None
    assert result.empty is not None


def test_view_faster_compares_real_arrival_order() -> None:
    """FASTER uses real time when arrivals straddle the clocks going back."""
    slow = fall_back(minutes=30, ride=50)
    quick = fall_back(minutes=40, ride=10)
    now = datetime(2026, 4, 5, 1, 50, tzinfo=SYDNEY)
    result = fall_back_view(departures=(slow, quick), now=now)
    assert [row.pill for row in result.rows] == [Pill.FASTER]
