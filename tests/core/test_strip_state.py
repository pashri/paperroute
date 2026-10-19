"""Tests for the reducer's Strip and Strip + map views."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.feeds import FeedKind
from paperroute.core.geo import Point
from paperroute.core.map_view import DEFAULT_ZOOM, MapView
from paperroute.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    FetchFeed,
    Knob,
    KnobPressed,
    KnobTurned,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    LoadStrip,
    PositionsFetched,
    Render,
    StripFailed,
    StripLoaded,
    Tick,
    TimetableSeen,
)
from paperroute.core.model import Departure, Mode, Pin, RouteKind
from paperroute.core.reach import Reach, ReachRow, RowKind, StripKey
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import RETRY_AFTER, State
from paperroute.core.stop_view import HORIZON
from paperroute.core.strip_model import (
    LOADING,
    NEEDS_UPDATE,
    STRIP_ROWS,
    StripMapView,
    StripView,
)
from paperroute.core.strip_state import keys_in_use, request_strips, strip_key
from tests.core.chooser_walks import CHOSEN, CHOSEN_KEY, OPEN, REACH, TO_STAGE
from tests.core.factories import START, departure, place, snapshot, vehicle

CENTRE: Final[Point] = (-17_000.0, 1_000.0)
PIN: Final[Pin] = Pin(
    origin=place('C', name='Central', point=CENTRE),
    destination=place('S5', name='S5'),
    kind=RouteKind.TRAIN,
)
KEY: Final[StripKey] = StripKey(
    station='C', kind=RouteKind.TRAIN, routes=(),
)
PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
TRAIN_POSITIONS: Final[str] = 'v2/gtfs/vehiclepos/sydneytrains'
BUS_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/buses'


def long_reach(*, size: int = 40, origin: int = 20) -> Reach:
    """
    Build a depth-0 reach with the From part-way down.

    Parameters
    ----------
    size : int
        Rows.
    origin : int
        The From row.

    Returns
    -------
    Reach
        Rows S0 to S<size - 1>, with C as the From.
    """
    return Reach(
        rows=tuple(
            ReachRow(
                place=place(
                    'C' if i == origin else f'S{i}',
                    name='C' if i == origin else f'S{i}',
                ),
                kind=RowKind.ORIGIN if i == origin else RowKind.MAIN,
                depth=0,
                above=i < origin,
                parent=(
                    None
                    if i == origin
                    else (i + 1 if i < origin else i - 1)
                ),
            )
            for i in range(size)
        ),
        stations=size - 1,
        station_of={},
    )


def trains(*, count: int = 3) -> tuple[Departure, ...]:
    """
    Make train departures five minutes apart from 8:05.

    Parameters
    ----------
    count : int
        Departures.

    Returns
    -------
    tuple[Departure, ...]
        Departures, each its own trip.
    """
    return tuple(
        departure(
            departs=START + timedelta(minutes=5 * (i + 1)),
            mode=Mode.TRAIN,
            route='T1',
            trip_id=f'train-{i}',
            kind=RouteKind.TRAIN,
        )
        for i in range(count)
    )


def on_view(
    *,
    view: LiveView,
    pin: Pin = PIN,
    departures: tuple[Departure, ...] | None = None,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Start on a view and load departures there.

    Parameters
    ----------
    view : LiveView
        The Live view to start on.
    pin : Pin
        The only pin, shown.
    departures : tuple[Departure, ...] | None
        Departures loaded; three trains by default.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects after the load.
    """
    saved = Saved(
        pins=(pin,), current=0, unpinned=None, view=view, zoom=DEFAULT_ZOOM,
    )
    state = initial_state(saved=saved, now=START, stored=True)
    return step(
        state,
        DeparturesLoaded(
            pin=pin,
            departures=trains() if departures is None else departures,
            until=START + HORIZON,
        ),
    )


def kinds[T](*, effects: tuple[Effect, ...], kind: type[T]) -> list[T]:
    """
    Pick the effects of one type.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.
    kind : type[T]
        Effect type.

    Returns
    -------
    list[T]
        The matching effects, in order.
    """
    return [effect for effect in effects if isinstance(effect, kind)]


def run(
    *, state: State, events: list[Event],
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply events in turn.

    Parameters
    ----------
    state : State
        State before.
    events : list[Event]
        Events.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after, and every effect in order.
    """
    effects: tuple[Effect, ...] = ()
    for event in events:
        state, more = step(state, event)
        effects = (*effects, *more)
    return state, effects


def turn(*, steps: int) -> KnobTurned:
    """
    Turn the bottom knob.

    Parameters
    ----------
    steps : int
        Detents, clockwise positive.

    Returns
    -------
    KnobTurned
        The turn.
    """
    return KnobTurned(knob=Knob.BOTTOM, steps=steps)


def test_strip_key_takes_the_pins_from_kind_and_routes() -> None:
    """The key is the From's ID, the kind and the routes."""
    pin = replace(PIN, kind=RouteKind.BUS, routes=('606',))
    assert strip_key(pin=pin) == StripKey(
        station='C', kind=RouteKind.BUS, routes=('606',),
    )


def test_shown_key_stays_in_use_on_every_view() -> None:
    """Cycling views keeps the shown reach, so it is never rebuilt."""
    for view in LiveView:
        state, _ = on_view(view=view)
        assert keys_in_use(state=state) == {KEY}


def test_request_strips_only_on_strip_views() -> None:
    """The Stop view and Map ask for no reach."""
    for view in (LiveView.STOP, LiveView.MAP):
        state, _ = on_view(view=view)
        assert request_strips(state=state) == (state, ())


def test_strip_view_requests_reach_once() -> None:
    """One LoadStrip for the shown key, however the views cycle."""
    state, effects = on_view(view=LiveView.STRIP)
    assert kinds(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=KEY, origin=PIN.origin),
    ]
    assert isinstance(state.last_view, StripView)
    assert state.last_view.empty == LOADING
    state, effects = run(
        state=state,
        events=[Tick(now=START + timedelta(seconds=5)), PRESS],
    )
    assert not kinds(effects=effects, kind=LoadStrip)
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    state, effects = run(state=state, events=[PRESS, PRESS, PRESS, PRESS])
    assert state.view is LiveView.STRIP_MAP
    assert not kinds(effects=effects, kind=LoadStrip)
    assert KEY in state.strip.reaches


def test_strip_failure_backs_off() -> None:
    """A failed reach is asked for again only after RETRY_AFTER."""
    state, _ = on_view(view=LiveView.STRIP)
    state, effects = step(state, StripFailed(key=KEY))
    assert not effects
    assert KEY not in state.strip.pending
    soon = START + RETRY_AFTER - timedelta(seconds=1)
    state, effects = step(state, Tick(now=soon))
    assert not kinds(effects=effects, kind=LoadStrip)
    _, effects = step(state, Tick(now=START + RETRY_AFTER))
    assert kinds(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=KEY, origin=PIN.origin),
    ]


def test_strip_on_old_timetable_says_update() -> None:
    """A reach of None means the timetable has no run tables yet."""
    state, _ = on_view(view=LiveView.STRIP)
    state, effects = step(state, StripLoaded(key=KEY, reach=None))
    [render] = kinds(effects=effects, kind=Render)
    assert isinstance(render.view, StripView)
    assert render.view.empty == NEEDS_UPDATE
    state, effects = step(state, Tick(now=START + timedelta(minutes=5)))
    assert not kinds(effects=effects, kind=LoadStrip)


def test_new_timetable_clears_the_reaches() -> None:
    """A new timetable version builds the reach again at once."""
    state, _ = on_view(view=LiveView.STRIP)
    state, _ = step(state, StripLoaded(key=KEY, reach=None))
    state, effects = step(state, TimetableSeen(version='v2'))
    assert not state.strip.reaches
    assert kinds(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=KEY, origin=PIN.origin, timetable='v2'),
    ]
    [render] = kinds(effects=effects, kind=Render)
    assert isinstance(render.view, StripView)
    assert render.view.empty == LOADING


def test_reach_from_the_old_timetable_is_dropped() -> None:
    """A reach built before a timetable swap is never stored."""
    state, _ = on_view(view=LiveView.STRIP)
    state, _ = step(state, TimetableSeen(version='v2'))
    state, effects = step(state, StripLoaded(key=KEY, reach=long_reach()))
    assert not effects
    assert not state.strip.reaches
    assert KEY in state.strip.pending
    state, effects = step(state, StripFailed(key=KEY))
    assert (effects, state.strip.failed_at) == ((), None)
    assert KEY in state.strip.pending
    state, _ = step(
        state, StripLoaded(key=KEY, reach=long_reach(), timetable='v2'),
    )
    assert state.strip.reaches == {KEY: long_reach()}


def test_stale_strip_failure_is_ignored() -> None:
    """A failure for a key neither pending nor in use starts no backoff."""
    state, _ = on_view(view=LiveView.STRIP)
    other = replace(KEY, station='elsewhere')
    after, effects = step(state, StripFailed(key=other))
    assert (after, effects) == (state, ())


def test_stale_strip_result_is_ignored() -> None:
    """A reach for a key no longer in use is not kept."""
    state, _ = on_view(view=LiveView.STRIP)
    other = replace(KEY, station='elsewhere')
    state, effects = step(state, StripLoaded(key=other, reach=long_reach()))
    assert other not in state.strip.reaches
    assert not kinds(effects=effects, kind=Render)


def test_strip_map_zooms_on_bottom_turn() -> None:
    """The bottom turn zooms the Strip + map's map; the strip stays put."""
    state, _ = on_view(view=LiveView.STRIP_MAP)
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    state, effects = step(state, turn(steps=-1))
    assert state.map.zoom == DEFAULT_ZOOM + 1
    assert state.strip.scroll == 0
    [render] = kinds(effects=effects, kind=Render)
    assert isinstance(render.view, StripMapView)
    assert render.view.map.zoom == DEFAULT_ZOOM + 1
    assert render.view.map.base.width == 480
    assert render.view.pane.rows[2].name == 'C'


def test_strip_rests_at_zero_with_from_at_the_top() -> None:
    """A From on the top row keeps the resting scroll at 0."""
    state, _ = on_view(view=LiveView.STRIP)
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach(origin=0)))
    assert state.strip.scroll == 0
    state, _ = step(state, turn(steps=-3))
    assert state.strip.scroll == 0
    assert first_name(state=state) == 'C'


def first_name(*, state: State) -> str:
    """
    Name the Strip's top row.

    Parameters
    ----------
    state : State
        State drawn on the Strip.

    Returns
    -------
    str
        The top row's name.
    """
    assert isinstance(state.last_view, StripView)
    return state.last_view.rows[0].name


def test_strip_scroll_is_separate_from_stop_scroll() -> None:
    """Each view keeps its own scroll; the long ride to S5 ends at FROM."""
    state, _ = on_view(view=LiveView.STOP, departures=trains(count=20))
    state, _ = step(state, turn(steps=2))
    state, _ = run(state=state, events=[PRESS, PRESS])
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    assert first_name(state=state) == 'S9'
    state, _ = step(state, turn(steps=1))
    assert (state.scroll, state.strip.scroll) == (2, 1)
    assert first_name(state=state) == 'S10'
    state, _ = run(state=state, events=[PRESS, PRESS])
    assert state.view is LiveView.STOP
    assert state.scroll == 2


def test_strip_scroll_is_clamped() -> None:
    """The stored strip scroll stops where the view stops moving.

    The ride to S5 is longer than the strip, so it rests with FROM, S20,
    in the bottom slot: rows S9 to S20.
    """
    state, _ = on_view(view=LiveView.STRIP)
    state, _ = step(state, turn(steps=5))
    assert state.strip.scroll == 0
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    state, _ = step(state, turn(steps=100))
    assert state.strip.scroll == 40 - STRIP_ROWS - 9
    state, effects = step(state, turn(steps=1))
    assert not effects
    state, _ = step(state, turn(steps=-100))
    assert state.strip.scroll == -9
    assert first_name(state=state) == 'S0'


def test_flip_resets_the_strip_scroll() -> None:
    """A new place starts its strip at the default rows."""
    other = replace(PIN, origin=place('D', name='D', point=CENTRE))
    saved = Saved(
        pins=(PIN, other),
        current=0,
        unpinned=None,
        view=LiveView.STRIP,
        zoom=DEFAULT_ZOOM,
    )
    state = initial_state(saved=saved, now=START, stored=True)
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    state, _ = step(state, turn(steps=4))
    assert state.strip.scroll == 4
    state, effects = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert state.strip.scroll == 0
    assert KEY not in state.strip.reaches
    assert kinds(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=strip_key(pin=other), origin=other.origin),
    ]


def test_strip_polls_positions_for_departure_modes() -> None:
    """The Strip polls only its own kind's positions, never area modes."""
    bus = departure(departs=START + timedelta(minutes=2), trip_id='bus')
    _, effects = on_view(view=LiveView.STRIP, departures=(bus, *trains()))
    fetches = kinds(effects=effects, kind=FetchFeed)
    assert FetchFeed(feed=TRAIN_POSITIONS, kind=FeedKind.POSITIONS) in fetches
    assert all(fetch.feed != BUS_POSITIONS for fetch in fetches)
    assert not kinds(effects=effects, kind=LoadModes)
    assert not kinds(effects=effects, kind=LoadShapes)


def test_strip_trips_patterns_requested() -> None:
    """The Strip asks for the patterns of its trips in the window."""
    late = departure(
        departs=START + timedelta(minutes=45),
        mode=Mode.TRAIN,
        trip_id='late',
        kind=RouteKind.TRAIN,
    )
    _, effects = on_view(view=LiveView.STRIP, departures=(*trains(), late))
    [load] = kinds(effects=effects, kind=LoadPatterns)
    assert load.trip_ids == {'train-0', 'train-1', 'train-2'}


def whole_stop(*, at: datetime) -> tuple[Departure, ...]:
    """
    Make 500 whole-stop departures, 60 of them trains in the strip window.

    Parameters
    ----------
    at : datetime
        Now.

    Returns
    -------
    tuple[Departure, ...]
        440 buses and 60 trains from 59 minutes ago to 29.5 minutes on,
        in departure order.
    """
    window = tuple(
        departure(
            departs=at + timedelta(minutes=-59, seconds=90 * i),
            mode=Mode.TRAIN,
            route='T1',
            trip_id=f'train-{i}',
            kind=RouteKind.TRAIN,
        )
        for i in range(60)
    )
    buses = tuple(
        departure(
            departs=at + timedelta(seconds=15 * i),
            trip_id=f'bus-{i}',
            kind=RouteKind.BUS,
        )
        for i in range(440)
    )
    return tuple(
        sorted((*window, *buses), key=lambda dep: dep.departs.timestamp())
    )


def test_strip_map_patterns_follow_the_strip() -> None:
    """With no destination, Strip + map loads exactly the strip's trips."""
    pin = replace(PIN, destination=None)
    _, effects = on_view(
        view=LiveView.STRIP_MAP,
        pin=pin,
        departures=whole_stop(at=START),
    )
    [load] = kinds(effects=effects, kind=LoadPatterns)
    assert load.trip_ids == {f'train-{i}' for i in range(60)}


def test_map_still_caps_a_whole_stop() -> None:
    """The plain Map keeps to the sidebar's departures."""
    pin = replace(PIN, destination=None)
    state, effects = on_view(
        view=LiveView.MAP,
        pin=pin,
        departures=whole_stop(at=START),
    )
    assert isinstance(state.last_view, MapView)
    [load] = kinds(effects=effects, kind=LoadPatterns)
    assert len(load.trip_ids) == len(state.last_view.sidebar)


def test_strip_trips_do_not_wait_for_the_fetch_in_flight() -> None:
    """A strip trip seen in an old download is asked for at once."""
    early = departure(
        departs=START + timedelta(minutes=1),
        mode=Mode.TRAIN,
        trip_id='early',
        kind=RouteKind.TRAIN,
    )
    state, _ = on_view(view=LiveView.STRIP, departures=(early,))
    seen = vehicle(timestamp=int(START.timestamp()), trip_id='train-0')
    state, _ = step(
        state,
        PositionsFetched(
            feed=TRAIN_POSITIONS,
            snapshot=snapshot(fetched_at=START, vehicles=(seen,)),
        ),
    )
    state, effects = step(state, Tick(now=START + RETRY_AFTER))
    fetch = FetchFeed(feed=TRAIN_POSITIONS, kind=FeedKind.POSITIONS)
    assert fetch in effects
    _, effects = step(
        state,
        DeparturesLoaded(
            pin=PIN,
            departures=(early, *trains()),
            until=START + HORIZON,
        ),
    )
    [load] = kinds(effects=effects, kind=LoadPatterns)
    assert 'train-0' in load.trip_ids


def test_to_stage_keeps_the_chooser_key_in_use() -> None:
    """The chooser's strip is kept while it picks a TO, not after a back."""
    pin = replace(PIN, kind=RouteKind.BUS)
    state, _ = on_view(view=LiveView.STOP, pin=pin)
    state, _ = run(state=state, events=list(TO_STAGE))
    assert keys_in_use(state=state) == {strip_key(pin=pin), CHOSEN_KEY}
    state, _ = step(state, StripLoaded(key=CHOSEN_KEY, reach=REACH))
    assert state.strip.reaches == {CHOSEN_KEY: REACH}
    state, _ = step(state, OPEN)
    assert keys_in_use(state=state) == {strip_key(pin=pin)}
    assert not state.strip.reaches


def test_to_stage_reuses_reach_after_commit() -> None:
    """The reach built for the TO stage serves the new place's Strip."""
    state, _ = on_view(view=LiveView.STOP, pin=replace(PIN, kind=None))
    state, effects = run(state=state, events=list(TO_STAGE))
    assert kinds(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=CHOSEN_KEY, origin=CHOSEN.origin),
    ]
    state, effects = run(
        state=state,
        events=[
            StripLoaded(key=CHOSEN_KEY, reach=REACH), PRESS, PRESS, PRESS,
        ],
    )
    assert state.chooser is None
    assert state.view is LiveView.STRIP
    assert not kinds(effects=effects, kind=LoadStrip)
    assert isinstance(state.last_view, StripView)
    assert state.last_view.rows[1].station == 'S2'
