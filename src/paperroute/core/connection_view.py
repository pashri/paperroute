"""The Stop view of a connection pin: the next pair and the ones after."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from paperroute.core.connection_model import Pair, walk_metres
from paperroute.core.model import Mode, Pin
from paperroute.core.stop_view import (
    PinTag,
    StopView,
    clock_text,
    countdown,
    empty_text,
)

NO_PAIRS: Final[str] = 'No connection in the next 3 hours'
PAIR_ROWS: Final[int] = 5
MODE_NOUNS: Final[dict[Mode, str]] = {
    Mode.BUS: 'bus',
    Mode.TRAIN: 'train',
    Mode.METRO: 'metro',
    Mode.FERRY: 'ferry',
    Mode.LIGHT_RAIL: 'light rail',
    Mode.COACH: 'coach',
    Mode.REGIONAL: 'train',
    Mode.OTHER: 'service',
}
WAITING: Final[frozenset[str]] = frozenset({
    empty_text(destination=None, loaded=False),
    empty_text(destination=None, loaded=True, missing=True),
})


@dataclass(frozen=True)
class PairHero:
    """
    The next pair, spelled out step by step.

    Attributes
    ----------
    countdown : str
        Minutes to leg 1's departure, 'now', or its clock time when far.
    route : str
        Leg 1's route.
    mode : Mode
        Leg 1's mode.
    headsign : str
        Leg 1's headsign.
    leaves_at : str
        Leg 1's departure time.
    steps : tuple[str, ...]
        The get-off and walk lines, or the stay-on line.
    onto : str | None
        Leg 2's route, or None when staying on.
    onto_mode : Mode | None
        Leg 2's mode, or None when staying on.
    onto_at : str
        Leg 2's departure time; empty when staying on.
    arrives_at : str
        Arrival time at the destination.
    live : bool
        True when leg 1 has live times.
    """

    countdown: str
    route: str
    mode: Mode
    headsign: str
    leaves_at: str
    steps: tuple[str, ...]
    onto: str | None
    onto_mode: Mode | None
    onto_at: str
    arrives_at: str
    live: bool


@dataclass(frozen=True)
class PairRow:
    """
    One pair in a list.

    Attributes
    ----------
    leaves_at : str
        Leg 1's departure time.
    route : str
        Leg 1's route.
    onto : str | None
        Leg 2's route, or None when staying on.
    detail : str
        'change {alight}, {noun} {board}' or 'stay on'.
    arrives_at : str
        Arrival time at the destination.
    minutes : int
        Whole minutes from leaving to arriving.
    """

    leaves_at: str
    route: str
    onto: str | None
    detail: str
    arrives_at: str
    minutes: int


@dataclass(frozen=True)
class ConnectionView:
    """
    Everything the connection Stop view draws.

    Attributes
    ----------
    hero : PairHero | None
        The next pair, or None with no pair.
    rows : tuple[PairRow, ...]
        The pairs after it, scrolled, at most `PAIR_ROWS`.
    empty : str | None
        Why there is no hero, or None.
    alight_at : tuple[str, ...]
        Leg 1's arrival times at the change for the listed pairs; empty
        unless a view needs them.
    """

    origin: str
    destination: str | None
    clock: str
    tag: PinTag
    hero: PairHero | None
    rows: tuple[PairRow, ...]
    empty: str | None
    banner: str | None
    warning: bool
    more: int
    planned: bool
    alight_at: tuple[str, ...] = ()


def pair_steps(*, pair: Pair, pin: Pin) -> tuple[str, ...]:
    """
    Spell out what happens between leg 1 and the destination.

    Parameters
    ----------
    pair : Pair
        The pair.
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[str, ...]
        The get-off and walk lines, or the stay-on line.
    """
    change = pin.change
    if pair.second is None or change is None:
        to = '' if pin.destination is None else pin.destination.name
        return (f'stay on to {to}',)
    metres = walk_metres(alight=change.alight, board=change.board)
    return (
        f'get off {change.alight.name} '
        f'{clock_text(moment=pair.first.arrives)}',
        f'walk {metres} m to {change.board.name}',
    )


def pair_hero(*, pair: Pair, pin: Pin, now: datetime) -> PairHero:
    """
    Spell out the next pair.

    Parameters
    ----------
    pair : Pair
        The pair.
    pin : Pin
        Connection pin.
    now : datetime
        Current time.

    Returns
    -------
    PairHero
        The hero.
    """
    first = pair.first
    second = pair.second
    return PairHero(
        countdown=countdown(departs=first.departs, now=now),
        route=first.departure.route,
        mode=first.departure.mode,
        headsign=first.departure.headsign,
        leaves_at=clock_text(moment=first.departs),
        steps=pair_steps(pair=pair, pin=pin),
        onto=None if second is None else second.departure.route,
        onto_mode=None if second is None else second.departure.mode,
        onto_at='' if second is None else clock_text(moment=second.departs),
        arrives_at=clock_text(moment=pair.arrives),
        live=first.live,
    )


def pair_row(*, pair: Pair) -> PairRow:
    """
    Summarise a pair as a list row.

    Parameters
    ----------
    pair : Pair
        The pair.

    Returns
    -------
    PairRow
        The row.
    """
    first = pair.first
    second = pair.second
    detail = 'stay on' if second is None else (
        f'change {clock_text(moment=first.arrives)}, '
        f'{MODE_NOUNS[second.departure.mode]} '
        f'{clock_text(moment=second.departs)}'
    )
    seconds = pair.arrives.timestamp() - first.departs.timestamp()
    return PairRow(
        leaves_at=clock_text(moment=first.departs),
        route=first.departure.route,
        onto=None if second is None else second.departure.route,
        detail=detail,
        arrives_at=clock_text(moment=pair.arrives),
        minutes=int(seconds // 60),
    )


def pair_rows(*, pairs: Sequence[Pair]) -> tuple[PairRow, ...]:
    """
    Summarise pairs as list rows.

    Parameters
    ----------
    pairs : Sequence[Pair]
        The pairs.

    Returns
    -------
    tuple[PairRow, ...]
        One row each, in order.
    """
    return tuple(pair_row(pair=pair) for pair in pairs)


def no_pair_text(*, stop_view: StopView, pairs: Sequence[Pair]) -> str | None:
    """
    Say why there is no hero.

    Parameters
    ----------
    stop_view : StopView
        Leg 1's Stop view.
    pairs : Sequence[Pair]
        The workable pairs.

    Returns
    -------
    str | None
        None with a pair; leg 1's text while loading or with a missing
        stop; otherwise `NO_PAIRS`.
    """
    if pairs:
        return None
    if stop_view.empty in WAITING:
        return stop_view.empty
    return NO_PAIRS


def build_connection_view(
    *,
    stop_view: StopView,
    pin: Pin,
    pairs: Sequence[Pair],
    scroll: int,
    now: datetime,
) -> ConnectionView:
    """
    Build the connection Stop view.

    Parameters
    ----------
    stop_view : StopView
        Leg 1's Stop view, for the titles, tag and banner.
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs in leg-1 departure order.
    scroll : int
        Requested scroll; the list starts at pair `1 + scroll`, clamped so
        the last pair stays listed.
    now : datetime
        Current time.

    Returns
    -------
    ConnectionView
        View-model.
    """
    rest = pairs[1:]
    start = max(0, min(scroll, len(rest) - 1))
    return ConnectionView(
        origin=stop_view.origin,
        destination=stop_view.destination,
        clock=stop_view.clock,
        tag=stop_view.tag,
        hero=pair_hero(pair=pairs[0], pin=pin, now=now) if pairs else None,
        rows=pair_rows(pairs=rest[start : start + PAIR_ROWS]),
        empty=no_pair_text(stop_view=stop_view, pairs=pairs),
        banner=stop_view.banner,
        warning=stop_view.warning,
        more=stop_view.more,
        planned=stop_view.planned,
    )
