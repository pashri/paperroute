"""Tests for stop view: rows and countdown."""

from dataclasses import replace
from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from paperroute_device.core.text import clock_text
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.factories import START
from paperroute_transit.core.stop_view import (
    NOT_PINNED,
    ROWS_UNDER_BANNER,
    VISIBLE_ROWS,
    Pill,
    PinTag,
    StopInputs,
    build_stop_view,
    countdown,
    live_line,
    pin_tag,
)
from tests.core.stop_view.helpers import hero, live, many, stop_inputs, view


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
    assert (row.route, row.vehicle, row.where) == ('606', 'bus', '')
    assert row.destination == 'Parramatta'
    assert (row.leaves_at, row.arrives_at) == ('8:03', '8:28')
    assert (row.ride_minutes, row.live, row.pill) == (25, True, None)


def test_view_countdown_zero_is_now() -> None:
    """A departure this minute counts down as 'now'."""
    assert hero(result=view(departures=(live(minutes=0),))).countdown == 'now'


def test_live_countdown_has_no_far_limit() -> None:
    """A live departure counts down however far away it is."""
    result = view(departures=(live(minutes=130),))
    assert hero(result=result).countdown == '130'


def test_countdown_counts_clock_minutes() -> None:
    """The count is the difference between the printed clock minutes."""
    now = datetime(2026, 10, 6, 7, 45, 50, tzinfo=SYDNEY_ZONE)
    soon = datetime(2026, 10, 6, 7, 48, 10, tzinfo=SYDNEY_ZONE)
    later = datetime(2026, 10, 6, 8, 55, tzinfo=SYDNEY_ZONE)
    assert countdown(departs=soon, now=now) == '3'
    assert countdown(departs=later, now=now) == '70'


def test_countdown_now_for_this_minute_and_just_gone() -> None:
    """This minute's departure, or last minute's, reads 'now'."""
    this = datetime(2026, 10, 6, 7, 45, 59, tzinfo=SYDNEY_ZONE)
    gone = datetime(2026, 10, 6, 7, 44, 30, tzinfo=SYDNEY_ZONE)
    assert countdown(
        departs=this,
        now=datetime(2026, 10, 6, 7, 45, tzinfo=SYDNEY_ZONE),
    ) == 'now'
    assert countdown(
        departs=gone,
        now=datetime(2026, 10, 6, 7, 45, 10, tzinfo=SYDNEY_ZONE),
    ) == 'now'


def test_countdown_counts_real_minutes_across_dst() -> None:
    """Across the clocks going forward the count stays in real minutes."""
    now = datetime(2026, 10, 4, 1, 59, tzinfo=SYDNEY_ZONE)
    departs = datetime(2026, 10, 4, 3, 1, tzinfo=SYDNEY_ZONE)
    assert countdown(departs=departs, now=now) == '2'


def clock_minutes(*, text: str) -> int:
    """
    Read a clock time as minutes of the day.

    Parameters
    ----------
    text : str
        For example '8:05'.

    Returns
    -------
    int
        Minutes since midnight.
    """
    hours, minutes = text.split(':')
    return int(hours) * 60 + int(minutes)


@given(
    now=st.datetimes(
        min_value=datetime(2026, 10, 5),
        max_value=datetime(2026, 10, 7),
        timezones=st.just(SYDNEY_ZONE),
    ),
    ahead=st.integers(min_value=0, max_value=3 * 3600),
)
def test_countdown_agrees_with_printed_clocks(
    now: datetime,
    ahead: int,
) -> None:
    """The count plus the printed now is the printed departure."""
    departs = now + timedelta(seconds=ahead)
    text = countdown(departs=departs, now=now)
    dep_min = clock_minutes(text=clock_text(moment=departs, zone=SYDNEY_ZONE))
    now_min = clock_minutes(text=clock_text(moment=now, zone=SYDNEY_ZONE))
    assert (dep_min - now_min) % 1440 == (0 if text == 'now' else int(text))


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


def test_live_line_waits_for_the_limit() -> None:
    """Live data 150 s old is stale under a 2 minute limit, not under 3."""
    since = START - timedelta(seconds=150)
    assert live_line(
        live_since=since, now=START, limit=timedelta(minutes=3),
        zone=SYDNEY_ZONE,
    ) is None
    assert live_line(
        live_since=since, now=START, limit=timedelta(minutes=2),
        zone=SYDNEY_ZONE,
    ) == 'No live data since 7:57'


def test_view_banner_follows_the_stale_limit() -> None:
    """The view's live line uses its inputs' stale limit."""
    inputs = stop_inputs(
        departures=(live(minutes=5),),
        age=timedelta(seconds=150),
    )
    result = build_stop_view(
        inputs=replace(inputs, stale_after=timedelta(minutes=3)),
    )
    assert result.banner is None


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
        inputs=StopInputs(
            origin='Hollis St',
            destination='Parramatta Station',
            departures=(),
            now=START,
            scroll=0,
            live_since=START,
            has_feed=True,
            stale_after=timedelta(minutes=2),
            loaded=False,
            zone=SYDNEY_ZONE,
            city=SYDNEY,
        ),
    )
    assert result.empty == 'Loading timetable'


def test_view_without_a_destination_has_none_and_a_plain_empty() -> None:
    """No destination gives None and an empty message naming none."""
    result = build_stop_view(
        inputs=StopInputs(
            origin='Hollis St',
            destination=None,
            departures=(),
            now=START,
            scroll=0,
            live_since=START,
            has_feed=True,
            stale_after=timedelta(minutes=2),
            zone=SYDNEY_ZONE,
            city=SYDNEY,
        ),
    )
    assert result.destination is None
    assert result.empty == 'No services in the next 3 hours'
    assert 'direction' not in result.empty


def test_tag_with_nickname() -> None:
    """A nickname replaces 'pin' in a pinned tag; unpinned ignores it."""
    tag = pin_tag(index=1, total=3, nickname='Work')
    assert tag == PinTag(text='Work 2/3', pinned=True)
    assert pin_tag(index=1, total=3, nickname=None).text == 'pin 2/3'
    assert pin_tag(index=None, total=3, nickname='Work') == NOT_PINNED


def test_view_carries_its_tag() -> None:
    """The inputs' tag reaches the view; the default is not pinned."""
    assert view().tag == NOT_PINNED
    tag = pin_tag(index=0, total=2)
    result = build_stop_view(
        inputs=StopInputs(
            origin='A',
            destination='B',
            departures=(),
            now=START,
            scroll=0,
            live_since=START,
            has_feed=True,
            stale_after=timedelta(minutes=2),
            tag=tag,
            zone=SYDNEY_ZONE,
            city=SYDNEY,
        ),
    )
    assert result.tag == tag


def test_view_empty_is_none_with_departures() -> None:
    """The empty message only appears when there is nothing to show."""
    assert view(departures=(live(minutes=5),)).empty is None
