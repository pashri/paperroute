"""The pins, the place shown, and moving between them."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute.core.feeds import feed_for
from paperroute.core.geo import MetreBox
from paperroute.core.map_state import MapState, area_box
from paperroute.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    Event,
    LoadModes,
    MapDataFailed,
    ModesLoaded,
)
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
        Only vehicle positions and their fetch times are kept.
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


def with_pinned(*, places: Places, pin: Pin) -> Places:
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


def with_unpinned(*, places: Places) -> Places:
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
        return replace(state, places=with_unpinned(places=places))
    pin = places.unpinned
    return replace(state, places=with_pinned(places=places, pin=pin))


def other_area(*, state: State, mpp: int, box: MetreBox) -> bool:
    """
    Say whether a map area is not the one shown at a zoom.

    Parameters
    ----------
    state : State
        State.
    mpp : int
        Metres per pixel the area was asked for at.
    box : MetreBox
        The area asked for.

    Returns
    -------
    bool
        True when the map has no centre or its area at `mpp` differs.
    """
    centre = state.map.centre
    return centre is None or box != area_box(centre=centre, mpp=mpp)


def is_stale(*, state: State, event: Event) -> bool:
    """
    Say whether an event answers a request for a place no longer shown.

    Parameters
    ----------
    state : State
        State.
    event : Event
        What happened.

    Returns
    -------
    bool
        True for departures loaded or failed for another place, and for
        area modes loaded or failed for another map area.
    """
    match event:
        case DeparturesLoaded(pin=pin) | DeparturesFailed(pin=pin):
            return not same_place(first=pin, second=shown(state=state))
        case (
            ModesLoaded(mpp=mpp, box=box)
            | MapDataFailed(request=LoadModes(mpp=mpp, box=box))
        ):
            return other_area(state=state, mpp=mpp, box=box)
    return False
