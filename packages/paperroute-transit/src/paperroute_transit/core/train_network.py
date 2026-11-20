"""A train kind's hops and weekday runs, as a strip reads them."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from paperroute_transit.core.model import until_revisit


@dataclass(frozen=True)
class Hop:
    """
    The track between two consecutive calls.

    Attributes
    ----------
    passed : tuple[str, ...]
        Station IDs passed without a call, in order of travel.
    inside : bool
        Whether the track joins the two stations.
    """

    passed: tuple[str, ...]
    inside: bool


@dataclass(frozen=True)
class WeekdayRun:
    """
    A run that has regular weekday trains.

    Attributes
    ----------
    route : str
        Route short name; the operator in Cambridge.
    stations : tuple[str, ...]
        Station IDs the run calls at, in order.
    trains : int
        Regular trains a weekday.
    """

    route: str
    stations: tuple[str, ...]
    trains: int


@dataclass(frozen=True)
class TrainNetwork:
    """
    The hops and weekday runs of one kind around a From.

    Attributes
    ----------
    hops : Mapping[tuple[str, str], Hop]
        Hops by their two stations.
    runs : tuple[WeekdayRun, ...]
        The kind's runs through the From that have weekday trains.
    """

    hops: Mapping[tuple[str, str], Hop]
    runs: tuple[WeekdayRun, ...]


OUTSIDE: Final[Hop] = Hop(passed=(), inside=False)


def hop_of(*, network: TrainNetwork, a: str, b: str) -> Hop:
    """
    Find the hop between two consecutive calls.

    Parameters
    ----------
    network : TrainNetwork
        The loaded hops.
    a : str
        Station ID of the earlier call.
    b : str
        Station ID of the later call.

    Returns
    -------
    Hop
        The stored hop, or `OUTSIDE` when there is none.
    """
    return network.hops.get((a, b), OUTSIDE)


def from_origin(
    *, stations: Sequence[str], origin: str,
) -> tuple[tuple[str, ...], bool]:
    """
    Take the stations after a run's first call at the origin.

    Parameters
    ----------
    stations : Sequence[str]
        Station IDs the run calls at, in order.
    origin : str
        The From station.

    Returns
    -------
    tuple[tuple[str, ...], bool]
        The stations after the first origin, cut before the first
        station the run calls at twice; and whether the cut fell on the
        origin. A run that does not call at the origin gives ``((),
        False)``.
    """
    if origin not in stations:
        return (), False
    rest = list(stations[list(stations).index(origin):])
    kept = until_revisit(stations=rest)
    looped = len(kept) < len(rest) and rest[len(kept)] == origin
    return kept[1:], looped


def expanded(
    *, network: TrainNetwork, stations: Sequence[str],
) -> tuple[tuple[str, bool], ...]:
    """
    Put each hop's passed stations between the calls.

    Parameters
    ----------
    network : TrainNetwork
        The loaded hops.
    stations : Sequence[str]
        Station IDs called at, in order.

    Returns
    -------
    tuple[tuple[str, bool], ...]
        Each station with whether it was passed rather than called at.
    """
    out: list[tuple[str, bool]] = []
    for a, b in pairwise(stations):
        out.append((a, False))
        out.extend(
            (station, True)
            for station in hop_of(network=network, a=a, b=b).passed
        )
    out.extend((station, False) for station in stations[-1:])
    return tuple(out)
