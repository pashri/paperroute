"""Events that bring strips, chooser columns and map data."""

from dataclasses import replace

from paperroute.core.chooser_steps import chooser_event
from paperroute.core.cycle import answered, answered_late
from paperroute.core.map_state import failed, with_patterns
from paperroute.core.messages import (
    ChooserEvent,
    Effect,
    MapDataFailed,
    MapEvent,
    ModesLoaded,
    PatternsLoaded,
    PositionsFetched,
    ShapesLoaded,
    StripFailed,
    StripLoaded,
)
from paperroute.core.state_model import State
from paperroute.core.state_views import settled
from paperroute.core.strip_state import strip_failed, strip_loaded


def on_map_event(
    *, state: State, event: MapEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store map data, ask for what is still needed, and redraw.

    Parameters
    ----------
    state : State
        State.
    event : MapEvent
        Map data or a failure to load it.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; a failure only records itself, and
        positions answering late are left for the next frame to draw.
    """
    map_state, draw = state.map, True
    match event:
        case ShapesLoaded(shapes=shapes):
            map_state = replace(
                map_state,
                shapes={**map_state.shapes, **shapes},
            )
        case PatternsLoaded():
            map_state = with_patterns(map_state=map_state, event=event)
        case ModesLoaded(mpp=mpp, modes=modes):
            map_state = replace(
                map_state,
                area_modes={**map_state.area_modes, mpp: modes},
                pending_modes=map_state.pending_modes - {mpp},
            )
        case PositionsFetched(feed=feed, snapshot=snapshot):
            draw = not answered_late(state=state, feed=feed)
            state = answered(state=state, feed=feed)
            map_state = replace(
                map_state,
                positions={**map_state.positions, feed: snapshot},
            )
        case MapDataFailed():
            map_state = failed(map_state=map_state, event=event, now=state.now)
            return replace(state, map=map_state), ()
    return settled(state=replace(state, map=map_state), draw=draw)


def on_chooser_event(
    *, state: State, event: ChooserEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a chooser load's result, asking for what comes next.

    Parameters
    ----------
    state : State
        State.
    event : ChooserEvent
        A column, line or departures loaded or failed.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when nothing changed.
    """
    after, effects = chooser_event(state=state, event=event)
    if after is state:
        return state, ()
    return settled(state=after, effects=effects)


def on_strip(
    *, state: State, event: StripLoaded | StripFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store a built reach, redrawing, or note a failed one.

    Parameters
    ----------
    state : State
        State.
    event : StripLoaded | StripFailed
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    if isinstance(event, StripFailed):
        return strip_failed(state=state, event=event), ()
    return settled(state=strip_loaded(state=state, event=event))
