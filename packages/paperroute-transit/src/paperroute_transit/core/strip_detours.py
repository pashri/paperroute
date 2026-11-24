"""Short loops and stacked spurs in a trip's calls."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from paperroute_transit.core.memo import SameMemo
from paperroute_transit.core.patterns import PatternStop, TripPattern

type Span = tuple[int, int]

MAX_BETWEEN: Final[int] = 6  # stops strictly between the two visits
MIN_BETWEEN: Final[int] = 2  # stops strictly between for a return to count
NEAR: Final[float] = 200.0  # metres
SHORT_HEAD: Final[int] = 2  # characters
BREAKS: Final[str] = ' ,'  # characters a shared name part may end before


class DetourKind(StrEnum):
    """How a detour is drawn."""

    LOOP = 'loop'
    SPUR = 'spur'


@dataclass(frozen=True)
class Detour:
    """
    A part of a trip that leaves a place and comes back to it.

    Attributes
    ----------
    kind : DetourKind
        Ring or spur.
    first : int
        Index of the call leaving the repeated station or junction.
    last : int
        Index of the call back at it.
    """

    kind: DetourKind
    first: int
    last: int


@dataclass(frozen=True)
class DetourPart:
    """
    Where a strip row sits on a loop or spur.

    Attributes
    ----------
    kind : DetourKind
        Ring or spur.
    group : int
        The detour's index among those on the reach.
    slot : int
        On a loop, the stop's place on the ring in calling order from 0;
        on a spur, its level, 0 for the junction.
    size : int
        On a loop, the stops on the ring; on a spur, its levels.
    back : str
        On a spur row standing for two calls, the back call's station
        ID; else ''.
    """

    kind: DetourKind
    group: int
    slot: int
    size: int
    back: str = ''


DETOURS: Final[SameMemo[tuple[Detour, ...]]] = SameMemo(size=128)


def place_key(*, stop: PatternStop) -> str:
    """
    Give the place a call is at, shared by stands of one place.

    Parameters
    ----------
    stop : PatternStop
        The call.

    Returns
    -------
    str
        The name before its first comma when that is longer than two
        characters, else the station.
    """
    head = stop.name.split(',')[0].strip() if ',' in stop.name else ''
    return f'name:{head}' if len(head) > SHORT_HEAD else stop.station


def apart(*, one: PatternStop, other: PatternStop) -> float:
    """
    Give the distance between two placed calls.

    Parameters
    ----------
    one : PatternStop
        A call with a point.
    other : PatternStop
        Another call with a point.

    Returns
    -------
    float
        Metres; infinite when either has no point.
    """
    if one.point is None or other.point is None:
        return math.inf
    return math.dist(one.point, other.point)


def is_there_and_back(*, stops: Sequence[PatternStop]) -> bool:
    """
    Say whether every return-leg call is near an out-leg call.

    Parameters
    ----------
    stops : Sequence[PatternStop]
        The leaving call to the returning call.

    Returns
    -------
    bool
        True when the second half retraces the first; the middle call of
        an odd loop is the turn and belongs to neither leg.
    """
    half = (len(stops) - 2) // 2
    out = stops[:1 + half]
    back = stops[len(stops) - 1 - half:]
    return all(
        min(apart(one=stop, other=other) for other in out) <= NEAR
        for stop in back
    )


def spur_levels(*, stops: Sequence[PatternStop]) -> int:
    """
    Count a stacked spur's levels.

    Parameters
    ----------
    stops : Sequence[PatternStop]
        The junction call to the call back at it.

    Returns
    -------
    int
        Paired calls past the junction plus one for the far end; 0 when
        a pair working in from the junction is more than `NEAR` apart.
    """
    i, j = 0, len(stops) - 1
    while j - i >= 2:
        if apart(one=stops[i], other=stops[j]) > NEAR:
            return 0
        i, j = i + 1, j - 1
    return i


def spans_of(*, stops: Sequence[PatternStop]) -> list[Span]:
    """
    Find each return to a place since its last visit.

    Parameters
    ----------
    stops : Sequence[PatternStop]
        A trip's calls.

    Returns
    -------
    list[Span]
        Index pairs with at least `MIN_BETWEEN` calls between.
    """
    last: dict[str, int] = {}
    spans = []
    for index, stop in enumerate(stops):
        key = place_key(stop=stop)
        if key in last and index - last[key] - 1 >= MIN_BETWEEN:
            spans.append((last[key], index))
        last[key] = index
    return spans


def candidate(*, span: Span, spans: list[Span], count: int) -> bool:
    """
    Say whether a return is short, mid-trip and holds no other.

    Parameters
    ----------
    span : Span
        The return.
    spans : list[Span]
        Every return on the trip.
    count : int
        Calls on the trip.

    Returns
    -------
    bool
        True when it could be drawn as a ring or a spur.
    """
    first, last = span
    holds = any(
        first <= other[0] and other[1] <= last and other != span
        for other in spans
    )
    return (
        0 < first
        and last < count - 1
        and last - first - 1 <= MAX_BETWEEN
        and not holds
    )


def kind_of(*, stops: Sequence[PatternStop]) -> DetourKind | None:
    """
    Classify a candidate's calls.

    Parameters
    ----------
    stops : Sequence[PatternStop]
        The leaving call to the returning call.

    Returns
    -------
    DetourKind | None
        Loop, spur, or None for plain rows.
    """
    if any(stop.point is None for stop in stops):
        return None
    if not is_there_and_back(stops=stops):
        return DetourKind.LOOP
    return DetourKind.SPUR if spur_levels(stops=stops) >= 2 else None


def find_detours(*, stops: Sequence[PatternStop]) -> tuple[Detour, ...]:
    """
    Find the loops and spurs drawn on a trip.

    Parameters
    ----------
    stops : Sequence[PatternStop]
        A trip's calls.

    Returns
    -------
    tuple[Detour, ...]
        Detours in running order.
    """
    spans = spans_of(stops=stops)
    kinds = [
        (span, kind_of(stops=stops[span[0]:span[1] + 1]))
        for span in spans
        if candidate(span=span, spans=spans, count=len(stops))
    ]
    return tuple(
        Detour(kind=kind, first=first, last=last)
        for (first, last), kind in kinds
        if kind is not None
    )


def detours_of(*, pattern: TripPattern) -> tuple[Detour, ...]:
    """
    Find the loops and spurs drawn on a trip, remembered per pattern.

    Parameters
    ----------
    pattern : TripPattern
        The trip.

    Returns
    -------
    tuple[Detour, ...]
        Detours in running order.
    """
    held = DETOURS.get(inputs=(pattern,))
    if held is not None:
        return held
    return DETOURS.put(
        inputs=(pattern,),
        value=find_detours(stops=pattern.stops),
    )


def pair_name(*, first: str, second: str) -> str:
    """
    Name a row standing for two calls.

    Parameters
    ----------
    first : str
        The out call's name.
    second : str
        The back call's name.

    Returns
    -------
    str
        The longest shared start of the two that ends at a word or a
        comma, trimmed; both joined by ' / ' when they share none.
    """
    if first == second:
        return first
    shared = 0
    for one, other in zip(first, second):
        if one != other:
            break
        shared += 1
    ends = [
        k for k in range(shared + 1)
        if all(k == len(name) or name[k] in BREAKS for name in (first, second))
    ]
    part = first[:max(ends, default=0)].strip(BREAKS)
    return part or f'{first} / {second}'
