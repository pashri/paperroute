"""Auto pin state and the occurrences of pins' windows."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

from paperroute_transit.core.model import AutoWindow, Pin, pin_ref


@dataclass(frozen=True)
class Occurrence:
    """
    One run of a pin's window.

    Attributes
    ----------
    ref : str
        `pin_ref` of the pin.
    start : datetime
        When it starts.
    end : datetime
        When it ends.
    """

    ref: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Before:
    """
    The place shown before a window started.

    Attributes
    ----------
    place : Pin
        Place, with no windows.
    pinned : bool
        True when it was a pin rather than the unpinned place.
    """

    place: Pin
    pinned: bool


@dataclass(frozen=True)
class AutoState:
    """
    What the auto pin rules have done.

    Attributes
    ----------
    occurrence : Occurrence | None
        Occurrence whose start was last applied, or None.
    before : Before | None
        Place to go back to when the window ends, or None.
    overridden_at : datetime | None
        When the owner moved away from the auto pin, or None.
    """

    occurrence: Occurrence | None = None
    before: Before | None = None
    overridden_at: datetime | None = None


def occurrence_of(
    *, window: AutoWindow, day: date, zone: tzinfo,
) -> tuple[datetime, datetime] | None:
    """
    Find when a window runs that starts on a day.

    Parameters
    ----------
    window : AutoWindow
        Window.
    day : date
        Day it would start.
    zone : tzinfo
        Local time zone the window's times are in.

    Returns
    -------
    tuple[datetime, datetime] | None
        Start and end in UTC, or None when the window doesn't start on
        that day. Datetimes in the same zone compare by wall clock, so
        UTC keeps a daylight-saving day right: a time in the skipped hour
        runs an hour later, and one in the repeated hour runs once.
    """
    if day.weekday() not in window.days:
        return None
    end_day = day + timedelta(days=1) if window.end < window.start else day
    return (
        datetime.combine(day, window.start, tzinfo=zone).astimezone(UTC),
        datetime.combine(end_day, window.end, tzinfo=zone).astimezone(UTC),
    )


def candidates(
    *, pins: Sequence[Pin], now: datetime, zone: tzinfo,
) -> Iterator[Occurrence]:
    """
    List the occurrences running at a moment, in pin order.

    Parameters
    ----------
    pins : Sequence[Pin]
        Pins in knob order.
    now : datetime
        Aware time.
    zone : tzinfo
        Local time zone the windows' times are in.

    Yields
    ------
    Occurrence
        Each occurrence with `start <= now < end`.
    """
    today = now.astimezone(zone).date()
    days = (today - timedelta(days=1), today)
    for pin in pins:
        for window in pin.windows:
            for day in days:
                found = occurrence_of(window=window, day=day, zone=zone)
                if found is not None and found[0] <= now < found[1]:
                    yield Occurrence(
                        ref=pin_ref(pin=pin), start=found[0], end=found[1],
                    )


def running(
    *, pins: Sequence[Pin], now: datetime, zone: tzinfo,
) -> Occurrence | None:
    """
    Find the occurrence running at a moment.

    Parameters
    ----------
    pins : Sequence[Pin]
        Pins in knob order.
    now : datetime
        Aware time.
    zone : tzinfo
        Local time zone the windows' times are in.

    Returns
    -------
    Occurrence | None
        Running occurrence with the latest start, the earlier pin on a
        tie; None when nothing runs.
    """
    best: Occurrence | None = None
    for found in candidates(pins=pins, now=now, zone=zone):
        if best is None or found.start > best.start:
            best = found
    return best


def holds_awake(*, auto: AutoState) -> bool:
    """
    Say whether the auto pin rules keep the panel awake.

    Parameters
    ----------
    auto : AutoState
        Auto pin state.

    Returns
    -------
    bool
        True while an occurrence is recorded.
    """
    return auto.occurrence is not None
