"""The Stop view of a connection pin: the next chain and the ones after."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Final

from paperroute.core.chains import Chain
from paperroute.core.connection_model import change_walk
from paperroute.core.rides import Ride, rides_of
from paperroute.core.stop_view import (
    HORIZON_TEXT,
    PinTag,
    StopView,
    countdown,
    empty_text,
    nouns,
)
from paperroute_device.core.text import clock_text
from paperroute_transit.core.city import City
from paperroute_transit.core.model import Pin

NO_PAIRS: Final[str] = f'No connections in {HORIZON_TEXT}'
PAIR_ROWS: Final[int] = 5
WAITING: Final[frozenset[str]] = frozenset({
    empty_text(destination=None, loaded=False),
    empty_text(destination=None, loaded=True, missing=True),
})


@dataclass(frozen=True)
class HeroLine:
    """
    One line of the hero: a ride's badge line or a plain step.

    Attributes
    ----------
    route : str | None
        The ride's route, or None for a plain step.
    noun : str | None
        What the ride is called, such as 'train', or None for a plain
        step.
    text : str
        The ride's headsign, or the step.
    at : str
        The ride's departure time; empty for a plain step.
    """

    route: str | None
    noun: str | None
    text: str
    at: str = ''


@dataclass(frozen=True)
class ChainHero:
    """
    The next chain, spelled out ride by ride.

    Attributes
    ----------
    countdown : str
        Minutes to the first ride, or 'now'.
    leaves_at : str
        The first ride's departure time.
    lines : tuple[HeroLine, ...]
        Each ride's badge line, with the change and walk lines between,
        or the direct or stay-on line after a lone ride.
    arrives_at : str
        Arrival time at the Destination.
    live : bool
        True when the first ride has live times.
    """

    countdown: str
    leaves_at: str
    lines: tuple[HeroLine, ...]
    arrives_at: str
    live: bool


@dataclass(frozen=True)
class PairRow:
    """
    One chain in a list.

    Attributes
    ----------
    leaves_at : str
        The first ride's departure time.
    chips : tuple[str, ...]
        Each ride's route.
    detail : str
        'direct', 'stay on', 'at {stop}' or 'via {stop}, {stop}'.
    arrives_at : str
        Arrival time at the Destination.
    minutes : int
        Journey time: whole minutes from leaving to arriving.
    countdown : str
        Minutes to the first ride, or 'now'; empty when not counted.
    live : bool
        True when the first ride has live times.
    """

    leaves_at: str
    chips: tuple[str, ...]
    detail: str
    arrives_at: str
    minutes: int
    countdown: str = ''
    live: bool = False


@dataclass(frozen=True)
class ConnectionView:
    """
    Everything the connection Stop view draws.

    Attributes
    ----------
    hero : ChainHero | None
        The next chain, or None with no chain.
    rows : tuple[PairRow, ...]
        The chains after it, scrolled, at most `PAIR_ROWS`.
    empty : str | None
        Why there is no hero, or None.
    """

    origin: str
    destination: str | None
    clock: str
    tag: PinTag
    hero: ChainHero | None
    rows: tuple[PairRow, ...]
    empty: str | None
    banner: str | None
    warning: bool
    more: int
    planned: bool


def ride_line(*, ride: Ride, zone: tzinfo, city: City) -> HeroLine:
    """
    Make a ride's badge line.

    Parameters
    ----------
    ride : Ride
        The ride.
    zone : tzinfo
        Local time zone the times show in.
    city : City
        The city, which holds what each ride is called.

    Returns
    -------
    HeroLine
        Its route, connection noun, headsign and departure time.
    """
    departure = ride.item.departure
    return HeroLine(
        route=departure.route,
        noun=nouns(city=city, departure=departure).connection,
        text=departure.headsign,
        at=clock_text(moment=ride.departs, zone=zone),
    )


def change_lines(*, ride: Ride, zone: tzinfo) -> tuple[HeroLine, ...]:
    """
    Spell out the change made to board a ride.

    Parameters
    ----------
    ride : Ride
        A ride after the first.
    zone : tzinfo
        Local time zone the times show in.

    Returns
    -------
    tuple[HeroLine, ...]
        'change at {board} {time}', then 'walk {metres} m' when the
        change walks (see `change_walk`); nothing for the first ride.
    """
    change = ride.change
    if change is None:
        return ()
    at = HeroLine(
        route=None,
        noun=None,
        text=f'change at {change.board.name} '
        f'{clock_text(moment=ride.departs, zone=zone)}',
    )
    metres = change_walk(change=change)
    if metres is None:
        return (at,)
    return at, HeroLine(route=None, noun=None, text=f'walk {metres} m')


def hero_lines(
    *, chain: Chain, pin: Pin, zone: tzinfo, city: City,
) -> tuple[HeroLine, ...]:
    """
    List the hero's lines for a chain.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.
    zone : tzinfo
        Local time zone the times show in.
    city : City
        The city, which holds what each ride is called.

    Returns
    -------
    tuple[HeroLine, ...]
        Each ride's change lines and badge line; a lone ride adds
        'direct to {To}' or 'stay on to {To}'.
    """
    rides = rides_of(chain=chain, pin=pin)
    lines = tuple(
        line
        for ride in rides
        for line in (
            *change_lines(ride=ride, zone=zone),
            ride_line(ride=ride, zone=zone, city=city),
        )
    )
    if len(rides) > 1:
        return lines
    to = '' if pin.destination is None else pin.destination.name
    lead = 'direct' if chain.way is None else 'stay on'
    return (*lines, HeroLine(route=None, noun=None, text=f'{lead} to {to}'))


def chain_hero(
    *, chain: Chain, pin: Pin, now: datetime, zone: tzinfo, city: City,
) -> ChainHero:
    """
    Spell out the next chain.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.
    now : datetime
        Current time.
    zone : tzinfo
        Local time zone the times show in.
    city : City
        The city, which holds what each ride is called.

    Returns
    -------
    ChainHero
        The hero.
    """
    return ChainHero(
        countdown=countdown(departs=chain.departs, now=now),
        leaves_at=clock_text(moment=chain.departs, zone=zone),
        lines=hero_lines(chain=chain, pin=pin, zone=zone, city=city),
        arrives_at=clock_text(moment=chain.arrives, zone=zone),
        live=chain.legs[0].live,
    )


def route_chips(*, chain: Chain, pin: Pin) -> tuple[str, ...]:
    """
    List a chain's route chips.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[str, ...]
        Each ride's route; a stay on is one chip.
    """
    return tuple(
        ride.item.departure.route for ride in rides_of(chain=chain, pin=pin)
    )


def via_text(*, chain: Chain, pin: Pin) -> str:
    """
    Say where a chain changes.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.

    Returns
    -------
    str
        'direct', 'stay on', 'at {board}' for one change, or
        'via {board}, {board}' for two.
    """
    boards = [
        ride.change.board.name
        for ride in rides_of(chain=chain, pin=pin)
        if ride.change is not None
    ]
    if not boards:
        return 'direct' if chain.way is None else 'stay on'
    if len(boards) == 1:
        return f'at {boards[0]}'
    return 'via ' + ', '.join(boards)


def chain_row(
    *, chain: Chain, pin: Pin, now: datetime, zone: tzinfo,
) -> PairRow:
    """
    Summarise a chain as a list row.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.
    now : datetime
        Current time.
    zone : tzinfo
        Local time zone the times show in.

    Returns
    -------
    PairRow
        The row.
    """
    seconds = chain.arrives.timestamp() - chain.departs.timestamp()
    return PairRow(
        leaves_at=clock_text(moment=chain.departs, zone=zone),
        chips=route_chips(chain=chain, pin=pin),
        detail=via_text(chain=chain, pin=pin),
        arrives_at=clock_text(moment=chain.arrives, zone=zone),
        minutes=int(seconds // 60),
        countdown=countdown(departs=chain.departs, now=now),
        live=chain.legs[0].live,
    )


def chain_rows(
    *, chains: Sequence[Chain], pin: Pin, now: datetime, zone: tzinfo,
) -> tuple[PairRow, ...]:
    """
    Summarise chains as list rows.

    Parameters
    ----------
    chains : Sequence[Chain]
        The chains.
    pin : Pin
        Connection pin.
    now : datetime
        Current time.
    zone : tzinfo
        Local time zone the times show in.

    Returns
    -------
    tuple[PairRow, ...]
        One row each, in order.
    """
    return tuple(
        chain_row(chain=chain, pin=pin, now=now, zone=zone)
        for chain in chains
    )


def no_pair_text(
    *, stop_view: StopView, chains: Sequence[Chain],
) -> str | None:
    """
    Say why there is no hero.

    Parameters
    ----------
    stop_view : StopView
        The direct list's Stop view.
    chains : Sequence[Chain]
        The workable chains.

    Returns
    -------
    str | None
        None with a chain; the Stop view's text while loading or with a
        missing stop; otherwise no connections to the To, or `NO_PAIRS`
        without a To name.
    """
    if chains:
        return None
    if stop_view.empty in WAITING:
        return stop_view.empty
    if stop_view.destination is None:
        return NO_PAIRS
    return f'No connections to {stop_view.destination} in {HORIZON_TEXT}'


def build_connection_view(
    *,
    stop_view: StopView,
    pin: Pin,
    chains: Sequence[Chain],
    scroll: int,
    now: datetime,
    zone: tzinfo,
    city: City,
) -> ConnectionView:
    """
    Build the connection Stop view.

    Parameters
    ----------
    stop_view : StopView
        The direct list's Stop view, for the titles, tag and banner.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains in first-leg departure order.
    scroll : int
        Requested scroll; the list starts at chain `1 + scroll`, clamped
        so the last chain stays listed.
    now : datetime
        Current time.
    zone : tzinfo
        Local time zone the times show in.
    city : City
        The city, which holds what each ride is called.

    Returns
    -------
    ConnectionView
        View-model.
    """
    rest = chains[1:]
    start = max(0, min(scroll, len(rest) - 1))
    return ConnectionView(
        origin=stop_view.origin,
        destination=stop_view.destination,
        clock=stop_view.clock,
        tag=stop_view.tag,
        hero=(
            chain_hero(
                chain=chains[0], pin=pin, now=now, zone=zone, city=city,
            )
            if chains
            else None
        ),
        rows=chain_rows(
            chains=rest[start : start + PAIR_ROWS],
            pin=pin,
            now=now,
            zone=zone,
        ),
        empty=no_pair_text(stop_view=stop_view, chains=chains),
        banner=stop_view.banner,
        warning=stop_view.warning,
        more=stop_view.more,
        planned=stop_view.planned,
    )
