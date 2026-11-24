"""The route gauge's line: earlier stops, the From, calls and the end."""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute_transit.core.gauge import call_stops
from paperroute_transit.core.gauge_buses import GaugeBus
from paperroute_transit.core.gauge_line import (
    BREAK_SLOTS,
    GaugeBreak,
    GaugeLine,
    GaugeMark,
    MarkKind,
    mark_kind,
    street_runs,
)
from paperroute_transit.core.names import NameRules, base_name
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.stop_under import GaugeCall

FROM: Final[int] = -1  # call position of the From


@dataclass(frozen=True)
class RouteGauge:
    """
    What the route gauge draws, before it is laid out.

    Attributes
    ----------
    pattern : TripPattern
        The hero trip.
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.
    calls : tuple[GaugeCall, ...]
        Marks after the From.
    buses : tuple[GaugeBus, ...]
        Buses on the route.
    words : str
        Where the hero's vehicle is, or ''.
    """

    pattern: TripPattern
    start: int
    end: int | None
    calls: tuple[GaugeCall, ...]
    buses: tuple[GaugeBus, ...]
    words: str = ''


def laid(
    *, named: Sequence[int],
) -> tuple[dict[int, int], list[GaugeBreak]]:
    """
    Give named indices slots, with breaks over long gaps.

    A gap of up to ``BREAK_SLOTS + 1`` fills with the indices between;
    a longer one becomes a break.

    Parameters
    ----------
    named : Sequence[int]
        Rising indices to name.

    Returns
    -------
    tuple[dict[int, int], list[GaugeBreak]]
        The slot of each index shown and the breaks.
    """
    slots: dict[int, int] = {}
    gaps: list[GaugeBreak] = []
    slot = 0
    for here, after in zip(named, named[1:]):
        slots[here] = slot
        if after - here <= BREAK_SLOTS + 1:
            slots.update({
                index: slot + index - here
                for index in range(here + 1, after)
            })
            slot += after - here
        else:
            gaps.append(GaugeBreak(
                first=slot,
                last=slot + BREAK_SLOTS + 1,
                count=after - here - 1,
            ))
            slot += BREAK_SLOTS + 1
    if named:
        slots[named[-1]] = slot
    return slots, gaps


def fits(*, named: Sequence[int], budget: int) -> bool:
    """
    Tell whether named indices fit in slots with at most one break.

    Parameters
    ----------
    named : Sequence[int]
        Rising indices to name.
    budget : int
        Last slot the layout may take.

    Returns
    -------
    bool
        True when they fit.
    """
    slots, gaps = laid(named=named)
    return max(slots.values()) <= budget and len(gaps) <= 1


def left_named(
    *, start: int, behind: Sequence[float], budget: int,
) -> tuple[tuple[int, ...], bool]:
    """
    Choose the stops named left of and including the From.

    The stops the buses behind the From last left are kept, nearest the
    From first, while they fit; stop 0 is added if it still fits.

    Parameters
    ----------
    start : int
        Position of the From among the stops the trip calls at.
    behind : Sequence[float]
        Positions, among those stops, of buses behind the From.
    budget : int
        Last slot the left end may take.

    Returns
    -------
    tuple[tuple[int, ...], bool]
        Positions shown, and True when an arrow ends the line.
    """
    floors = sorted(
        {int(spot) for spot in behind if spot < start},
        reverse=True,
    )
    named = [start]
    for stop in [*floors, 0]:
        trial = sorted({*named, stop})
        if fits(named=trial, budget=budget):
            named = trial
    slots, _ = laid(named=named)
    return tuple(sorted(slots)), min(named) > 0


def right_named(
    *, calls: int, place: int | None, room: int,
) -> tuple[list[int], bool]:
    """
    Choose the calls named right of the From.

    Parameters
    ----------
    calls : int
        Calls after the From.
    place : int | None
        Index of the Destination among the calls.
    room : int
        Slots right of the From.

    Returns
    -------
    tuple[list[int], bool]
        Call positions shown, the From as ``FROM``, and True when calls
        are left off the end.
    """
    last = calls - 1
    kept = max(0, room - BREAK_SLOTS - 1)
    if place is None and calls <= room:
        return list(range(FROM, calls)), False
    whole = sorted({*range(FROM, (place or 0) + 1), last})
    if place is not None and max(laid(named=whole)[0].values()) <= room:
        return whole, False
    if room <= BREAK_SLOTS:
        return list(range(FROM, room)), True
    if place is None:
        return [*range(FROM, kept), last], False
    return [*range(FROM, min(kept, place)), place], place < last


def with_place(*, slots: dict[int, int], place: int | None) -> dict[int, int]:
    """
    Give the Destination the slot of the last call shown, if it is missing.

    Parameters
    ----------
    slots : dict[int, int]
        Slot of each call position shown, counted from the From.
    place : int | None
        Index of the Destination among the calls.

    Returns
    -------
    dict[int, int]
        The slots, with the Destination in place of the last call.
    """
    if place is None or place in slots:
        return slots
    last = max(slots)
    return {
        **{index: slot for index, slot in slots.items() if index != last},
        place: slots[last],
    }


def left_marks(
    *, pattern: TripPattern, slots: dict[int, int], start: int,
) -> list[GaugeMark]:
    """
    Make the marks left of and including the From.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    slots : dict[int, int]
        Slot of each pattern index shown.
    start : int
        Pattern index of the From.

    Returns
    -------
    list[GaugeMark]
        Marks in slot order.
    """
    return [
        GaugeMark(
            slot=slot,
            kind=MarkKind.FROM if index == start else MarkKind.BEHIND,
            name=base_name(name=pattern.stops[index].name),
        )
        for index, slot in sorted(slots.items())
    ]


def right_marks(
    *,
    calls: tuple[GaugeCall, ...],
    slots: dict[int, int],
    place: int | None,
    offset: int,
) -> list[GaugeMark]:
    """
    Make the marks right of the From.

    Parameters
    ----------
    calls : tuple[GaugeCall, ...]
        Marks after the From.
    slots : dict[int, int]
        Slot of each call position shown, counted from the From.
    place : int | None
        Index of the Destination among the calls.
    offset : int
        Slot of the From.

    Returns
    -------
    list[GaugeMark]
        Marks in slot order.
    """
    return [
        GaugeMark(
            slot=offset + slot,
            kind=mark_kind(index=index, end=place),
            name=calls[index].name,
            minutes=calls[index].minutes,
        )
        for index, slot in sorted(slots.items())
        if index != FROM
    ]


def shifted(*, gap: GaugeBreak | None, by: int) -> GaugeBreak | None:
    """
    Move a break along the line.

    Parameters
    ----------
    gap : GaugeBreak | None
        The break.
    by : int
        Slots to move it.

    Returns
    -------
    GaugeBreak | None
        The moved break, or None.
    """
    if gap is None:
        return None
    return GaugeBreak(
        first=gap.first + by, last=gap.last + by, count=gap.count,
    )


def left_end(
    *,
    pattern: TripPattern,
    start: int,
    buses: Sequence[GaugeBus],
    budget: int,
) -> GaugeLine:
    """
    Lay out the line from its left end to the From.

    Parameters
    ----------
    pattern : TripPattern
        The hero trip.
    start : int
        Pattern index of the From.
    buses : Sequence[GaugeBus]
        Buses on the route.
    budget : int
        Slots left of the From.

    Returns
    -------
    GaugeLine
        The left marks, the left break and the arrow flag.
    """
    calling = call_stops(pattern=pattern, start=-1, end=start)
    calling = calling[:calling.index(start) + 1]
    behind = [
        float(bisect_right(calling, bus.spot) - 1)
        for bus in buses
        if calling[0] <= bus.spot < start
    ]
    named, cut = left_named(
        start=len(calling) - 1,
        behind=behind,
        budget=budget,
    )
    slots, gaps = laid(named=named)
    shift = int(cut)
    marks = left_marks(
        pattern=pattern,
        slots={
            calling[index]: slot + shift
            for index, slot in slots.items()
        },
        start=start,
    )
    return GaugeLine(
        marks=tuple(marks),
        back=shifted(gap=gaps[0] if gaps else None, by=shift),
        starts=not cut,
        stops=tuple(calling[index] for index in sorted(slots)),
        cut=cut,
    )


def right_end(
    *,
    line: GaugeLine,
    pattern: TripPattern,
    start: int,
    end: int | None,
    calls: tuple[GaugeCall, ...],
    room: int,
) -> GaugeLine:
    """
    Add the calls right of the From to a line.

    Parameters
    ----------
    line : GaugeLine
        The line up to and including the From.
    pattern : TripPattern
        The hero trip.
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.
    calls : tuple[GaugeCall, ...]
        Marks after the From.
    room : int
        Slots right of the From.

    Returns
    -------
    GaugeLine
        The whole line, before street runs.
    """
    indices = call_stops(pattern=pattern, start=start, end=end)
    place = indices.index(end) if end in indices else None
    shown, arrow = right_named(calls=len(calls), place=place, room=room)
    slots, gaps = laid(named=shown)
    slots = with_place(slots=slots, place=place)
    origin = line.marks[-1].slot
    marks = right_marks(calls=calls, slots=slots, place=place, offset=origin)
    return replace(
        line,
        marks=(*line.marks, *marks),
        ahead=shifted(gap=gaps[0] if gaps else None, by=origin),
        ride=None if place is None else (origin, origin + slots[place]),
        arrow=arrow,
        stops=(
            *line.stops,
            *(indices[index] for index in sorted(slots)[1:]),
        ),
    )


def route_line(
    *,
    pattern: TripPattern,
    start: int,
    end: int | None,
    calls: tuple[GaugeCall, ...],
    buses: Sequence[GaugeBus],
    budget: int,
    room: int,
    rules: NameRules,
) -> GaugeLine:
    """
    Lay the route gauge out in slots.

    Parameters
    ----------
    pattern : TripPattern
        The hero trip.
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.
    calls : tuple[GaugeCall, ...]
        Marks after the From, as `gauge_calls` gives them.
    buses : Sequence[GaugeBus]
        Buses on the route.
    budget : int
        Slots left of the From.
    room : int
        Slots right of the From.
    rules : NameRules
        Street-name rules.

    Returns
    -------
    GaugeLine
        The marks, breaks, runs and flags.
    """
    line = right_end(
        line=left_end(
            pattern=pattern,
            start=start,
            buses=buses,
            budget=budget,
        ),
        pattern=pattern,
        start=start,
        end=end,
        calls=calls,
        room=room,
    )
    marks, runs = street_runs(marks=line.marks, rules=rules)
    return replace(line, marks=marks, runs=runs)


def slot_of(*, line: GaugeLine, spot: float) -> float:
    """
    Find the slot of a spot along the pattern.

    Between named stops, and inside a break, the slot is linear in the
    spot. Left of the first mark it is the arrow's slot 0 when the line
    is cut, else the first mark's; right of the last mark, the last
    mark's.

    Parameters
    ----------
    line : GaugeLine
        The laid-out line.
    spot : float
        Fractional pattern index.

    Returns
    -------
    float
        The slot.
    """
    pairs = list(zip(line.stops, (mark.slot for mark in line.marks)))
    if spot < pairs[0][0]:
        return 0.0 if line.cut else float(pairs[0][1])
    for (here, low), (after, high) in zip(pairs, pairs[1:]):
        if here <= spot <= after:
            return low + (spot - here) / (after - here) * (high - low)
    return float(pairs[-1][1])


def break_of(*, line: GaugeLine, slot: float) -> GaugeBreak | None:
    """
    Find the break a slot falls inside.

    Parameters
    ----------
    line : GaugeLine
        The laid-out line.
    slot : float
        Position in slots.

    Returns
    -------
    GaugeBreak | None
        The break, or None.
    """
    return next(
        (
            gap
            for gap in (line.back, line.ahead)
            if gap is not None and gap.first < slot < gap.last
        ),
        None,
    )
