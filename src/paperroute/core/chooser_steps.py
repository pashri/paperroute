"""The route chooser in the reducer: its knobs, loads and screens."""

from dataclasses import replace
from typing import Final

from paperroute.core.chooser import (
    ChooserRequest,
    ChooserState,
    ColumnRequest,
    LineRequest,
    Stage,
    StripRequest,
    backed,
    chosen_pin,
    opened,
    pressed,
    to_row,
    turned,
    with_column,
    with_line,
)
from paperroute.core.chooser_view import ChooserView, build_chooser_view
from paperroute.core.live import live_since, resolved
from paperroute.core.map_requests import polled_positions
from paperroute.core.messages import (
    ChooserDeparturesLoaded,
    ChooserEvent,
    ColumnFailed,
    ColumnLoaded,
    Effect,
    Knob,
    KnobHeld,
    KnobPressed,
    KnobTurned,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadLine,
)
from paperroute.core.model import Departure, Pin
from paperroute.core.places import committed, feeds_of, shown
from paperroute.core.reach import Reach
from paperroute.core.saved import LiveView
from paperroute.core.state_model import State, load_window
from paperroute.core.stop_view import (
    NOT_PINNED,
    StopInputs,
    StopView,
    build_stop_view,
)
from paperroute.core.strip_state import picking_to
from paperroute.core.strip_view import (
    NEEDS_UPDATE,
    PICK_FROM,
    PICK_TO,
    StripInputs,
    StripView,
    build_strip_view,
    line_reach,
    origin_of,
)

LINE_FAILED: Final[str] = "Can't load the line"


def chooser_reach(*, chooser: ChooserState, state: State) -> Reach | None:
    """
    Find the chooser's strip.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    state : State
        State.

    Returns
    -------
    Reach | None
        The TO stage's reach, or None while it is not built or before the
        TO stage.
    """
    if chooser.key is None:
        return None
    return state.strip.reaches.get(chooser.key)


def departures_load(
    *, state: State, chooser: ChooserState,
) -> tuple[Effect, ...]:
    """
    Ask for the departures from the chooser's From.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Chooser with a From.

    Returns
    -------
    tuple[Effect, ...]
        A LoadChooserDepartures over the usual window.
    """
    start, end = load_window(now=state.now)
    load = LoadChooserDepartures(
        pin=chosen_pin(chooser=chooser, destination=None),
        start=start,
        end=end,
        timetable=state.saving.timetable,
    )
    return (load,)


def request_effects(
    *, state: State, request: ChooserRequest | None,
) -> tuple[Effect, ...]:
    """
    Turn a chooser request into effects.

    The strip itself is asked for when the state settles.

    Parameters
    ----------
    state : State
        State with the chooser that made the request.
    request : ChooserRequest | None
        The request.

    Returns
    -------
    tuple[Effect, ...]
        A LoadColumn, a LoadLine, or for a strip the departures load.
    """
    timetable = state.saving.timetable
    match request:
        case ColumnRequest(path=path):
            return (LoadColumn(path=path, timetable=timetable),)
        case LineRequest(towards=towards):
            return (LoadLine(towards=towards, timetable=timetable),)
        case StripRequest() if state.chooser is not None:
            return departures_load(state=state, chooser=state.chooser)
    return ()


def moved(
    *,
    state: State,
    chooser: ChooserState | None,
    request: ChooserRequest | None = None,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store the chooser and ask for what it requests.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState | None
        The new chooser, or None when it closed.
    request : ChooserRequest | None
        What it asks for.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; the same state when nothing changed.
    """
    if chooser == state.chooser and request is None:
        return state, ()
    state = replace(state, chooser=chooser)
    return state, request_effects(state=state, request=request)


def commit(*, state: State, chooser: ChooserState, pin: Pin) -> State:
    """
    Close the chooser and show its place on the Stop view.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Chooser in the TO stage.
    pin : Pin
        The place chosen.

    Returns
    -------
    State
        State showing the place, with the chooser's departures when it
        has no Destination.
    """
    departures = chooser.departures if pin.destination is None else ()
    state = replace(state, chooser=None, view=LiveView.STOP)
    return committed(state=state, pin=pin, departures=departures)


def chooser_pressed(
    *, state: State, chooser: ChooserState,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Handle a bottom-knob press in the chooser.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Its chooser.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The chooser moved on, or its place committed.
    """
    after, outcome = pressed(
        chooser=chooser, reach=chooser_reach(chooser=chooser, state=state),
    )
    if isinstance(outcome, Pin):
        return commit(state=state, chooser=chooser, pin=outcome), ()
    return moved(state=state, chooser=after, request=outcome)


def chooser_knob(
    *, state: State, event: KnobTurned | KnobPressed | KnobHeld,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a knob event to the chooser, opening it on a top press.

    Parameters
    ----------
    state : State
        State with the chooser open, or a top press.
    event : KnobTurned | KnobPressed | KnobHeld
        Knob event.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; the same state for a top turn or a hold.
    """
    chooser = state.chooser
    if chooser is None:
        first, asked = opened(shown=shown(state=state))
        return moved(state=state, chooser=first, request=asked)
    match event:
        case KnobPressed(knob=Knob.TOP):
            return moved(state=state, chooser=backed(chooser=chooser))
        case KnobPressed(knob=Knob.BOTTOM):
            return chooser_pressed(state=state, chooser=chooser)
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            after, request = turned(
                chooser=chooser,
                steps=steps,
                reach=chooser_reach(chooser=chooser, state=state),
            )
            return moved(state=state, chooser=after, request=request)
    return state, ()


def with_departures(
    *, chooser: ChooserState, event: ChooserDeparturesLoaded,
) -> ChooserState:
    """
    Take in the departures from the chooser's From.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    event : ChooserDeparturesLoaded
        The departures and the place they were asked for.

    Returns
    -------
    ChooserState
        The chooser with the departures; unchanged unless it is picking a
        Destination from that place's From.
    """
    origin = chooser.origin
    if (
        chooser.stage is not Stage.TO
        or origin is None
        or origin.stop_id != event.pin.origin.stop_id
    ):
        return chooser
    return replace(chooser, departures=event.departures)


def replied(
    *, chooser: ChooserState, event: ChooserEvent,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Take a load's result into the chooser.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    event : ChooserEvent
        A column, line or departures loaded or failed.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser and its next request; unchanged for a result it no
        longer waits for and for a failed departures load.
    """
    match event:
        case ColumnLoaded(path=path, column=column):
            return with_column(chooser=chooser, path=path, column=column)
        case ColumnFailed(path=path):
            return with_column(chooser=chooser, path=path, column=None)
        case LineLoaded(towards=towards, line=line):
            return with_line(chooser=chooser, towards=towards, line=line), None
        case LineFailed(towards=towards):
            return with_line(chooser=chooser, towards=towards, line=None), None
        case ChooserDeparturesLoaded():
            return with_departures(chooser=chooser, event=event), None
    return chooser, None


def chooser_event(
    *, state: State, event: ChooserEvent,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a chooser load's result.

    Parameters
    ----------
    state : State
        State.
    event : ChooserEvent
        A column, line or departures loaded or failed.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; the same state when the chooser is closed
        or nothing changed.
    """
    chooser = state.chooser
    if chooser is None:
        return state, ()
    after, request = replied(chooser=chooser, event=event)
    return moved(state=state, chooser=after, request=request)


def asked_again(*, chooser: ChooserState) -> ChooserRequest | None:
    """
    Choose what the chooser asks for again from a new timetable.

    Parameters
    ----------
    chooser : ChooserState
        Chooser, not failed.

    Returns
    -------
    ChooserRequest | None
        The column waited for, the line while loading, or the TO stage's
        strip; None when nothing is waited for.
    """
    match chooser.stage:
        case Stage.LISTS if chooser.waiting is not None:
            return ColumnRequest(path=chooser.waiting)
        case Stage.FROM if not chooser.line and chooser.towards is not None:
            return LineRequest(towards=int(chooser.towards.key))
        case Stage.TO if chooser.key is not None:
            return StripRequest(key=chooser.key)
    return None


def chooser_retimed(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Carry the chooser over to a new timetable.

    Loads in flight from the old timetable are dropped when they land,
    so whatever the chooser waits for is asked for again.

    Parameters
    ----------
    state : State
        State with the new timetable version recorded.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with a failed chooser closed and the TO cursor back on the
        From, and the loads asked for again.
    """
    chooser = state.chooser
    if chooser is None:
        return state, ()
    if chooser.failed:
        return replace(state, chooser=None), ()
    request = asked_again(chooser=chooser)
    state = replace(state, chooser=replace(chooser, to_cursor=0))
    return state, request_effects(state=state, request=request)


def trip_feeds(*, state: State) -> tuple[str, ...]:
    """
    List the trip-updates feeds to poll.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        The shown place's feeds, then those the chooser's departures
        need in the TO stage, each once.
    """
    chooser = picking_to(state=state)
    extra = () if chooser is None else feeds_of(departures=chooser.departures)
    return tuple(dict.fromkeys((*state.feeds, *extra)))


def from_inputs(*, state: State, chooser: ChooserState) -> StripInputs:
    """
    Gather what the FROM stage's strip is built from.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Chooser in the FROM stage.

    Returns
    -------
    StripInputs
        The towards line as a plain strip with its cursor, no
        departures, and `LINE_FAILED` when the line can't be read.
    """
    line = chooser.line
    return StripInputs(
        reach=line_reach(line=line) if line else None,
        unavailable=LINE_FAILED if chooser.failed else None,
        departures=(),
        patterns={},
        positions={},
        now=state.now,
        scroll=0,
        cursor=chooser.line_cursor if line else None,
        picking=PICK_FROM,
        destination=None,
        tag=NOT_PINNED,
    )


def picked_stop_view(*, state: State, chooser: ChooserState) -> StopView:
    """
    Build the board of the chooser's From, for the TO stage's panel.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Chooser in the TO stage.

    Returns
    -------
    StopView
        The From's whole-stop board, with live times where known.
    """
    departures: tuple[Departure, ...] = chooser.departures
    feeds = feeds_of(departures=departures)
    return build_stop_view(
        inputs=StopInputs(
            origin='' if chooser.origin is None else chooser.origin.name,
            destination=None,
            departures=resolved(
                departures=departures,
                snapshots=state.snapshots,
                now=state.now,
            ),
            now=state.now,
            scroll=0,
            live_since=live_since(
                snapshots=state.snapshots, feeds=feeds, now=state.now,
            ),
            has_feed=bool(feeds) or not departures,
            loaded=bool(departures),
        ),
    )


def to_cursor_row(*, chooser: ChooserState, reach: Reach | None) -> int | None:
    """
    Find the TO stage's cursor row.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the TO stage.
    reach : Reach | None
        Its strip, or None while loading.

    Returns
    -------
    int | None
        The From row moved by the TO cursor, or None with no From row.
    """
    origin = None if reach is None else origin_of(reach=reach)
    if reach is None or origin is None:
        return None
    return to_row(chooser=chooser, reach=reach, origin=origin)


def to_inputs(*, state: State, chooser: ChooserState) -> StripInputs:
    """
    Gather what the TO stage's strip is built from.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Chooser in the TO stage.

    Returns
    -------
    StripInputs
        The chooser's strip, cursor, panel, times and vehicles, all from
        its own departures; `NEEDS_UPDATE` when the timetable has no
        reach for it.
    """
    stop_view = picked_stop_view(state=state, chooser=chooser)
    reach = chooser_reach(chooser=chooser, state=state)
    built = chooser.key in state.strip.reaches
    return StripInputs(
        reach=reach,
        unavailable=NEEDS_UPDATE if built and reach is None else None,
        departures=resolved(
            departures=chooser.departures,
            snapshots=state.snapshots,
            now=state.now,
        ),
        patterns=state.map.patterns,
        positions=polled_positions(state=state),
        now=state.now,
        scroll=0,
        cursor=to_cursor_row(chooser=chooser, reach=reach),
        picking=PICK_TO,
        destination=None,
        tag=NOT_PINNED,
        stop_view=stop_view,
        key=chooser.key,
    )


def chooser_view(
    *, state: State, chooser: ChooserState,
) -> ChooserView | StripView:
    """
    Build the chooser's screen.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        The open chooser.

    Returns
    -------
    ChooserView | StripView
        The columns while listing routes, else the strip being picked on.
    """
    match chooser.stage:
        case Stage.LISTS:
            return build_chooser_view(chooser=chooser)
        case Stage.FROM:
            return build_strip_view(
                inputs=from_inputs(state=state, chooser=chooser),
            )
    return build_strip_view(inputs=to_inputs(state=state, chooser=chooser))
