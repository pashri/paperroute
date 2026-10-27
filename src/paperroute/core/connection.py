"""A connection pin's loaded legs: their pairs and what alerts name."""

from collections.abc import Mapping, Sequence

from paperroute.core.alert_match import (
    BETWEEN_TRIPS,
    PinFacts,
    is_name_pin,
    pin_facts,
)
from paperroute.core.connection_model import (
    Legs,
    Pair,
    first_change,
    pairs_of,
    stopgap_way,
    way_legs,
)
from paperroute.core.live import resolved
from paperroute.core.model import Departure, Pin
from paperroute.core.places import shown
from paperroute.core.state_model import Board, State


def live_pairs(*, state: State) -> tuple[Pair, ...]:
    """
    Pair the shown connection pin's loaded legs with live times.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Pair, ...]
        The workable pairs; empty for a plain pin or with no legs loaded.
    """
    change = first_change(pin=shown(state=state))
    legs = state.board.legs
    if change is None or legs is None:
        return ()
    lists = (state.board.departures, legs.second, legs.direct)
    first, second, direct = (
        resolved(departures=items, snapshots=state.snapshots, now=state.now)
        for items in lists
    )
    return pairs_of(
        first=first,
        second=second,
        direct=direct,
        allow=change.allow,
        now=state.now,
    )


def pair_legs(*, pair: Pair) -> tuple[Departure, ...]:
    """
    List the scheduled departures a pair rides.

    Parameters
    ----------
    pair : Pair
        The pair.

    Returns
    -------
    tuple[Departure, ...]
        Leg 1 then leg 2, or the one departure when staying on.
    """
    if pair.second is None:
        return (pair.first.departure,)
    return pair.first.departure, pair.second.departure


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
        The pin itself for a plain pin; leg 1 and leg 2 for a connection.
    """
    way = stopgap_way(pin=pin)
    if way is None:
        return ((pin, board.departures),)
    legs = board.legs or Legs()
    first, second = way_legs(pin=pin, way=way)
    return (first, board.departures), (second, legs.second)


def connection_facts(
    *,
    pin: Pin,
    departures: Sequence[Departure],
    legs: Legs | None,
    between: frozenset[str],
    families: Mapping[str, frozenset[str]],
) -> PinFacts:
    """
    Gather what alerts can name about either leg of a connection pin.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    departures : Sequence[Departure]
        Leg-1 departures.
    legs : Legs | None
        Leg-2 and direct departures; None before they load.
    between : frozenset[str]
        Place names of stations between each leg's ends.
    families : Mapping[str, frozenset[str]]
        Stop families of every leg's ends.

    Returns
    -------
    PinFacts
        Each leg's facts as for a plain pin, merged, plus the direct
        list's trips and routes. Names come only from a leg matched by
        name.

    Raises
    ------
    ValueError
        When the pin has no ticked way.
    """
    legs = legs or Legs()
    way = stopgap_way(pin=pin)
    if way is None:
        raise ValueError('A pin without a way has no legs.')
    first, second = way_legs(pin=pin, way=way)
    parts = [
        pin_facts(
            pin=leg, departures=items, between=between, families=families,
        )
        for leg, items in ((first, departures), (second, legs.second))
    ]
    direct = legs.direct
    return PinFacts(
        trips=parts[0].trips | parts[1].trips | {
            trip for item in direct for trip in (item.trip_id, *item.aliases)
        },
        stops=parts[0].stops | parts[1].stops,
        routes=(
            parts[0].routes | parts[1].routes | {item.route for item in direct}
        ),
        names=parts[0].names | parts[1].names,
        wharf=parts[0].wharf or parts[1].wharf,
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
    if stopgap_way(pin=pin) is None:
        return pin_facts(
            pin=pin,
            departures=board.departures,
            between=between,
            families=families,
        )
    return connection_facts(
        pin=pin,
        departures=board.departures,
        legs=board.legs,
        between=between,
        families=families,
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
    List the trips of the pair the connection Strip shows.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    frozenset[str]
        The trip IDs of the pair the scroll selects; empty with no pair.
    """
    pairs = live_pairs(state=state)
    if not pairs:
        return frozenset()
    pair = pairs[min(state.scroll, len(pairs) - 1)]
    return frozenset(item.trip_id for item in pair_legs(pair=pair))
