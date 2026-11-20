"""What the Stop view shows under the big number."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NextCall:
    """
    One later stop on the hero's trip.

    Attributes
    ----------
    at : str
        Clock time of the call.
    name : str
        Stop name.
    """

    at: str
    name: str


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
    delay : int
        Seconds late, negative when early.
    place : str
        Where the vehicle is, or '' when its position is not reported.
    calls : tuple[NextCall, ...]
        The next stops on the trip.
    """

    status: str
    platform: str = ''
    then: tuple[str, ...] = ()
    delay: int = 0
    place: str = ''
    calls: tuple[NextCall, ...] = ()
