"""The rows a strip lays a trip's loops and spurs out as."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from paperroute_transit.core.memo import SameMemo
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.patterns import PatternStop
from paperroute_transit.core.strip_detours import (
    Detour,
    DetourKind,
    DetourPart,
    find_detours,
    pair_name,
    spur_levels,
)

type Shaped = tuple[tuple[int, ...], DetourPart | None]
LAYOUTS: Final[SameMemo[list[Shaped]]] = SameMemo(size=4)


def loop_rows(*, detour: Detour, group: int) -> list[Shaped]:
    """
    Lay a loop out between its two stand rows.

    Parameters
    ----------
    detour : Detour
        The loop.
    group : int
        Its index among the trip's detours.

    Returns
    -------
    list[Shaped]
        The leaving stand, the ring's calls last first, then the stand
        returned to.
    """
    size = detour.last - detour.first - 1
    inside = [
        (
            (index,),
            DetourPart(
                kind=DetourKind.LOOP,
                group=group,
                slot=index - detour.first - 1,
                size=size,
            ),
        )
        for index in range(detour.last - 1, detour.first, -1)
    ]
    return [((detour.first,), None), *inside, ((detour.last,), None)]


def spur_rows(*, detour: Detour, group: int,
              stops: Sequence[PatternStop]) -> list[Shaped]:
    """
    Fold a spur's out and back calls into one row a level.

    Parameters
    ----------
    detour : Detour
        The spur.
    group : int
        Its index among the trip's detours.
    stops : Sequence[PatternStop]
        The trip's calls.

    Returns
    -------
    list[Shaped]
        The junction and each paired level with both calls, then the
        far end with its one or two calls.
    """
    first, last = detour.first, detour.last
    levels = spur_levels(stops=stops[first:last + 1])
    return [
        (
            (first + level, last - level)
            if level < levels
            else tuple(range(first + levels, last - levels + 1)),
            DetourPart(
                kind=DetourKind.SPUR, group=group, slot=level, size=levels,
            ),
        )
        for level in range(levels + 1)
    ]


def shaped_rows(*, stops: Sequence[PatternStop]) -> list[Shaped]:
    """
    Lay a trip's calls out as strip rows.

    Parameters
    ----------
    stops : Sequence[PatternStop]
        The calls in calling order.

    Returns
    -------
    list[Shaped]
        Each row's call indexes, out call first, and its detour part;
        a call outside any detour is a row of its own.
    """
    detours = {detour.first: detour for detour in find_detours(stops=stops)}
    rows: list[Shaped] = []
    index = group = 0
    while index < len(stops):
        detour = detours.get(index)
        if detour is None:
            rows.append(((index,), None))
            index += 1
            continue
        rows += (
            loop_rows(detour=detour, group=group)
            if detour.kind is DetourKind.LOOP
            else spur_rows(detour=detour, group=group, stops=stops)
        )
        index, group = detour.last + 1, group + 1
    return rows


def detour_layout(*, calls: list[tuple[str, PatternStop]]) -> list[Shaped]:
    """
    Lay a strip's followed calls out as rows, remembered per call list.

    Parameters
    ----------
    calls : list[tuple[str, PatternStop]]
        Strip stations and calls below the From.

    Returns
    -------
    list[Shaped]
        The rows of `shaped_rows`.
    """
    held = LAYOUTS.get(inputs=(calls,))
    if held is not None:
        return held
    return LAYOUTS.put(
        inputs=(calls,),
        value=shaped_rows(stops=[stop for _, stop in calls]),
    )


def paired_place(*, out: StopPlace, back: StopPlace | None) -> StopPlace:
    """
    Name the place of a row standing for one or two calls.

    Parameters
    ----------
    out : StopPlace
        The out call's station.
    back : StopPlace | None
        The back call's station, or None.

    Returns
    -------
    StopPlace
        The out station, named by `pair_name` when there is a back one.
    """
    if back is None:
        return out
    return replace(out, name=pair_name(first=out.name, second=back.name))
