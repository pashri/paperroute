"""The Stop view's content: next service, then the list."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from paperroute.core.alert_model import AlertBanner
from paperroute.core.live import STALE_AFTER, LiveDeparture
from paperroute.core.model import SYDNEY, CarryOn, Departure, Mode, shifted

VISIBLE_ROWS: Final[int] = 8
ROWS_UNDER_BANNER: Final[int] = 7
LARGE_VISIBLE_ROWS: Final[int] = 6
LARGE_ROWS_UNDER_BANNER: Final[int] = 5
SIDEBAR_ROWS: Final[int] = 5
FAR_MINUTES: Final[int] = 100  # from here a countdown shows the clock time
HORIZON: Final[timedelta] = timedelta(hours=3)
HORIZON_TEXT: Final[str] = f'the next {HORIZON // timedelta(hours=1)} hours'
DOT: Final[str] = ' \u00b7 '


class Pill(StrEnum):
    """Row markers, in priority order."""

    CANCELLED = 'CANCELLED'
    FASTER = 'FASTER'


@dataclass(frozen=True)
class Row:
    """
    One departure as shown.

    `far` when its countdown is a clock time; `stops` counts the stations
    stopped at up to the Destination, None without one.
    """

    route: str
    mode: Mode
    platform: str
    destination: str
    countdown: str
    leaves_at: str
    arrives_at: str
    ride_minutes: int
    live: bool
    pill: Pill | None
    then: CarryOn | None = None
    far: bool = False
    onto: str | None = None
    stops: int | None = None


def stops_text(*, stops: int) -> str:
    """
    Say how many stops a ride makes.

    Parameters
    ----------
    stops : int
        Stations stopped at.

    Returns
    -------
    str
        Such as '1 stop' or '6 stops'.
    """
    return f'{stops} stop' if stops == 1 else f'{stops} stops'


def arrival_line(*, row: Row) -> str:
    """
    Make a row's small arrival line.

    Parameters
    ----------
    row : Row
        Departure.

    Returns
    -------
    str
        Such as 'arr 8:30', with the stop count after a dot when known.
    """
    if row.stops is None:
        return f'arr {row.arrives_at}'
    return f'arr {row.arrives_at}{DOT}{stops_text(stops=row.stops)}'


@dataclass(frozen=True)
class PinTag:
    """
    The header's pin tag.

    Attributes
    ----------
    text : str
        The tag's text.
    pinned : bool
        Whether it marks a pinned place.
    auto : bool
        Whether an auto pin rule is showing the place.
    """

    text: str
    pinned: bool
    auto: bool = False


NOT_PINNED: Final[PinTag] = PinTag(text='not pinned', pinned=False)


def pin_tag(
    *,
    index: int | None,
    total: int,
    nickname: str | None = None,
    auto: bool = False,
) -> PinTag:
    """
    Make the header tag for a place.

    Parameters
    ----------
    index : int | None
        Zero-based position among the pins, or None when not pinned.
    total : int
        How many pins there are.
    nickname : str | None
        The pin's nickname, shown in place of 'pin'.
    auto : bool
        Whether an auto pin rule is showing the pin.

    Returns
    -------
    PinTag
        'pin 2/3' or 'Work 2/3' style tag when pinned, else NOT_PINNED.
    """
    if index is None:
        return NOT_PINNED
    return PinTag(
        text=f'{nickname or "pin"} {index + 1}/{total}',
        pinned=True,
        auto=auto,
    )


@dataclass(frozen=True)
class StopView:
    """
    Everything the Stop view draws.

    Attributes
    ----------
    head_rows : tuple[Row, ...]
        The hero, if any, then the first rows of the unscrolled list, at
        most `SIDEBAR_ROWS` in all. They do not depend on `scroll`.
    head_departures : tuple[Departure, ...]
        The departures behind `head_rows`, in the same order.
    warning : bool
        True when the banner is a device warning or an active alert,
        drawn inverted.
    more : int
        Other alerts that could have taken the banner, when an alert
        line is the one shown.
    planned : bool
        True when the banner is a coming alert, drawn outlined.
    large : bool
        Whether the departure rows use larger text.
    inverted : bool
        Whether the departure list is drawn white on black.
    """

    origin: str
    destination: str | None
    clock: str
    hero: Row | None
    rows: tuple[Row, ...]
    banner: str | None
    empty: str | None
    scroll: int
    tag: PinTag = NOT_PINNED
    head_rows: tuple[Row, ...] = ()
    head_departures: tuple[Departure, ...] = ()
    warning: bool = False
    large: bool = False
    inverted: bool = False
    more: int = 0
    planned: bool = False


@dataclass(frozen=True)
class StopInputs:
    """
    Everything the Stop view is built from.

    Attributes
    ----------
    origin : str
        From name.
    destination : str | None
        Destination name, or None when the place has none.
    departures : tuple[LiveDeparture, ...]
        Departures in timetable order.
    now : datetime
        Current time.
    scroll : int
        Requested scroll offset.
    live_since : datetime | None
        Fetch time of the oldest needed snapshot, or None when one is
        missing.
    has_feed : bool
        Whether a live feed exists.
    loaded : bool
        Whether the timetable has been read yet; if not, an empty view
        says it is loading rather than that nothing runs.
    tag : PinTag
        The header's pin tag.
    missing : bool
        Whether the timetable no longer knows the place.
    notice : str | None
        A device warning that takes the banner ahead of any about live
        data, or None.
    large : bool
        Whether the departure rows use larger text.
    inverted : bool
        Whether the departure list is drawn white on black.
    alert : AlertBanner | None
        The pin's alert line, which takes the banner ahead of the
        no-feed line only.
    """

    origin: str
    destination: str | None
    departures: tuple[LiveDeparture, ...]
    now: datetime
    scroll: int
    live_since: datetime | None
    has_feed: bool
    loaded: bool = True
    tag: PinTag = NOT_PINNED
    missing: bool = False
    notice: str | None = None
    large: bool = False
    inverted: bool = False
    alert: AlertBanner | None = None


def clock_text(*, moment: datetime) -> str:
    """
    A 24-hour clock time without a leading zero.

    Parameters
    ----------
    moment : datetime
        Time.

    Returns
    -------
    str
        For example '8:05'.
    """
    local = moment.astimezone(SYDNEY)
    return f'{local.hour}:{local.minute:02d}'


def is_far(*, departs: datetime, now: datetime) -> bool:
    """
    Say whether a departure counts down as a clock time.

    Parameters
    ----------
    departs : datetime
        Departure time.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when it is `FAR_MINUTES` or more away.
    """
    return departs.timestamp() - now.timestamp() >= FAR_MINUTES * 60


def countdown(*, departs: datetime, now: datetime) -> str:
    """
    Count down to a departure: minutes, 'now', or the clock time if 100+ away.

    Parameters
    ----------
    departs : datetime
        Departure time.
    now : datetime
        Current time.

    Returns
    -------
    str
        Countdown text.
    """
    if is_far(departs=departs, now=now):
        return clock_text(moment=departs)
    minutes = int((departs.timestamp() - now.timestamp()) // 60)
    return 'now' if minutes <= 0 else str(minutes)


def to_row(*, item: LiveDeparture, now: datetime, pill: Pill | None) -> Row:
    """
    Turn a departure into a row.

    Parameters
    ----------
    item : LiveDeparture
        Departure.
    now : datetime
        Current time.
    pill : Pill | None
        Marker.

    Returns
    -------
    Row
        Row.
    """
    dep = item.departure
    onto = dep.facts.onto
    ride_s = item.arrives.timestamp() - item.departs.timestamp()
    return Row(
        route=dep.route,
        mode=dep.mode,
        platform=dep.platform,
        destination=(onto.headsign if onto else dep.headsign).removesuffix(
            ' Station'
        ),
        countdown=countdown(departs=item.departs, now=now),
        leaves_at=clock_text(moment=item.departs),
        arrives_at=clock_text(moment=item.arrives),
        ride_minutes=int(ride_s // 60),
        live=item.live,
        pill=pill,
        then=dep.facts.then,
        far=is_far(departs=item.departs, now=now),
        onto=onto.route if onto and onto.route != dep.route else None,
        stops=dep.stops,
    )


def pills(
    *, items: Sequence[LiveDeparture], faster: bool,
) -> list[Pill | None]:
    """
    Mark each departure CANCELLED, FASTER or nothing.

    FASTER means it arrives at the Destination before the service leaving
    just ahead of it. M1 pill priority is CANCELLED > FASTER; EXTRA and
    CHANGED come later.

    Parameters
    ----------
    items : Sequence[LiveDeparture]
        Departures in order.
    faster : bool
        Whether FASTER applies: only with a Destination, as without one
        each departure arrives somewhere else.

    Returns
    -------
    list[Pill | None]
        One marker or None per departure.
    """
    result: list[Pill | None] = []
    ahead: LiveDeparture | None = None
    for item in items:
        if item.cancelled:
            result.append(Pill.CANCELLED)
            continue
        beats = (
            faster
            and ahead is not None
            and item.arrives.timestamp() < ahead.arrives.timestamp()
        )
        result.append(Pill.FASTER if beats else None)
        ahead = item
    return result


def live_line(*, live_since: datetime | None, now: datetime) -> str | None:
    """
    Choose the banner about live data going missing, if one is needed.

    Parameters
    ----------
    live_since : datetime | None
        Fetch time of the oldest needed snapshot, or None when one is
        missing.
    now : datetime
        Current time.

    Returns
    -------
    str | None
        Banner text.
    """
    if live_since is None:
        return f'Timetable times{DOT}no live data yet'
    age = now.timestamp() - live_since.timestamp()
    if age > STALE_AFTER.total_seconds():
        return f'No live data since {clock_text(moment=live_since)}'
    return None


def no_feed_line(*, has_feed: bool) -> str | None:
    """
    Say when the pin's services have no live feed.

    Parameters
    ----------
    has_feed : bool
        Whether the pin's services have a live feed.

    Returns
    -------
    str | None
        Banner text, or None when there is a feed.
    """
    if has_feed:
        return None
    return f'Timetable times{DOT}no live feed for this service'


def not_departed(
    *, departures: Sequence[LiveDeparture], now: datetime
) -> list[LiveDeparture]:
    """
    Drop departures that left more than a minute ago, by live time.

    Parameters
    ----------
    departures : Sequence[LiveDeparture]
        Departures in order.
    now : datetime
        Current time.

    Returns
    -------
    list[LiveDeparture]
        The rest, in order.
    """
    cutoff = shifted(moment=now, seconds=-59)
    return [
        item
        for item in departures
        if item.departs.timestamp() >= cutoff.timestamp()
    ]


def in_view(
    *, departures: Sequence[LiveDeparture], now: datetime
) -> list[LiveDeparture]:
    """
    Keep departures leaving by now plus HORIZON, in live order.

    The list is stable-sorted by live departure time, except that a
    cancelled service sorts by its timetabled departure time.

    Parameters
    ----------
    departures : Sequence[LiveDeparture]
        Departures in timetable order.
    now : datetime
        Current time.

    Returns
    -------
    list[LiveDeparture]
        Departures to show, in order.
    """
    limit = (now + HORIZON).timestamp()
    kept = [
        item
        for item in not_departed(departures=departures, now=now)
        if item.departs.timestamp() <= limit
    ]
    return sorted(kept, key=lambda item: order_time(item=item))


def order_time(*, item: LiveDeparture) -> float:
    """
    When a departure sorts in the list: live time, or timetabled if cancelled.

    Parameters
    ----------
    item : LiveDeparture
        Departure.

    Returns
    -------
    float
        POSIX timestamp.
    """
    moment = item.departure.departs if item.cancelled else item.departs
    return moment.timestamp()


def empty_text(
    *, destination: str | None, loaded: bool, missing: bool = False
) -> str:
    """
    Say why the list is empty.

    Parameters
    ----------
    destination : str | None
        Destination name, or None when the place has none.
    loaded : bool
        Whether the timetable has been read yet.
    missing : bool
        Whether the timetable no longer knows the place.

    Returns
    -------
    str
        Message.
    """
    if missing:
        return 'Stop not in the new timetable'
    if not loaded:
        return 'Loading timetable'
    if destination is None:
        return f'No services in {HORIZON_TEXT}'
    return f'No direct services to {destination} in {HORIZON_TEXT}'


def row_count(*, large: bool, under_banner: bool) -> int:
    """
    How many list rows fit.

    Parameters
    ----------
    large : bool
        Whether rows use larger text.
    under_banner : bool
        Whether a banner takes a row's height.

    Returns
    -------
    int
        Rows shown.
    """
    if large:
        return LARGE_ROWS_UNDER_BANNER if under_banner else LARGE_VISIBLE_ROWS
    return ROWS_UNDER_BANNER if under_banner else VISIBLE_ROWS


def alert_shown(*, inputs: StopInputs) -> AlertBanner | None:
    """
    Pick the alert line when nothing outranks it.

    Parameters
    ----------
    inputs : StopInputs
        The view's inputs.

    Returns
    -------
    AlertBanner | None
        The alert, or None when there is none or a device warning or a
        live data line takes the banner. An empty notice counts as none.
    """
    if inputs.notice or inputs.alert is None:
        return None
    if inputs.has_feed and live_line(
        live_since=inputs.live_since, now=inputs.now,
    ):
        return None
    return inputs.alert


def banner_text(*, inputs: StopInputs) -> str | None:
    """
    Choose the banner text.

    The order is a device warning, the live data line, an alert, then
    the no-feed line.

    Parameters
    ----------
    inputs : StopInputs
        The view's inputs.

    Returns
    -------
    str | None
        Banner text, or None.
    """
    if inputs.notice:
        return inputs.notice
    if inputs.has_feed:
        line = live_line(live_since=inputs.live_since, now=inputs.now)
        if line:
            return line
    alert = alert_shown(inputs=inputs)
    if alert is not None:
        return alert.text
    return no_feed_line(has_feed=inputs.has_feed)


def lead_index(*, rows: Sequence[Row], hero: bool) -> int | None:
    """
    Find the row shown as the hero.

    Parameters
    ----------
    rows : Sequence[Row]
        Rows in departure order.
    hero : bool
        True when the view has a hero, which it does with a destination.

    Returns
    -------
    int | None
        Index of the first row not cancelled, or None.
    """
    if not hero:
        return None
    return next(
        (i for i, row in enumerate(rows) if row.pill is not Pill.CANCELLED),
        None,
    )


def head_indexes(*, count: int, lead: int | None) -> list[int]:
    """
    List the indexes of the sidebar rows, the hero first.

    Parameters
    ----------
    count : int
        How many rows there are.
    lead : int | None
        Index of the hero row, or None.

    Returns
    -------
    list[int]
        Up to `SIDEBAR_ROWS` indexes.
    """
    order = [i for i in range(count) if i != lead]
    if lead is not None:
        order.insert(0, lead)
    return order[:SIDEBAR_ROWS]


def build_stop_view(*, inputs: StopInputs) -> StopView:
    """
    Build the Stop view from resolved departures.

    Parameters
    ----------
    inputs : StopInputs
        The view's inputs.

    Returns
    -------
    StopView
        View-model, with scroll clamped to the rows that fit under any
        banner. A notice takes the banner ahead of the live data one.
        With no destination there is no hero: every departure is a row.
    """
    upcoming = in_view(departures=inputs.departures, now=inputs.now)
    rows = [
        to_row(item=item, now=inputs.now, pill=pill)
        for item, pill in zip(
            upcoming,
            pills(items=upcoming, faster=inputs.destination is not None),
            strict=True,
        )
    ]
    lead = lead_index(rows=rows, hero=inputs.destination is not None)
    rest = [row for i, row in enumerate(rows) if i != lead]
    head = head_indexes(count=len(rows), lead=lead)
    text = banner_text(inputs=inputs)
    alert = alert_shown(inputs=inputs)
    shown = row_count(large=inputs.large, under_banner=text is not None)
    offset = max(0, min(inputs.scroll, len(rest) - shown))
    return StopView(
        origin=inputs.origin,
        destination=inputs.destination,
        clock=clock_text(moment=inputs.now),
        hero=None if lead is None else rows[lead],
        rows=tuple(rest[offset : offset + shown]),
        banner=text,
        empty=None if rows else empty_text(
            destination=inputs.destination,
            loaded=inputs.loaded,
            missing=inputs.missing,
        ),
        scroll=offset,
        tag=inputs.tag,
        head_rows=tuple(rows[i] for i in head),
        head_departures=tuple(upcoming[i].departure for i in head),
        warning=inputs.notice is not None
        or (alert is not None and alert.active),
        large=inputs.large,
        inverted=inputs.inverted,
        more=0 if alert is None else alert.more,
        planned=alert is not None and not alert.active,
    )
