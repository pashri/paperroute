"""Tests for the Stop view view-model."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

from paperroute.core.alert_model import AlertBanner
from paperroute.core.banners import NO_WIFI
from paperroute.core.live import LiveDeparture
from paperroute.core.model import SYDNEY, CarryOn, Mode, TripFacts
from paperroute.core.stop_view import (
    DOT,
    HORIZON,
    LARGE_ROWS_UNDER_BANNER,
    LARGE_VISIBLE_ROWS,
    NOT_PINNED,
    ROWS_UNDER_BANNER,
    VISIBLE_ROWS,
    Pill,
    PinTag,
    Row,
    StopInputs,
    StopView,
    arrival_line,
    build_stop_view,
    clock_text,
    pin_tag,
    to_row,
)
from tests.core.alert_factories import WESTERN, XPLORER
from tests.core.factories import START, departure


def live(
    *,
    minutes: int,
    ride: int = 30,
    cancelled: bool = False,
    is_live: bool = True,
    late: int = 0,
    mode: Mode = Mode.BUS,
    facts: TripFacts = TripFacts(),
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
    late : int
        Minutes the live times run behind the timetable.
    mode : Mode
        Mode.
    facts : TripFacts
        What is known about the trip.

    Returns
    -------
    LiveDeparture
        Departure.
    """
    dep = replace(
        departure(
            departs=START + timedelta(minutes=minutes),
            ride=ride,
            mode=mode,
        ),
        facts=facts,
    )
    delay = timedelta(minutes=late)
    return LiveDeparture(
        departure=dep,
        departs=dep.departs + delay,
        arrives=dep.arrives + delay,
        live=is_live,
        cancelled=cancelled,
    )


def view(
    *,
    departures: tuple[LiveDeparture, ...] = (),
    scroll: int = 0,
    age: timedelta | None = timedelta(seconds=5),
    has_feed: bool = True,
    destination: str | None = 'Parramatta Station',
    large: bool = False,
    alert: AlertBanner | None = None,
    notice: str | None = None,
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
    destination : str | None
        Destination name, or None for none.
    large : bool
        Whether rows use larger text.
    alert : AlertBanner | None
        The pin's alert line, if any.
    notice : str | None
        A device warning, if any.

    Returns
    -------
    StopView
        View.
    """
    return build_stop_view(
        inputs=StopInputs(
            origin='Hollis St',
            destination=destination,
            departures=departures,
            now=START,
            scroll=scroll,
            live_since=None if age is None else START - age,
            has_feed=has_feed,
            large=large,
            alert=alert,
            notice=notice,
        ),
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
    assert hero(result=result).far


def test_view_countdown_99_minutes_stays_minutes() -> None:
    """99 minutes still counts down."""
    result = view(departures=(live(minutes=99),))
    assert hero(result=result).countdown == '99'
    assert not hero(result=result).far


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
        inputs=StopInputs(
            origin='Hollis St',
            destination='Parramatta Station',
            departures=(),
            now=START,
            scroll=0,
            live_since=START,
            has_feed=True,
            loaded=False,
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
            tag=tag,
        ),
    )
    assert result.tag == tag


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
        inputs=StopInputs(
            origin='A',
            destination='B',
            departures=departures,
            now=now,
            scroll=0,
            live_since=now,
            has_feed=True,
        ),
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


def test_no_destination_has_no_hero_and_lists_every_departure() -> None:
    """A train, a bus and a light rail departure all show, in order."""
    result = view(
        departures=(
            live(minutes=9, mode=Mode.LIGHT_RAIL),
            live(minutes=3, mode=Mode.TRAIN),
            live(minutes=6),
        ),
        destination=None,
    )
    assert result.hero is None
    assert [row.countdown for row in result.rows] == ['3', '6', '9']
    assert [row.mode for row in result.rows] == [
        Mode.TRAIN,
        Mode.BUS,
        Mode.LIGHT_RAIL,
    ]
    assert result.empty is None


def test_no_destination_scroll_clamps() -> None:
    """Scrolling past the end shows the last full page; 0 is the first."""
    first = view(departures=many(), destination=None)
    assert len(first.rows) == VISIBLE_ROWS
    assert first.rows[0].countdown == 'now'
    past = view(departures=many(), destination=None, scroll=99)
    assert past.scroll == len(many()) - VISIBLE_ROWS
    assert past.rows[-1].leaves_at == '8:56'
    under = view(departures=many(), destination=None, scroll=99, age=None)
    assert len(under.rows) == ROWS_UNDER_BANNER
    assert under.scroll == len(many()) - ROWS_UNDER_BANNER


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
    assert [row.pill for row in result.rows] == [Pill.CANCELLED, None, None]


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
                mode=Mode.TRAIN,
                facts=TripFacts(then=CarryOn(route='T9', headsign='Hornsby')),
            ),
        ),
        destination=None,
    )
    assert result.rows[0].then is not None
    assert result.rows[0].then.route == 'T9'


def test_row_for_carry_on() -> None:
    """A carry-on row names the route it becomes and where that goes."""
    onto = CarryOn(route='T2', headsign='Leppington Station')
    item = live(minutes=3, ride=40, facts=TripFacts(onto=onto))
    row = to_row(item=item, now=START, pill=None)
    assert row.onto == 'T2'
    assert row.route == '606'
    assert row.destination == 'Leppington'
    assert row.arrives_at == clock_text(moment=item.arrives)
    same = CarryOn(route='606', headsign='Revesby Station')
    row = to_row(
        item=live(minutes=3, facts=TripFacts(onto=same)), now=START, pill=None,
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
    assert far.scroll == len(items) - 6
    assert far.large


ACTIVE: Final[AlertBanner] = AlertBanner(
    text=WESTERN.header, active=True, more=2,
)
COMING: Final[AlertBanner] = AlertBanner(
    text=f'From Sat 6:00{DOT}{XPLORER.header}', active=False, more=0,
)
NO_FEED: Final[str] = f'Timetable times{DOT}no live feed for this service'


def test_device_warning_beats_alert() -> None:
    """A device warning keeps the banner, and no pill shows."""
    result = view(departures=(live(minutes=5),), notice=NO_WIFI, alert=ACTIVE)
    assert result.banner == NO_WIFI
    assert result.more == 0
    assert not result.planned


def test_stale_line_beats_alert() -> None:
    """Live data three minutes old outranks an alert."""
    result = view(
        departures=(live(minutes=5),),
        age=timedelta(minutes=3),
        alert=ACTIVE,
    )
    assert result.banner is not None
    assert result.banner.startswith('No live data since')
    assert result.more == 0


def test_alert_beats_no_live_feed_line() -> None:
    """The permanent no-feed line gives way to an alert, but not to none."""
    result = view(
        departures=(live(minutes=5),), has_feed=False, alert=ACTIVE,
    )
    assert result.banner == WESTERN.header
    assert result.warning
    result = view(departures=(live(minutes=5),), has_feed=False)
    assert result.banner == NO_FEED


def test_no_live_data_yet_beats_alert() -> None:
    """With no live data fetched yet, that line outranks an alert."""
    result = view(departures=(live(minutes=5),), age=None, alert=ACTIVE)
    assert result.banner == f'Timetable times{DOT}no live data yet'


def test_active_alert_banner() -> None:
    """An active alert is a warning banner with its pill count."""
    result = view(departures=(live(minutes=5),), alert=ACTIVE)
    assert result.banner == WESTERN.header
    assert result.warning
    assert result.more == 2
    assert not result.planned


def test_coming_alert_outlined() -> None:
    """A coming alert is outlined, not a warning."""
    result = view(departures=(live(minutes=5),), alert=COMING)
    assert result.banner == COMING.text
    assert result.planned
    assert not result.warning


def test_alert_banner_shortens_list() -> None:
    """An alert banner takes a row's height."""
    result = view(departures=many(), alert=ACTIVE)
    assert len(result.rows) == ROWS_UNDER_BANNER
