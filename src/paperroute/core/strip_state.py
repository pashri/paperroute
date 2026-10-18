"""Which reaches the strips need, asking for them, and the Strip's scroll."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.messages import (
    Effect,
    LoadStrip,
    StripFailed,
    StripLoaded,
)
from paperroute.core.model import Departure, Pin
from paperroute.core.places import shown
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.reach import Reach, StripKey
from paperroute.core.saved import LiveView
from paperroute.core.state_model import RETRY_AFTER, State, elapsed
from paperroute.core.stop_view import StopView
from paperroute.core.strip_vehicles import strip_trips, window_departures
from paperroute.core.strip_view import (
    NEEDS_UPDATE,
    PANE_FROM_ROW,
    STRIP_ROWS,
    StripInputs,
    origin_of,
)

STRIP_VIEWS: Final[frozenset[LiveView]] = frozenset(
    {LiveView.STRIP, LiveView.STRIP_MAP}
)


def strip_key(*, pin: Pin) -> StripKey:
    """
    Say which runs feed a place's strip.

    Parameters
    ----------
    pin : Pin
        The place.

    Returns
    -------
    StripKey
        Its From's ID, kind and routes.
    """
    return StripKey(
        station=pin.origin.stop_id, kind=pin.kind, routes=pin.routes
    )


def keys_in_use(*, state: State) -> frozenset[StripKey]:
    """
    List the strips whose reaches are kept.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    frozenset[StripKey]
        The shown place's key, on every Live view, so moving between
        views never builds its reach again.
    """
    return frozenset({strip_key(pin=shown(state=state))})


def pruned_reaches(*, state: State) -> State:
    """
    Drop the reaches no longer in use.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State keeping only the reaches of `keys_in_use`.
    """
    in_use = keys_in_use(state=state)
    reaches = {
        key: reach
        for key, reach in state.strip.reaches.items()
        if key in in_use
    }
    return replace(state, strip=replace(state.strip, reaches=reaches))


def request_strips(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop reaches no longer in use and ask for the shown one on a strip.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with only the reaches in use, and on a strip view the key
        pending and a LoadStrip; no effect when the reach is built or
        pending, or for `RETRY_AFTER` after a failure.
    """
    state = pruned_reaches(state=state)
    strip = state.strip
    pin = shown(state=state)
    key = strip_key(pin=pin)
    asked = strip.reaches.keys() | strip.pending
    backing_off = not elapsed(
        since=strip.failed_at, now=state.now, span=RETRY_AFTER
    )
    if state.view not in STRIP_VIEWS or key in asked or backing_off:
        return state, ()
    strip = replace(strip, pending=strip.pending | {key})
    load = LoadStrip(
        key=key, origin=pin.origin, timetable=state.saving.timetable
    )
    return replace(state, strip=strip), (load,)


def strip_loaded(*, state: State, event: StripLoaded) -> State:
    """
    Store a built reach.

    Parameters
    ----------
    state : State
        State.
    event : StripLoaded
        The reach built.

    Returns
    -------
    State
        State with the key no longer pending and its reach stored; a
        reach for a key out of use goes at the next `request_strips`.
    """
    strip = replace(
        state.strip,
        pending=state.strip.pending - {event.key},
        reaches={**state.strip.reaches, event.key: event.reach},
    )
    return replace(state, strip=strip)


def strip_failed(*, state: State, event: StripFailed) -> State:
    """
    Forget a failed reach request and start its backoff.

    Parameters
    ----------
    state : State
        State.
    event : StripFailed
        The failure.

    Returns
    -------
    State
        State with the key no longer pending and the failure time set;
        unchanged for a key neither pending nor in use.
    """
    stale = event.key not in state.strip.pending | keys_in_use(state=state)
    if stale:
        return state
    strip = replace(
        state.strip,
        pending=state.strip.pending - {event.key},
        failed_at=state.now,
    )
    return replace(state, strip=strip)


def shown_reach(*, state: State) -> Reach | None:
    """
    Find the shown place's reach.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Reach | None
        The reach, or None while it is not built or the timetable has
        none.
    """
    return state.strip.reaches.get(strip_key(pin=shown(state=state)))


def scroll_range(*, reach: Reach | None) -> tuple[int, int]:
    """
    Find how far the Strip scrolls before its rows stop moving.

    Parameters
    ----------
    reach : Reach | None
        The reach shown, or None.

    Returns
    -------
    tuple[int, int]
        The lowest and highest scroll; both 0 with no reach. The range
        always holds 0, the resting scroll, even when the From sits so
        near the top that a few scrolls down move nothing.
    """
    if reach is None:
        return 0, 0
    origin = origin_of(reach=reach)
    shift = 0 if origin is None else origin - PANE_FROM_ROW
    low = min(0, -shift)
    return low, max(low, len(reach.rows) - STRIP_ROWS - shift)


def clamped(*, state: State) -> State:
    """
    Keep the Strip's scroll within its reach.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with the scroll clamped to `scroll_range`.
    """
    low, high = scroll_range(reach=shown_reach(state=state))
    scroll = min(max(state.strip.scroll, low), high)
    return replace(state, strip=replace(state.strip, scroll=scroll))


def strip_window(*, state: State) -> tuple[Departure, ...]:
    """
    List the departures behind the shown strip's trips.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Departure, ...]
        One departure per trip of the strip's kind or routes, timetabled
        in the strip's window around now; every one.
    """
    return tuple(
        window_departures(
            departures=state.board.departures,
            key=strip_key(pin=shown(state=state)),
            now=state.now,
        )
    )


def strip_patterns(*, state: State) -> frozenset[str]:
    """
    List the trips whose patterns a strip needs.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    frozenset[str]
        The shown strip's trips on a strip view; none elsewhere.
    """
    if state.view not in STRIP_VIEWS:
        return frozenset()
    return strip_trips(
        departures=state.board.departures,
        key=strip_key(pin=shown(state=state)),
        now=state.now,
    )


def strip_inputs(
    *,
    state: State,
    stop_view: StopView,
    departures: tuple[LiveDeparture, ...],
    positions: Mapping[str, PositionsSnapshot],
) -> StripInputs:
    """
    Gather what the shown place's Strip views are built from.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The place's unscrolled Stop view.
    departures : tuple[LiveDeparture, ...]
        Every departure loaded, with live times applied.
    positions : Mapping[str, PositionsSnapshot]
        Downloads of the positions feeds polled.

    Returns
    -------
    StripInputs
        Inputs with no cursor; `NEEDS_UPDATE` when the timetable has no
        reach for the place.
    """
    pin = shown(state=state)
    key = strip_key(pin=pin)
    reaches = state.strip.reaches
    destination = pin.destination
    return StripInputs(
        reach=reaches.get(key),
        unavailable=(
            NEEDS_UPDATE
            if key in reaches and reaches[key] is None
            else None
        ),
        departures=departures,
        patterns=state.map.patterns,
        positions=positions,
        now=state.now,
        scroll=state.strip.scroll,
        cursor=None,
        picking=None,
        destination=None if destination is None else destination.stop_id,
        tag=stop_view.tag,
        stop_view=stop_view,
        key=key,
    )
