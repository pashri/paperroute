"""Tests for the reducer."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.feeds import FeedKind
from paperroute.core.geo import Point, make_shape
from paperroute.core.map_state import area_box
from paperroute.core.map_view import DEFAULT_ZOOM, SIDEBAR_ROWS, MapView
from paperroute.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    Knob,
    KnobPressed,
    KnobTurned,
    LoadDepartures,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    MapRequest,
    ModesLoaded,
    PatternsLoaded,
    PositionsFetched,
    QuitRequested,
    Render,
    ShapesLoaded,
    Stop,
    Tick,
)
from paperroute.core.model import SYDNEY, Departure, Mode, Pin, shifted
from paperroute.core.realtime import RealtimeSnapshot, StopUpdate, TripRealtime
from paperroute.core.saved import LIVE_VIEWS, LiveView
from paperroute.core.state import initial_state, needed_feeds, step
from paperroute.core.state_model import (
    FEED_IDLE,
    LOOKBACK,
    POLL_EVERY,
    RELOAD_BEFORE,
    RETRY_AFTER,
    State,
)
from paperroute.core.stop_view import HORIZON, VISIBLE_ROWS, StopView
from paperroute.core.tiles import CITY, ZOOMS
from tests.core.factories import START, departure, pattern, snapshot, vehicle

PIN = Pin(origin='2145615', destination='215020')
BUSES = 'v1/gtfs/realtime/buses'
BUS_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/buses'
FERRY_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/ferries/sydneyferries'
CENTRE: Final[Point] = (-17_000.0, 1_000.0)
PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)


def fresh(*, now: datetime = START, centre: Point | None = None) -> State:
    """
    A state with nothing loaded.

    Parameters
    ----------
    now : datetime
        Start time.
    centre : Point | None
        The From stop in metres.

    Returns
    -------
    State
        Initial state.
    """
    return initial_state(
        pin=PIN,
        origin='Hollis St',
        destination='Parramatta',
        now=now,
        centre=centre,
    )


def loaded(
    *,
    state: State,
    count: int = 3,
    mode: Mode = Mode.BUS,
    shaped: bool = False,
) -> State:
    """
    Apply a DeparturesLoaded event.

    Parameters
    ----------
    state : State
        State before.
    count : int
        Departures, three minutes apart.
    mode : Mode
        Their mode.
    shaped : bool
        Give each departure its own shape ID.

    Returns
    -------
    State
        State after.
    """
    deps = tuple(
        replace(
            departure(
                departs=START + timedelta(minutes=3 * (i + 1)),
                mode=mode,
            ),
            shape_id=f'b:{i}' if shaped else None,
        )
        for i in range(count)
    )
    event = DeparturesLoaded(
        departures=deps, until=START + HORIZON + RELOAD_BEFORE
    )
    return step(state, event)[0]


def test_first_tick_loads_departures() -> None:
    """The first tick loads from an hour back to the horizon plus 30 min."""
    _, effects = step(fresh(), Tick(now=START))
    start = START - LOOKBACK
    end = START + HORIZON + RELOAD_BEFORE
    assert LoadDepartures(pin=PIN, start=start, end=end) in effects


def test_loaded_departures_render_and_choose_feeds() -> None:
    """Loading renders and the next tick fetches the bus feed."""
    state = loaded(state=fresh())
    assert state.feeds == (BUSES,)
    _, effects = step(state, Tick(now=START))
    assert FetchFeed(feed=BUSES) in effects


def test_fetch_waits_for_poll_interval() -> None:
    """A second tick within 30 s doesn't fetch again."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    _, effects = step(state, Tick(now=START + timedelta(seconds=10)))
    assert not any(isinstance(effect, FetchFeed) for effect in effects)


def test_poll_interval_counts_real_seconds_on_fall_back_night() -> None:
    """02:30 happens twice on 5 Apr 2026; an hour apart is due to poll."""
    first = datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY)
    second = datetime(2026, 4, 5, 2, 30, 10, tzinfo=SYDNEY, fold=1)
    assert second.timestamp() - first.timestamp() == 3610
    state = replace(loaded(state=fresh()), last_fetch=first)
    _, effects = step(state, Tick(now=second))
    assert FetchFeed(feed=BUSES) in effects


def test_step_no_feed_for_ferry_pin() -> None:
    """A ferry pin never fetches and shows the no-feed banner."""
    state, effects = step(
        loaded(state=fresh(), mode=Mode.FERRY), Tick(now=START)
    )
    assert state.feeds == ()
    assert not any(isinstance(effect, FetchFeed) for effect in effects)
    assert state.last_view is not None
    assert state.last_view.banner is not None
    assert 'no live feed' in state.last_view.banner


def test_feed_fetched_renders_once() -> None:
    """New data renders; an identical view isn't rendered again."""
    state = loaded(state=fresh())
    snap = RealtimeSnapshot(fetched_at=START, trips={})
    state, effects = step(state, FeedFetched(feed=BUSES, snapshot=snap))
    assert any(isinstance(effect, Render) for effect in effects)
    _, effects = step(state, FeedFetched(feed=BUSES, snapshot=snap))
    assert not any(isinstance(effect, Render) for effect in effects)


def test_step_feed_failure_keeps_timetable_and_retries() -> None:
    """After a failure the view keeps rows, goes stale, and retries later."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    snap = RealtimeSnapshot(fetched_at=START, trips={})
    state, _ = step(state, FeedFetched(feed=BUSES, snapshot=snap))
    failure = FeedFailed(
        feed=BUSES, at=START + timedelta(seconds=30), reason='HTTP 429'
    )
    state, _ = step(state, failure)
    later = START + timedelta(minutes=3)
    state, effects = step(state, Tick(now=later))
    assert FetchFeed(feed=BUSES) in effects
    assert isinstance(state.last_view, StopView)
    assert state.last_view.hero is not None
    assert state.last_view.banner == 'No live data since 8:00'


def test_failed_load_retries_on_next_tick() -> None:
    """A failed timetable read clears the loading flag so it reloads."""
    state, _ = step(fresh(), Tick(now=START))
    assert state.loading
    state, _ = step(state, DeparturesFailed())
    assert not state.loading
    _, effects = step(state, Tick(now=START + timedelta(seconds=5)))
    assert any(isinstance(effect, LoadDepartures) for effect in effects)


def test_bottom_turn_scrolls_and_top_does_not() -> None:
    """The bottom knob scrolls; the top knob does nothing yet."""
    state = loaded(state=fresh(), count=20)
    state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=2))
    assert state.scroll == 2
    state, effects = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert state.scroll == 2
    assert effects == ()


def test_reload_before_horizon_runs_out() -> None:
    """Departures reload once less than the horizon remains loaded."""
    state = loaded(state=fresh())
    soon = START + RELOAD_BEFORE - timedelta(minutes=1)
    _, early = step(state, Tick(now=soon))
    assert not any(isinstance(e, LoadDepartures) for e in early)
    later = START + RELOAD_BEFORE + timedelta(minutes=1)
    _, effects = step(state, Tick(now=later))
    assert any(isinstance(effect, LoadDepartures) for effect in effects)


def test_scroll_is_clamped_and_recorded() -> None:
    """Over-scrolling stores the clamped offset; turning back moves rows."""
    state = loaded(state=fresh(), count=20)
    state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=50))
    assert isinstance(state.last_view, StopView)
    assert state.scroll == state.last_view.scroll
    assert state.scroll < 50
    clamped_rows = state.last_view.rows
    state, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=-1))
    assert isinstance(state.last_view, StopView)
    assert state.scroll == state.last_view.scroll
    renders = [e for e in effects if isinstance(e, Render)]
    assert len(renders) == 1
    assert isinstance(renders[0].view, StopView)
    assert renders[0].view.rows != clamped_rows


def test_second_tick_does_not_load_again() -> None:
    """While a load is in flight, later ticks don't ask for another."""
    state, first = step(fresh(), Tick(now=START))
    assert any(isinstance(effect, LoadDepartures) for effect in first)
    _, second = step(state, Tick(now=START + timedelta(seconds=1)))
    assert not any(isinstance(effect, LoadDepartures) for effect in second)


def test_mixed_modes_fetch_each_needed_feed_once() -> None:
    """Each mode's feed is fetched once; a ferry adds none."""
    modes = [Mode.BUS, Mode.TRAIN, Mode.REGIONAL, Mode.FERRY]
    deps = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)), mode=mode)
        for i, mode in enumerate(modes)
    )
    event = DeparturesLoaded(
        departures=deps, until=START + HORIZON + RELOAD_BEFORE
    )
    state, _ = step(fresh(), event)
    _, effects = step(state, Tick(now=START))
    fetched = [e.feed for e in effects if isinstance(e, FetchFeed)]
    assert sorted(fetched) == [
        'v1/gtfs/realtime/buses',
        'v1/gtfs/realtime/nswtrains',
        'v2/gtfs/realtime/sydneytrains',
    ]


def test_load_window_is_real_seconds_on_fall_back_night() -> None:
    """The load window is measured in real seconds across the repeated hour."""
    now = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY)
    _, effects = step(fresh(now=now), Tick(now=now))
    loads = [e for e in effects if isinstance(e, LoadDepartures)]
    assert len(loads) == 1
    gap = loads[0].end.timestamp() - now.timestamp()
    assert gap == (HORIZON + RELOAD_BEFORE).total_seconds()
    back = now.timestamp() - loads[0].start.timestamp()
    assert back == LOOKBACK.total_seconds()


def test_quit_stops() -> None:
    """Quitting stops the loop."""
    state, effects = step(fresh(), QuitRequested())
    assert not state.running
    assert effects == (Stop(),)


def effects_of[T](
    *, effects: tuple[Effect, ...], kind: type[T]
) -> list[T]:
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


def turned(*, state: State, steps: int) -> tuple[State, tuple[Effect, ...]]:
    """
    Turn the bottom knob.

    Parameters
    ----------
    state : State
        State before.
    steps : int
        Detents, clockwise positive.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects after.
    """
    return step(state, KnobTurned(knob=Knob.BOTTOM, steps=steps))


def test_bottom_press_switches_to_map_and_back() -> None:
    """The bottom press toggles the Live view between Stop and Map."""
    state = loaded(state=fresh())
    assert state.view is LiveView.STOP
    state, _ = step(state, PRESS)
    assert state.view is LiveView.MAP
    assert isinstance(state.last_view, MapView)
    state, _ = step(state, PRESS)
    assert state.view is LiveView.STOP
    assert isinstance(state.last_view, StopView)


def test_entering_map_requests_modes_shapes_and_renders_map() -> None:
    """The Map asks for its area's modes and the pin's shapes, and draws."""
    state = loaded(state=fresh(centre=CENTRE), count=2, shaped=True)
    _, effects = step(state, PRESS)
    box = area_box(centre=CENTRE, mpp=8)
    assert LoadModes(mpp=8, box=box) in effects
    assert LoadShapes(shape_ids=frozenset({'b:0', 'b:1'})) in effects
    renders = effects_of(effects=effects, kind=Render)
    assert len(renders) == 1
    assert isinstance(renders[0].view, MapView)


def test_bottom_turn_zooms_on_map_and_scrolls_on_stop() -> None:
    """On the Stop view the turn scrolls; on the Map it zooms."""
    state = loaded(state=fresh(), count=20)
    state, _ = turned(state=state, steps=2)
    assert (state.scroll, state.map.zoom) == (2, DEFAULT_ZOOM)
    state, _ = step(state, PRESS)
    state, effects = turned(state=state, steps=1)
    assert ZOOMS[state.map.zoom] == 5
    assert state.scroll == 2
    renders = effects_of(effects=effects, kind=Render)
    assert len(renders) == 1
    assert isinstance(renders[0].view, MapView)
    assert renders[0].view.zoom == 1


def test_zoom_clamps_at_both_ends() -> None:
    """Spinning either way stops at 3 and 45 m/px."""
    state, _ = step(loaded(state=fresh()), PRESS)
    state, _ = turned(state=state, steps=10)
    assert ZOOMS[state.map.zoom] == 3
    state, _ = turned(state=state, steps=1)
    assert ZOOMS[state.map.zoom] == 3
    state, _ = turned(state=state, steps=-10)
    assert ZOOMS[state.map.zoom] == 45
    state, _ = turned(state=state, steps=-1)
    assert ZOOMS[state.map.zoom] == 45


def test_zoom_is_kept_after_returning_to_stop() -> None:
    """Leaving the Map and coming back keeps its zoom and the Stop scroll."""
    state = loaded(state=fresh(), count=20)
    state, _ = turned(state=state, steps=3)
    state, _ = step(state, PRESS)
    state, _ = turned(state=state, steps=-1)
    state, _ = step(state, PRESS)
    assert isinstance(state.last_view, StopView)
    assert state.last_view.scroll == 3
    state, _ = step(state, PRESS)
    assert isinstance(state.last_view, MapView)
    assert ZOOMS[state.last_view.zoom] == 14


def test_modes_loaded_fetches_position_feeds_now() -> None:
    """The area's modes start their position feeds without waiting."""
    state, _ = step(fresh(centre=CENTRE), PRESS)
    event = ModesLoaded(mpp=8, modes=frozenset({Mode.BUS}))
    state, effects = step(state, event)
    fetch = FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)
    assert effects_of(effects=effects, kind=FetchFeed) == [fetch]
    assert state.map.area_modes == {8: frozenset({Mode.BUS})}
    assert not state.map.pending_modes


def test_map_polls_positions_with_trip_updates() -> None:
    """On the Map a due tick fetches both trip updates and positions."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    state, effects = step(state, PRESS)
    fetch = FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)
    assert effects_of(effects=effects, kind=FetchFeed) == [fetch]
    _, effects = step(state, Tick(now=START + timedelta(seconds=10)))
    assert not effects_of(effects=effects, kind=FetchFeed)
    _, effects = step(state, Tick(now=START + POLL_EVERY))
    assert effects_of(effects=effects, kind=FetchFeed) == [
        FetchFeed(feed=BUSES),
        fetch,
    ]


def test_stop_view_never_polls_positions() -> None:
    """The Stop view fetches trip updates only."""
    state = loaded(state=fresh(centre=CENTRE))
    for seconds in (0, 30, 60):
        now = START + timedelta(seconds=seconds)
        state, effects = step(state, Tick(now=now))
        fetched = effects_of(effects=effects, kind=FetchFeed)
        assert fetched == [FetchFeed(feed=BUSES)]
    assert needed_feeds(state=state) == (BUSES,)


def test_map_polls_positions_for_a_ferry_pin() -> None:
    """A ferry pin has no trip feed, yet the Map polls ferry positions."""
    state, _ = step(loaded(state=fresh(), mode=Mode.FERRY), PRESS)
    assert state.feeds == ()
    fetch = FetchFeed(feed=FERRY_POSITIONS, kind=FeedKind.POSITIONS)
    for seconds in (30, 60, 90):
        now = START + timedelta(seconds=seconds)
        state, effects = step(state, Tick(now=now))
        assert effects_of(effects=effects, kind=FetchFeed) == [fetch]


def test_empty_area_modes_fall_back_to_departure_modes() -> None:
    """With no modes found in the area, the departures' modes are used."""
    state, _ = step(loaded(state=fresh(centre=CENTRE)), PRESS)
    empty = ModesLoaded(mpp=8, modes=frozenset())
    state, _ = step(state, empty)
    assert needed_feeds(state=state) == (BUSES, BUS_POSITIONS)
    ferry = ModesLoaded(mpp=8, modes=frozenset({Mode.FERRY}))
    state, _ = step(state, ferry)
    assert needed_feeds(state=state) == (BUSES, FERRY_POSITIONS)


def test_modes_requested_once_per_zoom() -> None:
    """Spinning back and forth asks for each zoom's modes once."""
    state, effects = step(fresh(centre=CENTRE), PRESS)
    seen = list(effects)
    for _ in range(5):
        for steps in (1, -1, -1, 1):
            state, effects = turned(state=state, steps=steps)
            seen.extend(effects)
        state, effects = step(state, PRESS)
        seen.extend(effects)
        state, effects = step(state, PRESS)
        seen.extend(effects)
    loads = effects_of(effects=tuple(seen), kind=LoadModes)
    assert Counter(load.mpp for load in loads) == {5: 1, 8: 1, 14: 1}


def test_patterns_requested_once() -> None:
    """The pin's trip patterns are asked for once, then kept."""
    state = loaded(state=fresh(centre=CENTRE))
    trips = frozenset(dep.trip_id for dep in state.departures)
    state, effects = step(state, PRESS)
    load = LoadPatterns(trip_ids=trips, day=START.date())
    assert effects_of(effects=effects, kind=LoadPatterns) == [load]
    state, effects = step(state, Tick(now=START + timedelta(seconds=5)))
    assert not effects_of(effects=effects, kind=LoadPatterns)
    patterns = dict.fromkeys((*trips, 'gone'))
    state, effects = step(
        state,
        PatternsLoaded(trip_ids=trips, patterns=patterns, shapes={}),
    )
    assert not effects_of(effects=effects, kind=LoadPatterns)
    assert set(state.map.patterns) == trips
    assert not state.map.pending_patterns
    _, effects = step(state, Tick(now=START + timedelta(seconds=10)))
    assert not effects_of(effects=effects, kind=LoadPatterns)


def test_map_data_failure_retries_after_backoff() -> None:
    """A failed load is asked for again 30 s later, not before."""
    state = loaded(state=fresh(centre=CENTRE), shaped=True)
    state, effects = step(state, PRESS)
    requests: list[MapRequest] = [
        *effects_of(effects=effects, kind=LoadModes),
        *effects_of(effects=effects, kind=LoadShapes),
        *effects_of(effects=effects, kind=LoadPatterns),
    ]
    assert len(requests) == 3
    for request in requests:
        state, failed = step(state, MapDataFailed(request=request))
        assert failed == ()
    state, effects = step(state, Tick(now=START + timedelta(seconds=29)))
    assert not effects_of(effects=effects, kind=LoadModes)
    assert not effects_of(effects=effects, kind=LoadShapes)
    assert not effects_of(effects=effects, kind=LoadPatterns)
    state, effects = step(state, Tick(now=START + RETRY_AFTER))
    assert len(effects_of(effects=effects, kind=LoadModes)) == 1
    assert len(effects_of(effects=effects, kind=LoadShapes)) == 1
    assert len(effects_of(effects=effects, kind=LoadPatterns)) == 1


def test_map_data_failure_clears_only_its_request() -> None:
    """A failed zoom is retried; another zoom still pending is not."""
    state, _ = step(fresh(centre=CENTRE), PRESS)
    state, effects = turned(state=state, steps=1)
    [five] = effects_of(effects=effects, kind=LoadModes)
    state, _ = step(state, MapDataFailed(request=five))
    assert state.map.pending_modes == {8}
    _, effects = step(state, Tick(now=START + RETRY_AFTER))
    assert effects_of(effects=effects, kind=LoadModes) == [five]


def test_unknown_trips_do_not_stay_pending() -> None:
    """Trips the timetable doesn't know are stored as None, not re-asked."""
    state, effects = step(loaded(state=fresh(centre=CENTRE)), PRESS)
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    event = PatternsLoaded(trip_ids=load.trip_ids, patterns={}, shapes={})
    state, _ = step(state, event)
    assert not state.map.pending_patterns
    assert state.map.patterns == dict.fromkeys(load.trip_ids)
    _, effects = step(state, Tick(now=START + timedelta(minutes=5)))
    assert not effects_of(effects=effects, kind=LoadPatterns)


def test_load_patterns_day_is_the_sydney_date() -> None:
    """At 01:30 in Sydney the UTC date is a day behind; Sydney's is used."""
    now = datetime(2026, 10, 16, 14, 30, tzinfo=UTC)
    state = loaded(state=fresh(now=now, centre=CENTRE))
    _, effects = step(state, PRESS)
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert load.day == date(2026, 10, 17)


def test_map_sidebar_starts_at_the_next_departure() -> None:
    """Scrolling the Stop view doesn't shift the Map's sidebar."""
    state = loaded(state=fresh(), count=20)
    assert isinstance(state.last_view, StopView)
    assert state.last_view.hero is not None
    unscrolled = (state.last_view.hero, *state.last_view.rows)
    state, _ = turned(state=state, steps=4)
    state, _ = step(state, PRESS)
    assert isinstance(state.last_view, MapView)
    assert state.last_view.sidebar == unscrolled[:SIDEBAR_ROWS]
    assert state.scroll == 4


def test_positions_are_not_fetched_twice_within_a_poll() -> None:
    """The first fetch on entering the Map starts that feed's 30 s clock."""
    state, effects = step(loaded(state=fresh(), mode=Mode.FERRY), PRESS)
    fetch = FetchFeed(feed=FERRY_POSITIONS, kind=FeedKind.POSITIONS)
    assert effects_of(effects=effects, kind=FetchFeed) == [fetch]
    _, effects = step(state, Tick(now=START + timedelta(seconds=1)))
    assert not effects_of(effects=effects, kind=FetchFeed)
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    state, _ = step(state, Tick(now=START + timedelta(seconds=29)))
    state, _ = step(state, PRESS)
    _, effects = step(state, Tick(now=START + POLL_EVERY))
    assert effects_of(effects=effects, kind=FetchFeed) == [
        FetchFeed(feed=BUSES),
    ]


def test_returning_to_the_map_fetches_at_once() -> None:
    """Coming back to the Map after 30 s fetches positions straight away."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    state, _ = step(state, PRESS)
    state, _ = step(state, Tick(now=START + timedelta(seconds=29)))
    state, _ = step(state, PRESS)
    state, _ = step(state, Tick(now=START + timedelta(seconds=151)))
    _, effects = step(state, PRESS)
    fetch = FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)
    assert effects_of(effects=effects, kind=FetchFeed) == [fetch]


def test_map_draws_only_the_feeds_it_polls() -> None:
    """A stored download from a feed the Map doesn't poll isn't drawn."""
    state, _ = step(loaded(state=fresh(centre=CENTRE)), PRESS)
    boat = vehicle(timestamp=int(START.timestamp()), point=CENTRE)
    positions = snapshot(fetched_at=START, vehicles=(boat,))
    event = PositionsFetched(feed=FERRY_POSITIONS, snapshot=positions)
    state, _ = step(state, event)
    assert isinstance(state.last_view, MapView)
    assert state.last_view.markers == ()


def test_departures_loaded_on_the_map_request_shapes() -> None:
    """New departures on the Map ask for their shapes at once."""
    state, _ = step(fresh(centre=CENTRE), PRESS)
    state = loaded(state=state, count=1, shaped=True)
    assert state.map.requested_shapes == {'b:0'}


def test_idle_position_feeds_are_dropped_after_ten_minutes() -> None:
    """A feed not fetched for 10 min loses its stored positions."""
    state, _ = step(loaded(state=fresh(centre=CENTRE)), PRESS)
    positions = snapshot(fetched_at=START, vehicles=())
    event = PositionsFetched(feed=BUS_POSITIONS, snapshot=positions)
    state, _ = step(state, event)
    state, _ = step(state, PRESS)
    almost = START + FEED_IDLE - timedelta(seconds=1)
    state, _ = step(state, Tick(now=almost))
    assert BUS_POSITIONS in state.map.positions
    state, _ = step(state, Tick(now=START + FEED_IDLE))
    assert not state.map.positions
    assert not state.map.fetched_at


def test_loaded_shapes_and_positions_are_drawn() -> None:
    """Shapes draw the route line and positions draw markers."""
    state, _ = step(loaded(state=fresh(centre=CENTRE), shaped=True), PRESS)
    line = make_shape(shape_id='b:0', points=[CENTRE, (-16_900.0, 1_000.0)])
    state, effects = step(state, ShapesLoaded(shapes={'b:0': line}))
    assert isinstance(state.last_view, MapView)
    assert state.last_view.base.route == (line.points,)
    bus = vehicle(timestamp=int(START.timestamp()), point=CENTRE)
    positions = snapshot(fetched_at=START, vehicles=(bus,))
    event = PositionsFetched(feed=BUS_POSITIONS, snapshot=positions)
    state, effects = step(state, event)
    assert state.map.positions == {BUS_POSITIONS: positions}
    renders = effects_of(effects=effects, kind=Render)
    assert len(renders) == 1
    assert isinstance(state.last_view, MapView)
    assert [marker.live for marker in state.last_view.markers] == [True]


FALL_BACK_EVE = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY)
MODES = [Mode.BUS, Mode.TRAIN, Mode.REGIONAL, Mode.FERRY]


@st.composite
def map_events(draw: st.DrawFn, now: datetime) -> Event:
    """
    A random map data event.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    now : datetime
        Current time.

    Returns
    -------
    Event
        Shapes, patterns, modes, positions or a failure.
    """
    trips = frozenset(draw(st.sets(st.sampled_from(['trip-0800', 'x']))))
    mpp = draw(st.sampled_from(ZOOMS))
    line = make_shape(shape_id='b:1', points=[CENTRE, (0.0, 0.0)])
    bus = vehicle(timestamp=int(now.timestamp()), point=CENTRE)
    requests: list[MapRequest] = [
        LoadShapes(shape_ids=frozenset({'b:1', 'b:2'})),
        LoadPatterns(trip_ids=trips, day=now.date()),
        LoadModes(mpp=mpp, box=area_box(centre=CENTRE, mpp=mpp)),
    ]
    request = draw(st.sampled_from(requests))
    feed = draw(st.sampled_from([BUS_POSITIONS, FERRY_POSITIONS]))
    options: list[Event] = [
        ShapesLoaded(shapes={'b:1': line}),
        PatternsLoaded(trip_ids=trips, patterns={}, shapes={}),
        ModesLoaded(
            mpp=mpp,
            modes=frozenset(draw(st.sets(st.sampled_from(MODES)))),
        ),
        PositionsFetched(
            feed=feed,
            snapshot=snapshot(fetched_at=now, vehicles=(bus,)),
        ),
        MapDataFailed(request=request),
    ]
    return draw(st.sampled_from(options))


@st.composite
def event_lists(draw: st.DrawFn, start: datetime) -> list[Event]:
    """
    Random but time-ordered event sequences.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.
    start : datetime
        Time of the first event.

    Returns
    -------
    list[Event]
        Events.
    """
    now, events = start, list[Event]()
    kinds = [
        'tick',
        'turn',
        'press',
        'fail',
        'ok',
        'load',
        'unreadable',
        'map',
    ]
    for kind in draw(st.lists(st.sampled_from(kinds), max_size=40)):
        if kind == 'tick':
            now = shifted(moment=now, seconds=draw(st.integers(0, 900)))
            events.append(Tick(now=now))
        elif kind == 'turn':
            knob = draw(st.sampled_from(list(Knob)))
            steps = draw(st.integers(-10, 10))
            events.append(KnobTurned(knob=knob, steps=steps))
        elif kind == 'press':
            events.append(KnobPressed(knob=draw(st.sampled_from(list(Knob)))))
        elif kind == 'fail':
            events.append(FeedFailed(feed=BUSES, at=now, reason='timeout'))
        elif kind == 'unreadable':
            events.append(DeparturesFailed())
        elif kind == 'ok':
            snap = RealtimeSnapshot(fetched_at=now, trips={})
            events.append(FeedFetched(feed=BUSES, snapshot=snap))
        elif kind == 'map':
            events.append(draw(map_events(now=now)))
        else:
            count = draw(st.integers(0, 25))
            deps = tuple(
                replace(
                    departure(
                        departs=shifted(moment=now, seconds=120 * i),
                        mode=draw(st.sampled_from(MODES)),
                    ),
                    shape_id=draw(st.sampled_from([None, 'b:1', 'b:2'])),
                )
                for i in range(count)
            )
            until = shifted(moment=now, seconds=3 * 3600 + 1800)
            events.append(DeparturesLoaded(departures=deps, until=until))
    return events


def check_effects(
    *, before: State, after: State, event: Event, effects: tuple[Effect, ...]
) -> None:
    """
    Assert what must hold after any single step.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    effects : tuple[Effect, ...]
        Effects returned.
    """
    assert after.scroll >= 0
    assert 0 <= after.map.zoom < len(ZOOMS)
    assert after.view in LIVE_VIEWS
    if isinstance(after.last_view, StopView):
        assert after.scroll == after.last_view.scroll
    check_map_requests(before=before, after=after, effects=effects)
    for effect in effects:
        if isinstance(effect, Render):
            check_render(view=effect.view)
            assert effect.view != before.last_view
        if isinstance(effect, LoadDepartures):
            assert not before.loading
        if isinstance(effect, FetchFeed):
            check_fetch(before=before, after=after, event=event, effect=effect)


def check_map_requests(
    *, before: State, after: State, effects: tuple[Effect, ...]
) -> None:
    """
    Assert map data is asked for only on the Map, and modes once per zoom.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects returned.
    """
    requests = [
        effect
        for effect in effects
        if isinstance(effect, (LoadShapes, LoadPatterns, LoadModes))
        or (
            isinstance(effect, FetchFeed)
            and effect.kind is FeedKind.POSITIONS
        )
    ]
    if after.view is LiveView.STOP:
        assert not requests
    known = before.map.area_modes.keys() | before.map.pending_modes
    for load in effects_of(effects=effects, kind=LoadModes):
        assert load.mpp not in known


def check_render(*, view: StopView | MapView) -> None:
    """
    Assert a rendered view fits the screen.

    Parameters
    ----------
    view : StopView | MapView
        The view drawn.
    """
    if isinstance(view, StopView):
        assert len(view.rows) <= VISIBLE_ROWS
    else:
        assert len(view.sidebar) <= SIDEBAR_ROWS


def polled(*, before: State, event: Event) -> bool:
    """
    Say whether an event is a tick at least POLL_EVERY after the last fetch.

    Parameters
    ----------
    before : State
        State before the event.
    event : Event
        The event applied.

    Returns
    -------
    bool
        True for a due tick.
    """
    if not isinstance(event, Tick):
        return False
    if before.last_fetch is None:
        return True
    gap = event.now.timestamp() - before.last_fetch.timestamp()
    return gap >= POLL_EVERY.total_seconds()


def check_fetch(
    *, before: State, after: State, event: Event, effect: FetchFeed
) -> None:
    """
    Assert a fetch is needed and due on its own clock.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    effect : FetchFeed
        The fetch.
    """
    if effect.kind is FeedKind.TRIPS:
        assert effect.feed in after.feeds
        assert polled(before=before, event=event)
        return
    assert effect.feed in needed_feeds(state=after)
    last = before.map.fetched_at.get(effect.feed)
    if last is not None:
        gap = after.now.timestamp() - last.timestamp()
        assert gap >= POLL_EVERY.total_seconds()


@given(
    st.sampled_from([START, FALL_BACK_EVE]).flatmap(
        lambda start: st.tuples(st.just(start), event_lists(start))
    ),
    st.sampled_from([None, CENTRE]),
)
def test_step_invariants(
    case: tuple[datetime, list[Event]], centre: Point | None
) -> None:
    """No event sequence breaks the reducer's invariants."""
    start, events = case
    state = fresh(now=start, centre=centre)
    for event in events:
        before = state
        state, effects = step(before, event)
        check_effects(
            before=before, after=state, event=event, effects=effects
        )


@dataclass
class WindowedTimetable:
    """A timetable that answers a load with what falls in its window."""

    departures: tuple[Departure, ...]

    def load(self, *, effect: LoadDepartures) -> DeparturesLoaded:
        """
        Answer a load request.

        Parameters
        ----------
        effect : LoadDepartures
            The request.

        Returns
        -------
        DeparturesLoaded
            Departures from start to end, as the repository returns them.
        """
        found = tuple(
            dep
            for dep in self.departures
            if effect.start.timestamp()
            <= dep.departs.timestamp()
            < effect.end.timestamp()
        )
        return DeparturesLoaded(departures=found, until=effect.end)


def run_tick(
    *, state: State, timetable: WindowedTimetable, now: datetime
) -> State:
    """
    Tick, then answer any load request at once.

    Parameters
    ----------
    state : State
        State before.
    timetable : WindowedTimetable
        Where loads are answered.
    now : datetime
        Tick time.

    Returns
    -------
    State
        State after the tick and the load.
    """
    state, effects = step(state, Tick(now=now))
    for effect in effects:
        if isinstance(effect, LoadDepartures):
            state, _ = step(state, timetable.load(effect=effect))
    return state


def test_empty_never_shows_while_a_departure_is_within_horizon() -> None:
    """Across many load cycles the list always reaches a full horizon."""
    only = departure(departs=START + timedelta(hours=5, minutes=30))
    timetable = WindowedTimetable(departures=(only,))
    state = fresh()
    for minute in range(0, 6 * 60 + 1):
        now = START + timedelta(minutes=minute)
        state = run_tick(state=state, timetable=timetable, now=now)
        assert state.last_view is not None
        in_reach = only.departs <= now + HORIZON
        if in_reach and only.departs >= now:
            assert state.last_view.empty is None, now


def test_late_service_timetabled_before_now_appears_after_load() -> None:
    """A service timetabled 2 min ago but 6 min late is still listed."""
    late = departure(departs=START - timedelta(minutes=2))
    timetable = WindowedTimetable(departures=(late,))
    trip = TripRealtime(
        trip_id=late.trip_id,
        cancelled=False,
        updates=(
            StopUpdate(
                sequence=late.origin_seq,
                stop_id=None,
                arrival_delay=None,
                departure_delay=360,
                arrival_time=None,
                departure_time=None,
            ),
        ),
        start_date=None,
    )
    state = run_tick(state=fresh(), timetable=timetable, now=START)
    snapshot = RealtimeSnapshot(fetched_at=START, trips={late.trip_id: trip})
    state, _ = step(state, FeedFetched(feed=BUSES, snapshot=snapshot))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.hero is not None
    assert state.last_view.hero.leaves_at == '8:04'


def test_view_says_loading_before_the_first_load() -> None:
    """Until departures have loaded, the empty text says so."""
    state, _ = step(fresh(), Tick(now=START))
    assert state.last_view is not None
    assert state.last_view.empty == 'Loading timetable'
    loaded_state, _ = step(
        state, DeparturesLoaded(departures=(), until=START + HORIZON)
    )
    assert loaded_state.last_view is not None
    assert loaded_state.last_view.empty is not None
    assert 'No direct services' in loaded_state.last_view.empty


def city_map(*, count: int = 1) -> State:
    """
    Show the Map at city zoom with shaped pin departures.

    Parameters
    ----------
    count : int
        Pin departures, each with its own shape.

    Returns
    -------
    State
        The state on the Map at 45 m/px.
    """
    state = loaded(state=fresh(centre=CENTRE), count=count, shaped=True)
    state, _ = step(state, PRESS)
    state, _ = turned(state=state, steps=-len(ZOOMS))
    assert ZOOMS[state.map.zoom] == CITY
    return state


def stale_bus(*, trip_id: str, landed: datetime) -> PositionsFetched:
    """
    Land a bus download holding one vehicle unheard for ten minutes.

    Parameters
    ----------
    trip_id : str
        The vehicle's trip.
    landed : datetime
        When the download landed.

    Returns
    -------
    PositionsFetched
        The download, with the vehicle at the map centre.
    """
    bus = vehicle(
        timestamp=int((landed - timedelta(minutes=10)).timestamp()),
        trip_id=trip_id,
        point=CENTRE,
    )
    return PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=landed, vehicles=(bus,)),
    )


def stale_pattern_loaded(*, trip_id: str, shape_id: str) -> PatternsLoaded:
    """
    Load one stale trip's pattern with its own shape.

    Parameters
    ----------
    trip_id : str
        The trip.
    shape_id : str
        Its shape.

    Returns
    -------
    PatternsLoaded
        The pattern and its shape.
    """
    line = make_shape(shape_id=shape_id, points=[CENTRE, (0.0, 0.0)])
    return PatternsLoaded(
        trip_ids=frozenset({trip_id}),
        patterns={trip_id: pattern(trip_id=trip_id, shape_id=shape_id)},
        shapes={shape_id: line},
    )


def test_shapes_of_dropped_patterns_are_dropped() -> None:
    """A stale trip's shape goes with its pattern; the pin's shape stays."""
    state = city_map()
    pin_line = make_shape(shape_id='b:0', points=[CENTRE, (0.0, 0.0)])
    state, _ = step(state, ShapesLoaded(shapes={'b:0': pin_line}))
    state, _ = step(state, stale_bus(trip_id='gone', landed=START))
    state, _ = step(
        state,
        stale_pattern_loaded(trip_id='gone', shape_id='s:gone'),
    )
    assert set(state.map.shapes) == {'b:0', 's:gone'}
    state, _ = step(state, stale_bus(trip_id='other', landed=START))
    assert 'gone' not in state.map.patterns
    assert set(state.map.shapes) == {'b:0'}


def test_shapes_stay_bounded_at_city_zoom() -> None:
    """Stale trips coming and going never pile up shapes."""
    state = city_map()
    for index in range(20):
        landed = START + timedelta(seconds=index)
        trip_id = f'stale-{index}'
        state, effects = step(state, stale_bus(trip_id=trip_id, landed=landed))
        [load] = effects_of(effects=effects, kind=LoadPatterns)
        assert trip_id in load.trip_ids
        state, _ = step(
            state,
            stale_pattern_loaded(trip_id=trip_id, shape_id=f's:{index}'),
        )
        assert set(state.map.shapes) <= {'b:0', f's:{index}'}


def test_unneeded_pin_shapes_can_be_asked_for_again() -> None:
    """A pin shape dropped with its departure is requested when it returns."""
    state = city_map(count=2)
    lines = {
        shape_id: make_shape(shape_id=shape_id, points=[CENTRE, (0.0, 0.0)])
        for shape_id in ('b:0', 'b:1')
    }
    state, _ = step(state, ShapesLoaded(shapes=lines))
    state = loaded(state=state, count=1, shaped=True)
    assert set(state.map.shapes) == {'b:0'}
    assert state.map.requested_shapes == {'b:0'}
    state = loaded(state=state, count=2, shaped=True)
    assert state.map.requested_shapes == {'b:0', 'b:1'}


def test_stale_patterns_wait_for_the_fetch_in_flight() -> None:
    """Old positions on return to the Map ask for no stale patterns."""
    state = city_map()
    bus = vehicle(timestamp=int(START.timestamp()), point=CENTRE)
    event = PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=START, vehicles=(bus,)),
    )
    state, _ = step(state, event)
    state, _ = step(state, PRESS)
    later = START + timedelta(minutes=5)
    state, _ = step(state, Tick(now=later))
    state, effects = step(state, PRESS)
    fetch = FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)
    assert fetch in effects
    assert not any(
        't1' in load.trip_ids
        for load in effects_of(effects=effects, kind=LoadPatterns)
    )
    state, _ = step(state, Tick(now=later + timedelta(seconds=5)))
    assert 't1' not in state.map.pending_patterns
    landed = replace(
        event,
        snapshot=snapshot(fetched_at=later, vehicles=(bus,)),
    )
    _, effects = step(state, landed)
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert 't1' in load.trip_ids


def test_pin_patterns_do_not_wait_for_the_fetch_in_flight() -> None:
    """A pin trip whose vehicle went stale is asked for at once."""
    state = city_map()
    [pin] = state.departures
    state, _ = step(
        state,
        PatternsLoaded(
            trip_ids=frozenset({pin.trip_id}),
            patterns={pin.trip_id: pattern(trip_id=pin.trip_id)},
            shapes={},
        ),
    )
    bus = vehicle(
        timestamp=int(START.timestamp()),
        trip_id=pin.trip_id,
        point=CENTRE,
    )
    state, _ = step(
        state,
        PositionsFetched(
            feed=BUS_POSITIONS,
            snapshot=snapshot(fetched_at=START, vehicles=(bus,)),
        ),
    )
    assert pin.trip_id not in state.map.patterns
    state, _ = step(state, PRESS)
    state, _ = step(state, Tick(now=START + timedelta(minutes=5)))
    _, effects = step(state, PRESS)
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert pin.trip_id in load.trip_ids
