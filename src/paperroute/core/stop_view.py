"""The Stop view's content: next service, then the list."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from paperroute.core.live import STALE_AFTER, LiveDeparture
from paperroute.core.model import SYDNEY, Mode, shifted

VISIBLE_ROWS: Final[int] = 8
ROWS_UNDER_BANNER: Final[int] = 7
HORIZON: Final[timedelta] = timedelta(hours=3)
HORIZON_TEXT: Final[str] = f'the next {HORIZON // timedelta(hours=1)} hours'
DOT: Final[str] = ' \u00b7 '


class Pill(StrEnum):
    """Row markers, in priority order."""

    CANCELLED = 'CANCELLED'
    FASTER = 'FASTER'


@dataclass(frozen=True)
class Row:
    """One departure as shown."""

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


@dataclass(frozen=True)
class PinTag:
    """The header's pin tag: its text and whether it marks a pinned place."""

    text: str
    pinned: bool


NOT_PINNED: Final[PinTag] = PinTag(text='not pinned', pinned=False)


def pin_tag(*, index: int | None, total: int) -> PinTag:
    """
    Make the header tag for a place.

    Parameters
    ----------
    index : int | None
        Zero-based position among the pins, or None when not pinned.
    total : int
        How many pins there are.

    Returns
    -------
    PinTag
        'pin 2/3' style tag when pinned, else NOT_PINNED.
    """
    if index is None:
        return NOT_PINNED
    return PinTag(text=f'pin {index + 1}/{total}', pinned=True)


@dataclass(frozen=True)
class StopView:
    """Everything the Stop view draws."""

    origin: str
    destination: str | None
    clock: str
    hero: Row | None
    rows: tuple[Row, ...]
    banner: str | None
    empty: str | None
    scroll: int
    tag: PinTag = NOT_PINNED


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
    minutes = int((departs.timestamp() - now.timestamp()) // 60)
    if minutes <= 0:
        return 'now'
    return str(minutes) if minutes < 100 else clock_text(moment=departs)


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
    ride_s = item.arrives.timestamp() - item.departs.timestamp()
    return Row(
        route=dep.route,
        mode=dep.mode,
        platform=dep.platform,
        destination=dep.headsign.removesuffix(' Station'),
        countdown=countdown(departs=item.departs, now=now),
        leaves_at=clock_text(moment=item.departs),
        arrives_at=clock_text(moment=item.arrives),
        ride_minutes=int(ride_s // 60),
        live=item.live,
        pill=pill,
    )


def pills(*, items: Sequence[LiveDeparture]) -> list[Pill | None]:
    """
    Mark each departure CANCELLED, FASTER or nothing.

    FASTER means it arrives before the service leaving just ahead of it. M1
    pill priority is CANCELLED > FASTER; EXTRA and CHANGED come later.

    Parameters
    ----------
    items : Sequence[LiveDeparture]
        Departures in order.

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
        faster = (
            ahead is not None
            and item.arrives.timestamp() < ahead.arrives.timestamp()
        )
        result.append(Pill.FASTER if faster else None)
        ahead = item
    return result


def banner(
    *, live_since: datetime | None, now: datetime, has_feed: bool
) -> str | None:
    """
    Choose the banner about live data, if one is needed.

    Parameters
    ----------
    live_since : datetime | None
        Fetch time of the oldest needed snapshot, or None when one is missing.
    now : datetime
        Current time.
    has_feed : bool
        Whether the pin's services have a live feed.

    Returns
    -------
    str | None
        Banner text.
    """
    if not has_feed:
        return f'Timetable times{DOT}no live feed for this service'
    if live_since is None:
        return f'Timetable times{DOT}no live data yet'
    age = now.timestamp() - live_since.timestamp()
    if age > STALE_AFTER.total_seconds():
        return f'No live data since {clock_text(moment=live_since)}'
    return None


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


def empty_text(*, destination: str | None, loaded: bool) -> str:
    """
    Say why the list is empty.

    Parameters
    ----------
    destination : str | None
        Destination name, or None when the place has none.
    loaded : bool
        Whether the timetable has been read yet.

    Returns
    -------
    str
        Message.
    """
    if not loaded:
        return 'Loading timetable'
    if destination is None:
        return f'No services in {HORIZON_TEXT}'
    return f'No direct services to {destination} in {HORIZON_TEXT}'


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
        banner.
    """
    now = inputs.now
    upcoming = in_view(departures=inputs.departures, now=now)
    rows = [
        to_row(item=item, now=now, pill=pill)
        for item, pill in zip(upcoming, pills(items=upcoming), strict=True)
    ]
    lead = next(
        (i for i, row in enumerate(rows) if row.pill is not Pill.CANCELLED),
        None,
    )
    rest = [row for i, row in enumerate(rows) if i != lead]
    text = banner(
        live_since=inputs.live_since, now=now, has_feed=inputs.has_feed
    )
    shown = ROWS_UNDER_BANNER if text else VISIBLE_ROWS
    offset = max(0, min(inputs.scroll, len(rest) - shown))
    return StopView(
        origin=inputs.origin,
        destination=inputs.destination,
        clock=clock_text(moment=now),
        hero=None if lead is None else rows[lead],
        rows=tuple(rest[offset : offset + shown]),
        banner=text,
        empty=None if rows else empty_text(
            destination=inputs.destination, loaded=inputs.loaded
        ),
        scroll=offset,
        tag=inputs.tag,
    )
