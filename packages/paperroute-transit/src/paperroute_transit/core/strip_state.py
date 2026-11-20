"""Which reaches the strips need, asking for them, and the Strip's scroll."""

from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import Final

from paperroute_transit.core.chooser import ChooserState, Stage
from paperroute_transit.core.connection import selected_trips
from paperroute_transit.core.live import LiveDeparture, resolved
from paperroute_transit.core.messages import (
    Effect,
    LoadStrip,
    StripFailed,
    StripLoaded,
)
from paperroute_transit.core.model import (
    Departure,
    Pin,
    StopPlace,
    is_connection,
    pin_routes,
)
from paperroute_transit.core.modes import end_point_modes, line_modes
from paperroute_transit.core.places import shown
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.reach import Reach, StripKey
from paperroute_transit.core.rhythm import frame_time, stale_after
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_model import RETRY_AFTER, State, elapsed
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.core.strip_folds import folded
from paperroute_transit.core.strip_follow import followed_reach
from paperroute_transit.core.strip_model import (
    NEEDS_UPDATE,
    StripInputs,
    TripDay,
)
from paperroute_transit.core.strip_slots import (
    destination_station,
    home_of,
    window_of,
)
from paperroute_transit.core.strip_times import (
    TripInputs,
    joined_ids,
    strip_trips,
    trip_ids,
    window_departures,
)
from paperroute_transit.core.train_follow import Following, train_strip
from paperroute_transit.core.train_window import shows_dash

STRIP_VIEWS: Final[frozenset[LiveView]] = frozenset(
    {LiveView.STRIP, LiveView.STRIP_MAP}
)


def strip_key(*, pin: Pin, end_points: Collection[str]) -> StripKey:
    """
    Say which runs feed a place's strip.

    Parameters
    ----------
    pin : Pin
        The place.
    end_points : Collection[str]
        Ids of the modes chosen by end points.

    Returns
    -------
    StripKey
        Its From's ID, kind and routes (see `pin_routes`).
    """
    return StripKey(
        station=pin.origin.stop_id,
        kind=pin.kind,
        routes=pin_routes(pin=pin, end_points=end_points),
    )


def shown_key(*, state: State) -> StripKey:
    """
    Say which runs feed the shown place's strip.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    StripKey
        The shown place's key (see `strip_key`).
    """
    return strip_key(
        pin=shown(state=state),
        end_points=end_point_modes(modes=state.city.modes),
    )


def picking_to(*, state: State) -> ChooserState | None:
    """
    Find the chooser while it picks a Destination on a strip.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    ChooserState | None
        The chooser in the TO stage, else None.
    """
    chooser = state.chooser
    if chooser is None or chooser.stage is not Stage.TO:
        return None
    return chooser


def chooser_strip(*, state: State) -> tuple[StripKey, StopPlace] | None:
    """
    Find the strip the chooser picks a Destination on.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[StripKey, StopPlace] | None
        Its key and From in the TO stage, else None.
    """
    chooser = picking_to(state=state)
    if chooser is None or chooser.key is None or chooser.origin is None:
        return None
    return chooser.key, chooser.origin


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
        views never builds its reach again, and the chooser's in the TO
        stage.
    """
    keys = {shown_key(state=state)}
    picked = chooser_strip(state=state)
    if picked is not None:
        keys.add(picked[0])
    return frozenset(keys)


def strips_shown(*, state: State) -> dict[StripKey, StopPlace]:
    """
    List the strips on screen.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    dict[StripKey, StopPlace]
        Each strip's From by key: the chooser's in the TO stage, else
        the shown place's on a strip view; none for a connection pin.
    """
    picked = chooser_strip(state=state)
    if picked is not None:
        return dict([picked])
    pin = shown(state=state)
    on_strip = state.chooser is None and state.view in STRIP_VIEWS
    if not on_strip or is_connection(pin=pin):
        return {}
    key = strip_key(
        pin=pin, end_points=end_point_modes(modes=state.city.modes),
    )
    return {key: pin.origin}


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
    Drop reaches no longer in use and ask for the strips on screen.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with only the reaches in use, and for each strip on screen
        its key pending and a LoadStrip; no effect for a reach built or
        pending, or for `RETRY_AFTER` after a failure.
    """
    state = pruned_reaches(state=state)
    strip = state.strip
    asked = strip.reaches.keys() | strip.pending
    if not elapsed(since=strip.failed_at, now=state.now, span=RETRY_AFTER):
        return state, ()
    loads = tuple(
        LoadStrip(key=key, origin=origin, timetable=state.saving.timetable)
        for key, origin in strips_shown(state=state).items()
        if key not in asked
    )
    strip = replace(
        strip, pending=strip.pending | {load.key for load in loads},
    )
    return replace(state, strip=strip), loads


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


def folded_reach(*, state: State, key: StripKey | None,
                 destination: str | None) -> Reach | None:
    """
    Find a strip's reach with its folds shut but for the opened ones.

    Parameters
    ----------
    state : State
        State.
    key : StripKey | None
        The strip, or None.
    destination : str | None
        The Destination stop or station ID, never folded away, or None.

    Returns
    -------
    Reach | None
        The reach as shown, or None while it is not built or the
        timetable has none.
    """
    reach = None if key is None else state.strip.reaches.get(key)
    if reach is None:
        return None
    keep = () if destination is None else (
        reach.station_of.get(destination, destination),
    )
    return folded(reach=reach, opened=state.strip.opened, keep=keep)


def on_end_points(*, state: State) -> bool:
    """
    Say whether the shown place's kind is chosen by end points.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True for a pin of an end-point kind.
    """
    return shown(state=state).kind in end_point_modes(modes=state.city.modes)


def destination_id(*, state: State) -> str | None:
    """
    Give the shown place's Destination ID.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str | None
        The Destination's stop or station ID, or None.
    """
    destination = shown(state=state).destination
    return None if destination is None else destination.stop_id


def shown_reach(*, state: State) -> Reach | None:
    """
    Find the shown place's reach as the Strip shows it.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Reach | None
        The reach with its folds and its rows below the From along the
        train it follows, or None while it is not built or the timetable
        has none. A reach built with a train network is laid out by
        `train_strip` instead, with no folds.
    """
    key = shown_key(state=state)
    destination = destination_id(state=state)
    built = None if key is None else state.strip.reaches.get(key)
    if built is not None and built.source is not None:
        return train_strip(
            data=built.source,
            follow=Following(
                trips=followed_trips(state=state, key=key, reach=built),
                destination=destination_station(
                    reach=built, destination=destination,
                ),
                selected=state.strip.selected,
                loop_name=state.city.loop_name,
            ),
        )
    reach = folded_reach(state=state, key=key, destination=destination)
    if reach is None:
        return None
    return followed_reach(
        reach=reach,
        trips=followed_trips(state=state, key=key, reach=reach),
        target=destination_station(reach=reach, destination=destination),
    )


def followed_trips(
    *, state: State, key: StripKey | None, reach: Reach,
) -> TripInputs:
    """
    Gather the departures and patterns a strip's rows are laid out from.

    Parameters
    ----------
    state : State
        State.
    key : StripKey | None
        The strip.
    reach : Reach
        The strip's built reach.

    Returns
    -------
    TripInputs
        The departures with live times, the loaded patterns and the
        strip's stations; no positions.
    """
    frame, limit = frame_time(state=state), stale_after(state=state)
    return TripInputs(
        departures=resolved(
            departures=state.board.departures,
            snapshots=state.snapshots,
            now=frame,
            limit=limit,
            zone=state.city.zone,
            plan=state.city.feeds,
        ),
        key=key,
        patterns=state.map.patterns,
        positions={},
        now=frame,
        zone=state.city.zone,
        stale_after=limit,
        station_of=reach.station_of,
        line_kinds=line_modes(modes=state.city.modes),
    )


def scroll_range(
    *,
    reach: Reach | None,
    destination: str | None,
    banner: bool,
    end_points: bool = False,
) -> tuple[int, int]:
    """
    Find how far the Strip scrolls before its rows stop moving.

    Parameters
    ----------
    reach : Reach | None
        The reach shown, or None.
    destination : str | None
        The place's Destination stop or station ID, or None.
    banner : bool
        True while a live data banner shortens the strip.
    end_points : bool
        Whether the shown pin's kind is chosen by end points.

    Returns
    -------
    tuple[int, int]
        The lowest and highest scroll; both 0 with no reach. The range
        always holds 0, the resting scroll, even when the home window sits
        so near the top that a few scrolls down move nothing. When the
        home window dashes its top slot, the lowest is at least one turn
        back, which shows the earlier call the dash stands for.
    """
    if reach is None:
        return 0, 0
    rows = window_of(banner=banner).rows
    shift = home_of(
        reach=reach, destination=destination, rows=rows, end_points=end_points,
    )
    low, high = min(0, -shift), len(reach.rows) - rows - shift
    if end_points:
        high = max(0, high)
        if shows_dash(reach=reach, first=shift):
            low = min(-1, -shift)
    return low, max(low, high)


def clamped(*, state: State, banner: bool) -> State:
    """
    Keep the Strip's scroll within its reach.

    Parameters
    ----------
    state : State
        State.
    banner : bool
        True while a live data banner shortens the strip.

    Returns
    -------
    State
        State with the scroll set to the one last asked for, clamped to
        `scroll_range`, and those bounds kept for the knob; none kept
        while there is no reach.
    """
    reach = shown_reach(state=state)
    end_points = on_end_points(state=state)
    low, high = scroll_range(
        reach=reach,
        destination=destination_id(state=state),
        banner=banner,
        end_points=end_points,
    )
    strip = state.strip
    followed = followed_of(reach=reach, before=strip.followed)
    asked = 0 if followed != strip.followed and end_points else strip.asked
    return replace(state, strip=replace(
        strip,
        scroll=min(max(asked, low), high),
        asked=asked,
        followed=followed,
        bounds=None if reach is None else (low, high),
    ))


def followed_of(*, reach: Reach | None, before: TripDay | None
                ) -> TripDay | None:
    """
    Name the train a reach is laid out along.

    Parameters
    ----------
    reach : Reach | None
        The shown reach, or None.
    before : TripDay | None
        The train followed so far.

    Returns
    -------
    TripDay | None
        The reach's trip and day when it has one, else `before`.
    """
    if reach is None or reach.trip is None or reach.day is None:
        return before
    return reach.trip, reach.day


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
            key=shown_key(state=state),
            now=state.now,
            line_kinds=line_modes(modes=state.city.modes),
        )
    )


def picked_window(*, state: State) -> tuple[Departure, ...]:
    """
    List the departures behind the chooser's strip's trips.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Departure, ...]
        One departure per trip of the chooser's strip in its window, in
        the TO stage; none otherwise.
    """
    chooser = picking_to(state=state)
    if chooser is None or chooser.key is None:
        return ()
    return tuple(
        window_departures(
            departures=chooser.departures,
            key=chooser.key,
            now=state.now,
            line_kinds=line_modes(modes=state.city.modes),
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
        The shown strip's trips on a strip view, and the chooser's in
        the TO stage, with the trips their loaded patterns join onto;
        the selected pair's trips for a connection pin; none elsewhere.
    """
    wanted = trip_ids(departures=picked_window(state=state))
    if state.view in STRIP_VIEWS and is_connection(pin=shown(state=state)):
        wanted |= selected_trips(state=state)
    elif state.view in STRIP_VIEWS:
        wanted |= strip_trips(
            departures=state.board.departures,
            key=shown_key(state=state),
            now=state.now,
            line_kinds=line_modes(modes=state.city.modes),
        )
    return wanted | joined_ids(starts=wanted, patterns=state.map.patterns)


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
    key = shown_key(state=state)
    reaches = state.strip.reaches
    return StripInputs(
        reach=shown_reach(state=state),
        unavailable=(
            NEEDS_UPDATE
            if key in reaches and reaches[key] is None
            else None
        ),
        departures=departures,
        patterns=state.map.patterns,
        positions=positions,
        now=frame_time(state=state),
        zone=state.city.zone,
        stale_after=stale_after(state=state),
        line_kinds=line_modes(modes=state.city.modes),
        scroll=state.strip.scroll,
        cursor=None,
        picking=None,
        destination=destination_id(state=state),
        tag=stop_view.tag,
        stop_view=stop_view,
        key=key,
        selected=state.strip.selected,
        end_points=(
            shown(state=state).kind in end_point_modes(modes=state.city.modes)
        ),
    )
