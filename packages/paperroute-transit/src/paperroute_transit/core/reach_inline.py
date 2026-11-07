"""Put stations an express skips back on the line between its stops."""

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
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
    limit : int
        Most stations a way round may pass.
    """

    succ: Succ
    end: str
    placed: set[str]
    limit: int = MAX_SKIPPED


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
        Ways round through at least one and at most the gap's limit of
        stations not laid out.
    """
    for there, trips in gap.succ.get(here, {}).items():
        floor = min(low, trips)
        if there == gap.end and path:
            yield Detour(stations=path, floor=floor)
        elif (
            there not in gap.placed
            and there not in path
            and there != gap.end
            and len(path) < gap.limit
        ):
            yield from detours(
                gap=gap, here=there, path=(*path, there), low=floor,
            )


def best_detour(*, succ: Succ, start: str, end: str, placed: set[str],
                limit: int) -> tuple[str, ...]:
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
    limit : int
        Most stations the way round may pass.

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
            gap=Gap(succ=succ, end=end, placed=placed, limit=limit),
            here=start,
            path=(),
            low=math.inf,
        )
        if each.floor >= need
    ]
    return max(found, key=lambda each: each.floor).stations if found else ()


def filled(*, succ: Succ, start: str, end: str, placed: set[str]
           ) -> list[str]:
    """
    Put the stations a link skips back between its ends.

    Parameters
    ----------
    succ : Succ
        Trips by station and next station.
    start : str
        Station the link leaves.
    end : str
        Station it reaches.
    placed : set[str]
        Stations already laid out; the ones put inline are added.

    Returns
    -------
    list[str]
        The link's ends with busy enough ways round put between them,
        and ways round inside those, at most MAX_SKIPPED stations in all.
    """
    part = [start, end]
    index = 0
    while index < len(part) - 1:
        found = best_detour(
            succ=succ,
            start=part[index],
            end=part[index + 1],
            placed=placed,
            limit=MAX_SKIPPED + 2 - len(part),
        )
        part[index + 1:index + 1] = found
        placed.update(found)
        index += 0 if found else 1
    return part


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
        The line with each link `filled`, in travel order.
    """
    line = list(chain[:1])
    for start, end in pairwise(chain):
        line.extend(filled(
            succ=succ, start=start, end=end, placed=placed,
        )[1:])
    return tuple(line)
