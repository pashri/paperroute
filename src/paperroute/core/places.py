"""The pins, the place shown, and moving between them."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from paperroute.core.feeds import feed_for
from paperroute.core.map_state import MapState, area_box, area_frame
from paperroute.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    HolidaysLoaded,
    LineFailed,
    LineLoaded,
    LoadModes,
    MapDataFailed,
    MatchPlaces,
    ModesLoaded,
    PlacesFailed,
    ServiceSpanLoaded,
    StripFailed,
    StripLoaded,
)
from paperroute.core.saved import Saved
from paperroute.core.state_model import (
    RETRY_AFTER,
    Board,
    Places,
    State,
    elapsed,
    restarted,
)
from paperroute_transit.core.alert_model import BetweenLoaded
from paperroute_transit.core.city import City
from paperroute_transit.core.geo import MetreBox
from paperroute_transit.core.model import (
    Departure,
    Pin,
    StopPlace,
    pin_key,
    same_place,
)


def places_of(*, saved: Saved, city: City) -> Places:
    """
    Take the pins and the place shown from what was saved.

    Parameters
    ----------
    saved : Saved
        What was saved, possibly from a damaged file.
    city : City
        The city, whose default pin shows when nothing else would.

    Returns
    -------
    Places
        The saved places with `current` clamped onto the pins, and the
        city's default pin unpinned when there would be no place to show.
    """
    pins = saved.pins
    current = min(max(saved.current, 0), max(len(pins) - 1, 0))
    unpinned = saved.unpinned
    if unpinned is None and not pins:
        unpinned = city.default_pin
    return Places(pins=pins, current=current, unpinned=unpinned)


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
                if (feed := feed_for(mode=dep.source)) is not None
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
        in flight, the Stop view and the Strip at the top, only the
        needed feeds' downloads, and the map with no knob step centred on
        the new From. Only vehicle positions and their fetch times are
        kept; the old reach goes once it is out of use. A new cycle is
        due at once.
    """
    state = replace(state, places=replace(state.places, unpinned=pin))
    feeds = feeds_of(departures=departures)
    old = state.map
    state = restarted(state=state)
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
        strip=replace(state.strip, scroll=0, asked=0),
        map=MapState(
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
    index = pin_index(places=places, pin=pin)
    if index is not None:
        return Places(pins=places.pins, current=index)
    pins = (*places.pins, pin)
    return Places(pins=pins, current=len(pins) - 1)


def pin_index(*, places: Places, pin: Pin) -> int | None:
    """
    Find the pin for the same place as another.

    Parameters
    ----------
    places : Places
        The pins.
    pin : Pin
        The place.

    Returns
    -------
    int | None
        The index of the pin for the same place, or None.
    """
    return next(
        (
            index
            for index, existing in enumerate(places.pins)
            if same_place(first=existing, second=pin)
        ),
        None,
    )


def committed(
    *, state: State, pin: Pin, departures: tuple[Departure, ...] = (),
) -> State:
    """
    Show a chosen place: the pin for the same place, else unpinned.

    Parameters
    ----------
    state : State
        State.
    pin : Pin
        The chosen place.
    departures : tuple[Departure, ...]
        Departures already known for it.

    Returns
    -------
    State
        State showing the place. The place already shown keeps its
        board and scroll.
    """
    index = pin_index(places=state.places, pin=pin)
    places = (
        replace(state.places, unpinned=pin)
        if index is None
        else replace(state.places, current=index, unpinned=None)
    )
    after = replace(state, places=places)
    if same_place(first=shown(state=after), second=shown(state=state)):
        return after
    return with_place(state=after, pin=places.unpinned, departures=departures)


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
    centre, _ = area_frame(map_state=state.map)
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
        True for departures loaded under the other school switch; for
        departures loaded or failed, a service span read, or
        stations between read, for another place or another timetable
        version, for a reach, a chooser load, a failed place look-up or
        holidays from another timetable version, and for area modes
        loaded or failed for another map area.
    """
    match event:
        case DeparturesLoaded(skip_school=skip, skip_school_pins=pins) if (
            skip != state.settings.board.skip_school_buses
            or pins != state.settings.board.school_off_route_pins
        ):
            return True
        case (
            DeparturesLoaded(pin=pin, timetable=timetable)
            | DeparturesFailed(pin=pin, timetable=timetable)
            | ServiceSpanLoaded(pin=pin, timetable=timetable)
            | BetweenLoaded(pin=pin, timetable=timetable)
        ):
            return timetable != state.saving.timetable or not same_place(
                first=pin, second=shown(state=state)
            )
        case (
            StripLoaded(timetable=timetable)
            | StripFailed(timetable=timetable)
            | ColumnLoaded(timetable=timetable)
            | ColumnFailed(timetable=timetable)
            | LineLoaded(timetable=timetable)
            | LineFailed(timetable=timetable)
            | ChooserDeparturesLoaded(timetable=timetable)
            | ChooserDeparturesFailed(timetable=timetable)
            | PlacesFailed(timetable=timetable)
            | HolidaysLoaded(timetable=timetable)
        ):
            return timetable != state.saving.timetable
        case (
            ModesLoaded(mpp=mpp, box=box)
            | MapDataFailed(request=LoadModes(mpp=mpp, box=box))
        ):
            return other_area(state=state, mpp=mpp, box=box)
    return False


def stops_to_match(*, state: State) -> tuple[StopPlace, ...]:
    """
    List every stop the places use.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[StopPlace, ...]
        Each pin's From and Destination, then the unpinned place's, each
        stop ID once.
    """
    places = state.places
    pins = places.pins
    if places.unpinned is not None:
        pins = (*pins, places.unpinned)
    stops: dict[str, StopPlace] = {}
    for pin in pins:
        for stop in (pin.origin, pin.destination):
            if stop is not None:
                stops.setdefault(stop.stop_id, stop)
    return tuple(stops.values())


def looked_up(
    *, stop: StopPlace, found: Mapping[str, StopPlace | None]
) -> StopPlace | None:
    """
    Find a stop's new place.

    Parameters
    ----------
    stop : StopPlace
        The stop as stored.
    found : Mapping[str, StopPlace | None]
        New places by old stop ID.

    Returns
    -------
    StopPlace | None
        The new place, None when it is gone, or `stop` itself when it was
        not looked up.
    """
    return found[stop.stop_id] if stop.stop_id in found else stop


def rematched(*, pin: Pin, found: Mapping[str, StopPlace | None]) -> Pin:
    """
    Match one place's stops against the timetable.

    Parameters
    ----------
    pin : Pin
        The place.
    found : Mapping[str, StopPlace | None]
        New places by old stop ID.

    Returns
    -------
    Pin
        The place with its stops' new IDs, names and points; marked
        missing, with its old stops, when either is gone. A missing mark
        is cleared only when every stop was looked up and found. Unchanged
        when none of its stops was looked up.
    """
    looked = [
        stop.stop_id in found
        for stop in (pin.origin, pin.destination)
        if stop is not None
    ]
    if not any(looked):
        return pin
    origin = looked_up(stop=pin.origin, found=found)
    destination = (
        None
        if pin.destination is None
        else looked_up(stop=pin.destination, found=found)
    )
    if origin is None or (pin.destination is not None and destination is None):
        return replace(pin, missing=True)
    return replace(
        pin,
        origin=origin,
        destination=destination,
        missing=pin.missing and not all(looked),
    )


def rematched_pins(
    *, pins: tuple[Pin, ...], found: Mapping[str, StopPlace | None]
) -> tuple[Pin, ...]:
    """
    Match every pin, never letting two pins become the same place.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        The pins, all different places.
    found : Mapping[str, StopPlace | None]
        New places by old stop ID.

    Returns
    -------
    tuple[Pin, ...]
        The pins in the same order. A pin whose match would be another
        pin's place keeps its old stops and is marked missing.
    """
    result = list(pins)
    for index, pin in enumerate(pins):
        new = rematched(pin=pin, found=found)
        taken = {
            pin_key(pin=other)
            for other_index, other in enumerate(result)
            if other_index != index
        }
        if pin_key(pin=new) in taken:
            new = replace(pin, missing=True)
        result[index] = new
    return tuple(result)


def matched(
    *, state: State, found: Mapping[str, StopPlace | None]
) -> State:
    """
    Apply a timetable look-up to every pin and the unpinned place.

    A stop found under its own ID takes its new name and point; one found
    under another ID moves to it; one not found marks its place missing
    and keeps its old data. A later match of all its stops clears the
    mark.

    Parameters
    ----------
    state : State
        State.
    found : Mapping[str, StopPlace | None]
        New places by old stop ID; stops not in it are left alone, and a
        pin none of whose stops is in it is untouched.

    Returns
    -------
    State
        State with the places matched. Nothing else changes.
    """
    places = state.places
    unpinned = places.unpinned
    if unpinned is not None:
        unpinned = rematched(pin=unpinned, found=found)
    pins = rematched_pins(pins=places.pins, found=found)
    return replace(state, places=replace(places, pins=pins, unpinned=unpinned))


def rematch(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask to match the places against the timetable in use.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with no retry due, and a MatchPlaces tagged with the
        timetable version.
    """
    saving = replace(state.saving, match_failed_at=None)
    ask = MatchPlaces(
        places=stops_to_match(state=state), timetable=saving.timetable
    )
    return replace(state, saving=saving), (ask,)


def rematch_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Match the places again `RETRY_AFTER` after a failed look-up.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and a MatchPlaces when a retry is due; unchanged otherwise.
    """
    failed_at = state.saving.match_failed_at
    if failed_at is None or not elapsed(
        since=failed_at, now=state.now, span=RETRY_AFTER
    ):
        return state, ()
    return rematch(state=state)


def match_failed(*, state: State) -> State:
    """
    Note that a place look-up failed, so a later tick retries it.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with the failure time recorded.
    """
    saving = replace(state.saving, match_failed_at=state.now)
    return replace(state, saving=saving)
