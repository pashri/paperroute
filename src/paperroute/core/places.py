"""The pins, the place shown, and moving between them."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute.core.feeds import feed_for
from paperroute.core.map_state import MapState
from paperroute.core.model import Departure, Pin, same_place
from paperroute.core.state_model import Board, Places, State


def shown(*, state: State) -> Pin:
    """
    Find the place shown.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Pin
        The unpinned place, or else the current pin.
    """
    places = state.places
    if places.unpinned is not None:
        return places.unpinned
    return places.pins[places.current]


def shown_index(*, state: State) -> int | None:
    """
    Find the shown place's index among the pins.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    int | None
        The current pin's index, or None when the unpinned place is shown.
    """
    places = state.places
    return None if places.unpinned is not None else places.current


def feeds_of(*, departures: Sequence[Departure]) -> tuple[str, ...]:
    """
    List the trip-updates feeds some departures need.

    Parameters
    ----------
    departures : Sequence[Departure]
        Departures.

    Returns
    -------
    tuple[str, ...]
        Each needed feed once, sorted.
    """
    return tuple(
        sorted(
            {
                feed
                for dep in departures
                if (feed := feed_for(mode=dep.mode)) is not None
            }
        )
    )


def with_place(
    *,
    state: State,
    pin: Pin | None,
    departures: tuple[Departure, ...] = (),
) -> State:
    """
    Show a new place, dropping what belonged to the old one.

    Parameters
    ----------
    state : State
        State, with the pins already as they should be.
    pin : Pin | None
        The unpinned place to show, or None to show the current pin.
    departures : tuple[Departure, ...]
        Departures already known for the new place.

    Returns
    -------
    State
        State showing the place, with a board of `departures` and no load
        in flight, the Stop view at the top, only the needed feeds'
        downloads, and the map at the same zoom centred on the new From.
        Vehicle positions and failure times are kept.
    """
    state = replace(state, places=replace(state.places, unpinned=pin))
    feeds = feeds_of(departures=departures)
    old = state.map
    return replace(
        state,
        board=Board(departures=departures),
        feeds=feeds,
        snapshots={
            feed: snapshot
            for feed, snapshot in state.snapshots.items()
            if feed in feeds
        },
        last_fetch=None,
        scroll=0,
        map=MapState(
            zoom=old.zoom,
            centre=shown(state=state).origin.point,
            positions=old.positions,
            fetched_at=old.fetched_at,
            failed_at=old.failed_at,
        ),
    )


def flipped(*, state: State, steps: int) -> State:
    """
    Turn to another pin, wrapping at both ends.

    Parameters
    ----------
    state : State
        State.
    steps : int
        Detents; positive moves to later pins.

    Returns
    -------
    State
        State showing the new pin. When the unpinned place is shown, it is
        dropped and the current pin shown instead. Unchanged with no pins
        or when the turn lands on the pin already shown.
    """
    places = state.places
    if not places.pins:
        return state
    if places.unpinned is not None:
        return with_place(state=state, pin=None)
    current = (places.current + steps) % len(places.pins)
    if current == places.current:
        return state
    state = replace(state, places=replace(places, current=current))
    return with_place(state=state, pin=None)


def pinned(*, places: Places, pin: Pin) -> Places:
    """
    Pin the unpinned place, or select the pin it matches.

    Parameters
    ----------
    places : Places
        The pins.
    pin : Pin
        The unpinned place.

    Returns
    -------
    Places
        The pins with `pin` added last and selected, or with the existing
        pin for the same place selected; no unpinned place either way.
    """
    index = next(
        (
            index
            for index, existing in enumerate(places.pins)
            if same_place(first=existing, second=pin)
        ),
        None,
    )
    if index is not None:
        return Places(pins=places.pins, current=index)
    pins = (*places.pins, pin)
    return Places(pins=pins, current=len(pins) - 1)


def unpinned(*, places: Places) -> Places:
    """
    Unpin the current pin, making it the unpinned place.

    Parameters
    ----------
    places : Places
        The pins, with the current pin shown.

    Returns
    -------
    Places
        The other pins, with `current` kept in range, and the old current
        pin as the unpinned place.
    """
    pins = places.pins
    index = places.current
    rest = (*pins[:index], *pins[index + 1:])
    current = min(index, max(len(rest) - 1, 0))
    return Places(pins=rest, current=current, unpinned=pins[index])


def toggled(*, state: State) -> State:
    """
    Pin the unpinned place or unpin the current pin.

    The same place stays on screen, so nothing is reloaded.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with the pins changed.
    """
    places = state.places
    if places.unpinned is None:
        return replace(state, places=unpinned(places=places))
    pin = places.unpinned
    return replace(state, places=pinned(places=places, pin=pin))
