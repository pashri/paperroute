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
class Kerb[R]:
    """
    The next bus from one kerb of a both-sides pin.

    Attributes
    ----------
    label : str
        The fixed words that head the block.
    end : str
        The place drawn after the label, or '' for none.
    hero : R | None
        The kerb's next bus, or None when none comes in view.
    under : UnderNumber | None
        The block under the hero's number, or None without a hero.
    """

    label: str
    end: str
    hero: R | None
    under: UnderNumber | None


@dataclass(frozen=True)
class EachWay[R]:
    """
    The next bus from each kerb of a both-sides pin.

    Attributes
    ----------
    here : Kerb[R]
        The picked kerb.
    across : Kerb[R]
        The kerb across the road.
    """

    here: Kerb[R]
    across: Kerb[R]
