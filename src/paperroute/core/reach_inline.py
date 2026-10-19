"""Put stations an express skips back on the line between its stops."""

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

type Succ = Mapping[str, Mapping[str, int]]

DETOUR_SHARE: Final[float] = 0.25  # of the express link's trips
MAX_SKIPPED: Final[int] = 6


@dataclass(frozen=True)
class Detour:
    """
    A way round between two neighbouring stations of a line.

    Attributes
    ----------
    stations : tuple[str, ...]
        Stations between the two, in travel order.
    floor : float
        Fewest trips on any of the way's edges.
    """

    stations: tuple[str, ...]
    floor: float


@dataclass(frozen=True)
class Gap:
    """
    Two neighbouring stations of a line and what may go between them.

    Attributes
    ----------
    succ : Succ
        Trips by station and next station.
    end : str
        Station the way round comes back to.
    placed : set[str]
        Stations already laid out, which a way round can't pass.
    """

    succ: Succ
    end: str
    placed: set[str]


def detours(*, gap: Gap, here: str, path: tuple[str, ...], low: float
            ) -> Iterator[Detour]:
    """
    List every way round from a station back to the gap's end.

    Parameters
    ----------
    gap : Gap
        The gap and its graph.
    here : str
        Station reached so far.
    path : tuple[str, ...]
        Stations passed since the gap's start.
    low : float
        Fewest trips on the edges so far.

    Yields
    ------
    Detour
        Ways round through at least one and at most MAX_SKIPPED stations
        not laid out.
    """
    for there, trips in gap.succ.get(here, {}).items():
        floor = min(low, trips)
        if there == gap.end and path:
            yield Detour(stations=path, floor=floor)
        elif (
            there not in gap.placed
            and there not in path
            and there != gap.end
            and len(path) < MAX_SKIPPED
        ):
            yield from detours(
                gap=gap, here=there, path=(*path, there), low=floor,
            )


def best_detour(*, succ: Succ, start: str, end: str, placed: set[str]
                ) -> tuple[str, ...]:
    """
    Find the busiest way round between two neighbouring stations.

    Parameters
    ----------
    succ : Succ
        Trips by station and next station.
    start : str
        Station the line leaves.
    end : str
        The line's next station.
    placed : set[str]
        Stations already laid out.

    Returns
    -------
    tuple[str, ...]
        The stations of the way round with the most trips on its quietest
        edge, when that is at least DETOUR_SHARE of the direct link's
        trips; empty when there is none.
    """
    need = DETOUR_SHARE * succ[start][end]
    found = [
        each
        for each in detours(
            gap=Gap(succ=succ, end=end, placed=placed),
            here=start,
            path=(),
            low=math.inf,
        )
        if each.floor >= need
    ]
    return max(found, key=lambda each: each.floor).stations if found else ()


def inlined(*, succ: Succ, chain: Sequence[str], placed: set[str]
            ) -> tuple[str, ...]:
    """
    Put the stations each link of a line skips back between its ends.

    Parameters
    ----------
    succ : Succ
        Trips by station and next station.
    chain : Sequence[str]
        The line's stations in travel order, each linked to the next.
    placed : set[str]
        Stations already laid out; the ones put inline are added.

    Returns
    -------
    tuple[str, ...]
        The line with every busy enough way round of at most MAX_SKIPPED
        stations between neighbours put inline, in travel order.
    """
    line = list(chain)
    index = 0
    while index < len(line) - 1:
        found = best_detour(
            succ=succ, start=line[index], end=line[index + 1], placed=placed,
        )
        line[index + 1:index + 1] = found
        placed.update(found)
        if not found:
            index += 1
    return tuple(line)
