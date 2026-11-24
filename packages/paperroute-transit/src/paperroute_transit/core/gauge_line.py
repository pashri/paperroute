"""The gauge's marks in slots, with breaks and shared-street runs."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from paperroute_transit.core.names import NameRules
from paperroute_transit.core.stop_under import StopGauge
from paperroute_transit.core.streets import cross_street, street_of

BREAK_SLOTS: Final[int] = 2  # empty slots a break takes


class MarkKind(StrEnum):
    """What a mark on the gauge stands for."""

    BEHIND = 'behind'
    FROM = 'from'
    CALL = 'call'
    DESTINATION = 'destination'
    AFTER = 'after'


BOLD_KINDS: Final[frozenset[MarkKind]] = frozenset(
    {MarkKind.FROM, MarkKind.DESTINATION},
)


@dataclass(frozen=True)
class GaugeMark:
    """
    One named stop in a slot.

    Attributes
    ----------
    slot : int
        Position counted from the first mark.
    kind : MarkKind
        What the stop is.
    name : str
        Text to draw.
    minutes : str
        Text for under the mark.
    """

    slot: int
    kind: MarkKind
    name: str
    minutes: str = ''


@dataclass(frozen=True)
class GaugeBreak:
    """
    A gap standing for skipped stops.

    Attributes
    ----------
    first : int
        Slot of the mark before the gap.
    last : int
        Slot of the mark after the gap.
    count : int
        Stops hidden.
    """

    first: int
    last: int
    count: int


@dataclass(frozen=True)
class GaugeRun:
    """
    Adjacent marks on one street.

    Attributes
    ----------
    street : str
        Short street name.
    first : int
        Slot of the first mark.
    last : int
        Slot of the last mark.
    """

    street: str
    first: int
    last: int


@dataclass(frozen=True)
class BusSlot:
    """
    A bus badge, or a count of buses, placed on the line.

    Attributes
    ----------
    slot : float
        Position in slots.
    right : bool | None
        True facing right; on a count, None when the buses differ.
    live : bool | None
        True for a live badge; on a count, None when the buses differ.
    count : int
        Buses in a hollow ``+n`` count badge, or 0 for one bus.
    """

    slot: float
    right: bool | None
    live: bool | None
    count: int = 0


@dataclass(frozen=True)
class GaugeLine:
    """
    The gauge laid out in slots.

    Attributes
    ----------
    marks : tuple[GaugeMark, ...]
        Marks in slot order.
    bus : float | None
        The badge's spot in slots, or None.
    back : GaugeBreak | None
        Break left of the From.
    ahead : GaugeBreak | None
        Break right of the From.
    ride : tuple[int, int] | None
        Slots of the From and the Destination.
    runs : tuple[GaugeRun, ...]
        Shared-street runs.
    starts : bool
        True when the first mark is the trip's first stop.
    arrow : bool
        True when calls are left off the end.
    stops : tuple[int, ...]
        Pattern index at each mark, or empty.
    buses : tuple[BusSlot, ...]
        Buses placed on the line.
    cut : bool
        True when the line ends left in an arrow.
    """

    marks: tuple[GaugeMark, ...]
    bus: float | None = None
    back: GaugeBreak | None = None
    ahead: GaugeBreak | None = None
    ride: tuple[int, int] | None = None
    runs: tuple[GaugeRun, ...] = ()
    starts: bool = False
    arrow: bool = False
    stops: tuple[int, ...] = ()
    buses: tuple[BusSlot, ...] = ()
    cut: bool = False


def behind_marks(
    *, gauge: StopGauge,
) -> tuple[tuple[GaugeMark, ...], GaugeBreak | None]:
    """
    Place the previous stops and the From.

    Parameters
    ----------
    gauge : StopGauge
        The gauge's content.

    Returns
    -------
    tuple[tuple[GaugeMark, ...], GaugeBreak | None]
        The marks and the break between the last previous stop and the
        From, if stops are hidden.
    """
    marks = [
        GaugeMark(slot=slot, kind=MarkKind.BEHIND, name=name)
        for slot, name in enumerate(gauge.behind)
    ]
    slot = len(marks)
    gap = None
    if gauge.hidden and marks:
        slot += BREAK_SLOTS
        gap = GaugeBreak(
            first=slot - BREAK_SLOTS - 1, last=slot, count=gauge.hidden,
        )
    marks.append(GaugeMark(slot=slot, kind=MarkKind.FROM, name=gauge.origin))
    return tuple(marks), gap


def mark_kind(*, index: int, end: int | None) -> MarkKind:
    """
    Name the kind of a call.

    Parameters
    ----------
    index : int
        Index among the calls.
    end : int | None
        Index of the Destination.

    Returns
    -------
    MarkKind
        CALL before the Destination, DESTINATION at it, AFTER past it.
    """
    if end is None or index < end:
        return MarkKind.CALL
    return MarkKind.DESTINATION if index == end else MarkKind.AFTER


def ahead_marks(
    *, gauge: StopGauge, after: int, reach: int, dest_reach: int,
) -> tuple[tuple[GaugeMark, ...], GaugeBreak | None, int]:
    """
    Place the calls after the From.

    Parameters
    ----------
    gauge : StopGauge
        The gauge's content.
    after : int
        Slot of the From.
    reach : int
        Last slot a call may take.
    dest_reach : int
        Last slot the Destination may take.

    Returns
    -------
    tuple[tuple[GaugeMark, ...], GaugeBreak | None, int]
        The marks, the break before the Destination if any, and the
        calls placed or hidden.
    """
    end = gauge.end
    kept = len(gauge.calls)
    if end is not None and after + 1 + end > dest_reach:
        kept = max(0, min(end, dest_reach - after - 1 - BREAK_SLOTS))
    gap = None
    shift = 0
    if end is not None and kept < end:
        shift = BREAK_SLOTS - (end - kept)
        gap = GaugeBreak(
            first=after + kept,
            last=after + kept + 1 + BREAK_SLOTS,
            count=end - kept,
        )
    marks = []
    for index, call in enumerate(gauge.calls):
        if end is not None and gap is not None and kept <= index < end:
            continue
        slot = after + 1 + index + (shift if index >= kept else 0)
        kind = mark_kind(index=index, end=end)
        if kind != MarkKind.DESTINATION and slot > reach:
            continue
        marks.append(GaugeMark(
            slot=slot, kind=kind, name=call.name, minutes=call.minutes,
        ))
    return tuple(marks), gap, len(marks) + (gap.count if gap else 0)


def adjacent(*, marks: Sequence[GaugeMark]) -> list[list[GaugeMark]]:
    """
    Group marks in neighbouring slots.

    Parameters
    ----------
    marks : Sequence[GaugeMark]
        Marks in slot order.

    Returns
    -------
    list[list[GaugeMark]]
        Groups whose slots rise by one.
    """
    groups: list[list[GaugeMark]] = []
    for mark in marks:
        if groups and groups[-1][-1].slot + 1 == mark.slot:
            groups[-1].append(mark)
        else:
            groups.append([mark])
    return groups


def street_runs(
    *, marks: Sequence[GaugeMark], rules: NameRules,
) -> tuple[tuple[GaugeMark, ...], tuple[GaugeRun, ...]]:
    """
    Find neighbouring marks on one street and shorten their names.

    Marks whose cross street matches another mark's in the same group
    keep their whole names and take no part in a run.

    Parameters
    ----------
    marks : Sequence[GaugeMark]
        Marks in slot order.
    rules : NameRules
        The rules.

    Returns
    -------
    tuple[tuple[GaugeMark, ...], tuple[GaugeRun, ...]]
        The marks, renamed where in a run, and the runs.
    """
    streets = {
        mark.slot: street_of(name=mark.name, rules=rules) for mark in marks
    }
    crosses = {
        mark.slot: cross_street(name=mark.name, rules=rules)
        for mark in marks
    }
    renamed: dict[int, GaugeMark] = {}
    runs = []
    for group in adjacent(marks=marks):
        for part in same_street(group=group, streets=streets):
            texts = [crosses[mark.slot] for mark in part]
            keep = [
                mark
                for mark, text in zip(part, texts)
                if texts.count(text) == 1
            ]
            for chunk in adjacent(marks=keep):
                if len(chunk) < 2:
                    continue
                for mark in chunk:
                    renamed[mark.slot] = replace(
                        mark, name=crosses[mark.slot] or mark.name,
                    )
                runs.append(GaugeRun(
                    street=streets[chunk[0].slot] or '',
                    first=chunk[0].slot,
                    last=chunk[-1].slot,
                ))
    return tuple(renamed.get(mark.slot, mark) for mark in marks), tuple(runs)


def same_street(
    *, group: list[GaugeMark], streets: dict[int, str | None],
) -> list[list[GaugeMark]]:
    """
    Split a group where the street changes or is missing.

    Parameters
    ----------
    group : list[GaugeMark]
        Marks in neighbouring slots.
    streets : dict[int, str | None]
        Each slot's street.

    Returns
    -------
    list[list[GaugeMark]]
        Parts that each share one street.
    """
    parts: list[list[GaugeMark]] = []
    for mark in group:
        street = streets[mark.slot]
        if street is None:
            parts.append([])
        elif parts and parts[-1] and streets[parts[-1][-1].slot] == street:
            parts[-1].append(mark)
        else:
            parts.append([mark])
    return [part for part in parts if part]


def gauge_line(
    *, gauge: StopGauge, reach: int, dest_reach: int, rules: NameRules,
) -> GaugeLine:
    """
    Lay a gauge out in slots.

    Parameters
    ----------
    gauge : StopGauge
        The gauge's content.
    reach : int
        Last slot a call may take.
    dest_reach : int
        Last slot the Destination may take.
    rules : NameRules
        The rules.

    Returns
    -------
    GaugeLine
        The marks, breaks, runs and flags.
    """
    behind, back = behind_marks(gauge=gauge)
    after = behind[-1].slot
    dest_reach = min(dest_reach, reach + 1)
    ahead, gap, used = ahead_marks(
        gauge=gauge, after=after, reach=reach, dest_reach=dest_reach,
    )
    marks, runs = street_runs(marks=(*behind, *ahead), rules=rules)
    dest = [m.slot for m in marks if m.kind == MarkKind.DESTINATION]
    return GaugeLine(
        marks=marks,
        bus=gauge.bus,
        back=back,
        ahead=gap,
        ride=(after, dest[0]) if dest else None,
        runs=runs,
        starts=gauge.starts,
        arrow=used < len(gauge.calls),
    )
