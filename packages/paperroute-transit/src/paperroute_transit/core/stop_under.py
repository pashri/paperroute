"""What the Stop view shows under the big number."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UnderNumber:
    """
    The block under the hero's big number.

    Attributes
    ----------
    status : str
        The headline, such as '2 min late'.
    platform : str
        Platform or stand, or '' when unknown.
    then : tuple[str, ...]
        Clock times of the next departures after the hero.
    place : str
        The words for where the vehicle is, or '' when its position is
        not reported.
    """

    status: str
    platform: str = ''
    then: tuple[str, ...] = ()
    place: str = ''


@dataclass(frozen=True)
class GaugeCall:
    """
    One mark on the gauge after the From.

    Attributes
    ----------
    name : str
        Stop name.
    minutes : str
        Minutes past the hour of the call, such as ':08'.
    """

    name: str
    minutes: str


@dataclass(frozen=True)
class StopGauge:
    """
    The stops, bus and calls the gauge draws.

    Attributes
    ----------
    origin : str
        Name of the From.
    behind : tuple[str, ...]
        Previous stops to name, oldest first.
    hidden : int
        Stops a back break stands for.
    bus : float | None
        The badge's spot in mark slots from the first mark, or None.
    calls : tuple[GaugeCall, ...]
        Marks after the From.
    end : int | None
        Index in `calls` of the Destination.
    starts : bool
        True when the first mark is the trip's first stop.
    words : str
        Where the vehicle is, or '' when not reported.
    """

    origin: str
    behind: tuple[str, ...] = ()
    hidden: int = 0
    bus: float | None = None
    calls: tuple[GaugeCall, ...] = ()
    end: int | None = None
    starts: bool = False
    words: str = ''
