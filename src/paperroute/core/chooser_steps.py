"""The route chooser in the reducer: its knobs, loads and screens."""

from dataclasses import replace
from typing import Final

from paperroute.core.chooser import (
    ChooserRequest,
    ChooserState,
    ColumnRequest,
    DepartureLoad,
    LineRequest,
    Stage,
    StripRequest,
    chosen_pin,
    opened,
    to_row,
    turned,
    with_column,
    with_line,
)
from paperroute.core.chooser_station import (
    has_list_from,
    knob_backed,
    knob_pressed,
    suited,
)
from paperroute.core.chooser_view import (
    ChooserView,
    build_chooser_view,
    route_title,
)
from paperroute.core.live import live_since, resolved
from paperroute.core.map_requests import polled_positions
from paperroute.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ChooserEvent,
    ColumnFailed,
    ColumnLoaded,
    Effect,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadLine,
)
from paperroute.core.places import committed, feeds_of, shown
from paperroute.core.rhythm import stale_after
from paperroute.core.saved import LiveView
from paperroute.core.state_model import State, load_window, runs_short
from paperroute.core.stop_view import (
    NOT_PINNED,
    StopInputs,
    StopView,
    build_stop_view,
)
from paperroute.core.strip_follow import origin_of
from paperroute.core.strip_model import (
    EMPTY_STOP_VIEW,
    NEEDS_UPDATE,
    PICK_FROM,
    PICK_TO,
    StripInputs,
    StripView,
)
from paperroute.core.strip_state import folded_reach, picking_to
from paperroute.core.strip_view import build_strip_view, line_reach
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobPressed,
    KnobTurned,
)
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.reach import Reach, RowKind

LINE_FAILED: Final[str] = "Can't load the line"
DEPARTURES_FAILED: Final[str] = "Can't load departures"


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
        The TO stage's reach with its folds, or None while it is not
        built or before the TO stage.
    """
    return folded_reach(state=state, key=chooser.key, destination=None)


def picked_load(
    *, state: State, chooser: ChooserState,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the departures from the chooser's From.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Its chooser, with a From.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the load in flight, and a LoadChooserDepartures over
        the usual window.
    """
    start, end = load_window(now=state.now, zone=state.city.zone)
    load = LoadChooserDepartures(
        pin=chosen_pin(chooser=chooser, destination=None),
        start=start,
        end=end,
        timetable=state.saving.timetable,
    )
    marked = replace(chooser, load=replace(chooser.load, loading=True))
    return replace(state, chooser=marked), (load,)


def departures_chooser(*, state: State) -> ChooserState | None:
    """
    Find the chooser while it holds departures from its From.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    ChooserState | None
        The chooser in the TO stage, or in the lists once a station press
        has chosen its From; else None.
    """
    chooser = state.chooser
    if chooser is None or not has_list_from(chooser=chooser):
        return picking_to(state=state)
    return chooser


def picked_reloads(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Load the chooser's departures again when they run short.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the load in flight, and a LoadChooserDepartures, while
        the chooser holds departures, no load is in flight and less than
        `HORIZON` is loaded; unchanged otherwise.
    """
    chooser = departures_chooser(state=state)
    if chooser is None or chooser.load.loading:
        return state, ()
    if not runs_short(loaded_until=chooser.load.loaded_until, now=state.now):
        return state, ()
    return picked_load(state=state, chooser=chooser)


def asked(
    *, state: State, request: ChooserRequest | None,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for what the chooser requests.

    The strip itself is asked for when the state settles.

    Parameters
    ----------
    state : State
        State with the chooser that made the request.
    request : ChooserRequest | None
        The request.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State, and a LoadColumn, a LoadLine, or for a strip the
        departures load.
    """
    timetable = state.saving.timetable
    match request:
        case ColumnRequest(path=path):
            return state, (LoadColumn(path=path, timetable=timetable),)
        case LineRequest(towards=towards):
            return state, (LoadLine(towards=towards, timetable=timetable),)
        case StripRequest() if state.chooser is not None:
            return picked_load(state=state, chooser=state.chooser)
    return state, ()


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
    return asked(state=replace(state, chooser=chooser), request=request)


def commit(*, state: State, chooser: ChooserState, pin: Pin) -> State:
    """
    Close the chooser and show its place on the Stop view.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Chooser in the TO stage or on a TO column.
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
        The chooser moved on, its place committed, or the closed fold
        under the TO cursor opened. A station press also asks for the
        departures from the station.
    """
    unfolded = fold_pressed(state=state, chooser=chooser)
    if unfolded is not None:
        return moved(state=unfolded, chooser=unfolded.chooser or chooser)
    after, outcome = knob_pressed(
        chooser=chooser, reach=chooser_reach(chooser=chooser, state=state),
    )
    if isinstance(outcome, Pin):
        return commit(state=state, chooser=chooser, pin=outcome), ()
    state, effects = moved(state=state, chooser=after, request=outcome)
    if not isinstance(outcome, ColumnRequest) or not has_list_from(
        chooser=after,
    ):
        return state, effects
    state, load = picked_load(state=state, chooser=after)
    return state, (*effects, *load)


def cursor_at(*, reach: Reach | None, key: str) -> int:
    """
    Give a station's row counted from the From row.

    Parameters
    ----------
    reach : Reach | None
        The strip as shown.
    key : str
        Station ID.

    Returns
    -------
    int
        The station's row less the From row; 0 when either is missing.
    """
    origin = None if reach is None else origin_of(reach=reach)
    rows = [] if reach is None else [r.place.stop_id for r in reach.rows]
    if origin is None or key not in rows:
        return 0
    return rows.index(key) - origin


def fold_pressed(*, state: State, chooser: ChooserState) -> State | None:
    """
    Open the closed fold under the TO cursor.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Its chooser.

    Returns
    -------
    State | None
        State with the fold open and the cursor on its first station, or
        None when the cursor is not on a closed fold in the TO stage.
    """
    reach = chooser_reach(chooser=chooser, state=state)
    row = to_cursor_row(chooser=chooser, reach=reach)
    if chooser.stage is not Stage.TO or reach is None or row is None:
        return None
    fold = reach.rows[row]
    if fold.kind is not RowKind.FOLD:
        return None
    key = fold.place.stop_id
    shown_open = replace(state, strip=replace(
        state.strip, opened=state.strip.opened | {key},
    ))
    return replace(shown_open, chooser=replace(chooser, to_cursor=cursor_at(
        reach=chooser_reach(chooser=chooser, state=shown_open), key=key,
    )))


def chooser_knob(
    *, state: State, event: KnobEvent,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a knob event to the chooser, opening it on a top press.

    Parameters
    ----------
    state : State
        State with the chooser open, or a top press.
    event : KnobEvent
        Knob event.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; the same state for a top turn, a hold or
        a turn while held.
    """
    chooser = state.chooser
    if chooser is None:
        first, kinds = opened(shown=shown(state=state))
        return moved(state=state, chooser=first, request=kinds)
    match event:
        case KnobPressed(knob=Knob.TOP):
            return moved(state=state, chooser=knob_backed(chooser=chooser))
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


def from_matches(*, chooser: ChooserState, pin: Pin) -> bool:
    """
    Say whether a departures load is for the chooser's From.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    pin : Pin
        The place the load was asked for.

    Returns
    -------
    bool
        True in the TO stage when the From is the place's From; in the
        lists after a station press, when the place is the one picked so
        far, line included.
    """
    if has_list_from(chooser=chooser):
        return pin == chosen_pin(chooser=chooser, destination=None)
    origin = chooser.origin
    return (
        chooser.stage is Stage.TO
        and origin is not None
        and origin.stop_id == pin.origin.stop_id
    )


def with_departures(
    *,
    chooser: ChooserState,
    event: ChooserDeparturesLoaded | ChooserDeparturesFailed,
) -> ChooserState:
    """
    Take in the departures from the chooser's From, or their failure.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    event : ChooserDeparturesLoaded | ChooserDeparturesFailed
        The departures, or the failure, and the place they were asked
        for.

    Returns
    -------
    ChooserState
        The chooser with the departures and their window, or with the
        failure marked and any departures kept; unchanged unless it is
        picking a Destination from that place's From.
    """
    if not from_matches(chooser=chooser, pin=event.pin):
        return chooser
    if isinstance(event, ChooserDeparturesFailed):
        load = replace(chooser.load, loading=False, failed=True)
        return replace(chooser, load=load)
    return replace(
        chooser,
        departures=event.departures,
        load=DepartureLoad(loaded_until=event.until),
    )


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
        longer waits for.
    """
    match event:
        case ColumnLoaded(path=path, column=column):
            return with_column(
                chooser=chooser, path=path,
                column=suited(path=path, column=column),
                absent=column is None,
            )
        case ColumnFailed(path=path):
            return with_column(chooser=chooser, path=path, column=None)
        case LineLoaded(towards=towards, line=line):
            return with_line(chooser=chooser, towards=towards, line=line), None
        case LineFailed(towards=towards):
            return with_line(chooser=chooser, towards=towards, line=None), None
    return with_departures(chooser=chooser, event=event), None


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
        State with a failed chooser closed, the TO cursor back on the
        From and its departures due again, and the loads asked for again;
        after a station press, the departures too.
    """
    chooser = state.chooser
    if chooser is None:
        return state, ()
    if chooser.failed:
        return replace(state, chooser=None), ()
    request = asked_again(chooser=chooser)
    fresh = replace(chooser, to_cursor=0, load=DepartureLoad())
    state = replace(state, chooser=fresh)
    state, effects = asked(state=state, request=request)
    if not has_list_from(chooser=fresh):
        return state, effects
    state, load = picked_load(state=state, chooser=fresh)
    return state, (*effects, *load)


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
        need while it holds them, each once.
    """
    chooser = departures_chooser(state=state)
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
        The towards line as a plain strip with its cursor, titled with
        the route, no departures, and `LINE_FAILED` when the line can't
        be read.
    """
    line = chooser.line
    return StripInputs(
        reach=line_reach(line=line) if line else None,
        unavailable=LINE_FAILED if chooser.failed else None,
        departures=(),
        patterns={},
        positions={},
        now=state.now,
        zone=state.city.zone,
        stale_after=stale_after(state=state),
        scroll=0,
        cursor=chooser.line_cursor if line else None,
        picking=PICK_FROM,
        destination=None,
        tag=NOT_PINNED,
        stop_view=replace(
            EMPTY_STOP_VIEW, origin=route_title(chooser=chooser),
        ),
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
        The From's whole-stop board, with live times where known, and
        `DEPARTURES_FAILED` as its empty text when the load failed.
    """
    departures: tuple[Departure, ...] = chooser.departures
    feeds = feeds_of(departures=departures)
    board = build_stop_view(
        inputs=StopInputs(
            origin='' if chooser.origin is None else chooser.origin.name,
            destination=None,
            departures=resolved(
                departures=departures,
                snapshots=state.snapshots,
                now=state.now,
                limit=stale_after(state=state),
                zone=state.city.zone,
            ),
            now=state.now,
            zone=state.city.zone,
            scroll=0,
            live_since=live_since(
                snapshots=state.snapshots, feeds=feeds, now=state.now,
            ),
            has_feed=bool(feeds) or not departures,
            stale_after=stale_after(state=state),
            loaded=chooser.load.loaded_until is not None,
        ),
    )
    if chooser.load.failed and board.empty is not None:
        return replace(board, empty=DEPARTURES_FAILED)
    return board


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
            limit=stale_after(state=state),
            zone=state.city.zone,
        ),
        patterns=state.map.patterns,
        positions=polled_positions(state=state),
        now=state.now,
        zone=state.city.zone,
        stale_after=stale_after(state=state),
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
