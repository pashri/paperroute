"""The stations a strip reaches from its From."""

from collections.abc import Mapping
from dataclasses import dataclass

from paperroute.core.model import RouteKind, StopPlace


@dataclass(frozen=True)
class RunTail:
    """
    One way through the From: the stations before it and after it.

    Attributes
    ----------
    before : tuple[str, ...]
        Station IDs called at before the From, nearest last.
    after : tuple[str, ...]
        Station IDs called at after the From, nearest first.
    trips : int
        Trip-days that run this way.
    """

    before: tuple[str, ...]
    after: tuple[str, ...]
    trips: int


@dataclass(frozen=True)
class ReachInput:
    """
    Everything needed to lay out a strip from its From.

    Attributes
    ----------
    origin : StopPlace
        The From station.
    tails : tuple[RunTail, ...]
        The ways through the From.
    places : Mapping[str, StopPlace]
        Every station named in the tails, by ID.
    station_of : Mapping[str, str]
        The station ID of each stop under a station in the tails, and of
        each such station itself.
    """

    origin: StopPlace
    tails: tuple[RunTail, ...]
    places: Mapping[str, StopPlace]
    station_of: Mapping[str, str]


@dataclass(frozen=True)
class StripKey:
    """
    Which runs feed a strip.

    Attributes
    ----------
    station : str
        From stop or station ID.
    kind : RouteKind | None
        Kind of route, or None for every run.
    routes : tuple[str, ...]
        Route short names chosen.
    """

    station: str
    kind: RouteKind | None
    routes: tuple[str, ...]
