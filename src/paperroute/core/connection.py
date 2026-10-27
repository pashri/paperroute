"""A connection pin's loaded legs: their chains and what alerts name."""

from collections.abc import Mapping, Sequence

from paperroute.core.alert_match import (
    BETWEEN_TRIPS,
    PinFacts,
    is_name_pin,
    pin_facts,
)
from paperroute.core.chains import Chain, Legs, chains_of, open_legs
from paperroute.core.connection_model import direct_leg
from paperroute.core.live import LiveDeparture, resolved
from paperroute.core.model import Departure, Pin, is_connection
from paperroute.core.places import shown
from paperroute.core.state_model import Board, State


def live_chains(*, state: State) -> tuple[Chain, ...]:
    """
    Find the shown connection pin's chains with live times.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Chain, ...]
        The workable chains; empty for a plain pin or with no legs loaded.
    """
    pin = shown(state=state)
    legs = state.board.legs
    if not is_connection(pin=pin) or legs is None:
        return ()
    return chains_of(
        pin=pin,
        lists={
            key: live_list(items=items, state=state)
            for key, items in legs.lists.items()
        },
        direct=live_list(items=state.board.departures, state=state),
        now=state.now,
    )


def live_list(
    *, items: Sequence[Departure], state: State,
) -> list[LiveDeparture]:
    """
    Apply live times to one leg's departures.

    Parameters
    ----------
    items : Sequence[Departure]
        Scheduled departures.
    state : State
        State with the live snapshots and the time.

    Returns
    -------
    list[LiveDeparture]
        The departures with live times, sorted by when they leave.
    """
    found = resolved(
        departures=items, snapshots=state.snapshots, now=state.now,
    )
    return sorted(found, key=lambda item: item.departs)


def chain_legs(*, chain: Chain) -> tuple[Departure, ...]:
    """
    List the scheduled departures a chain rides.

    Parameters
    ----------
    chain : Chain
        The chain.

    Returns
    -------
    tuple[Departure, ...]
        Each leg's departure in order.
    """
    return tuple(item.departure for item in chain.legs)


def leg_lists(
    *, pin: Pin, board: Board,
) -> tuple[tuple[Pin, tuple[Departure, ...]], ...]:
    """
    Pair each leg pin of the shown pin with its loaded departures.

    Parameters
    ----------
    pin : Pin
        The shown pin.
    board : Board
        Its loaded departures.

    Returns
    -------
    tuple[tuple[Pin, tuple[Departure, ...]], ...]
        The pin itself for a plain pin; for a connection, the direct leg
        then each distinct leg of its ticked ways.
    """
    if not is_connection(pin=pin):
        return ((pin, board.departures),)
    lists = (board.legs or Legs()).lists
    return (
        (direct_leg(pin=pin), board.departures),
        *(
            (leg, lists.get(key, ()))
            for key, (leg, _) in open_legs(pin=pin).items()
        ),
    )


def connection_facts(
    *,
    pin: Pin,
    board: Board,
    between: frozenset[str],
    families: Mapping[str, frozenset[str]],
) -> PinFacts:
    """
    Gather what alerts can name about every leg of a connection pin.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    board : Board
        Its direct departures and way legs.
    between : frozenset[str]
        Place names of stations between each leg's ends.
    families : Mapping[str, frozenset[str]]
        Stop families of every leg's ends.

    Returns
    -------
    PinFacts
        Each leg's facts as for a plain pin, merged, so routes are those
        the loaded departures use. Names come only from a leg matched by
        name.
    """
    parts = [
        pin_facts(
            pin=leg, departures=items, between=between, families=families,
        )
        for leg, items in leg_lists(pin=pin, board=board)
    ]
    return PinFacts(
        trips=frozenset().union(*(part.trips for part in parts)),
        stops=frozenset().union(*(part.stops for part in parts)),
        routes=frozenset().union(*(part.routes for part in parts)),
        names=frozenset().union(*(part.names for part in parts)),
        wharf=any(part.wharf for part in parts),
    )


def board_facts(
    *,
    pin: Pin,
    board: Board,
    between: frozenset[str],
    families: Mapping[str, frozenset[str]],
) -> PinFacts:
    """
    Gather what alerts can name about the shown pin's loaded departures.

    Parameters
    ----------
    pin : Pin
        The shown pin.
    board : Board
        Its loaded departures.
    between : frozenset[str]
        Place names of stations between its ends.
    families : Mapping[str, frozenset[str]]
        Stop families of its ends.

    Returns
    -------
    PinFacts
        A connection pin's facts over both legs, or a plain pin's own.
    """
    if not is_connection(pin=pin):
        return pin_facts(
            pin=pin,
            departures=board.departures,
            between=between,
            families=families,
        )
    return connection_facts(
        pin=pin, board=board, between=between, families=families,
    )


def name_trips(*, pin: Pin, board: Board) -> tuple[bool, frozenset[str]]:
    """
    Say whether the shown pin needs stations between, and which trips.

    Parameters
    ----------
    pin : Pin
        The shown pin.
    board : Board
        Its loaded departures.

    Returns
    -------
    tuple[bool, frozenset[str]]
        True when some leg with a To is matched by name, and the first
        `BETWEEN_TRIPS` trip IDs of each such leg.
    """
    named = [
        items
        for leg, items in between_lists(pin=pin, board=board)
        if leg.destination is not None
        and is_name_pin(pin=leg, departures=items)
    ]
    return bool(named), frozenset(
        item.trip_id for items in named for item in items
    )


def between_lists(
    *, pin: Pin, board: Board,
) -> tuple[tuple[Pin, tuple[Departure, ...]], ...]:
    """
    Give each leg with its first departures asked about.

    Parameters
    ----------
    pin : Pin
        The shown pin.
    board : Board
        Its loaded departures.

    Returns
    -------
    tuple[tuple[Pin, tuple[Departure, ...]], ...]
        Each leg pin and its first `BETWEEN_TRIPS` departures.
    """
    return tuple(
        (leg, items[:BETWEEN_TRIPS])
        for leg, items in leg_lists(pin=pin, board=board)
    )


def selected_trips(*, state: State) -> frozenset[str]:
    """
    List the trips of the chain the connection Strip shows.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    frozenset[str]
        The trip IDs of the chain the scroll selects; empty with none.
    """
    chains = live_chains(state=state)
    if not chains:
        return frozenset()
    chain = chains[min(state.scroll, len(chains) - 1)]
    return frozenset(item.trip_id for item in chain_legs(chain=chain))
