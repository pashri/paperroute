"""Tests for the reducer."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.alert_state import ALERT_EVERY, alert_feeds
from paperroute.core.chooser import (
    ChooserPath,
    ChooserState,
    Column,
    Level,
    Stage,
    chosen,
    chosen_pin,
    focusable,
)
from paperroute.core.chooser_steps import trip_feeds
from paperroute.core.chooser_view import ChooserView
from paperroute.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute.core.connection_view import ConnectionView
from paperroute.core.cycle import cycle_due, needed_feeds
from paperroute.core.feeds import (
    ALL_POSITION_MODES,
    FeedKind,
    feeds_near,
    position_feeds,
)
from paperroute.core.map_requests import map_feeds, shows_vehicles
from paperroute.core.map_state import area_box, area_frame
from paperroute.core.map_view import DEFAULT_ZOOM, MAP_WIDTH, MapView
from paperroute.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    HolidaysLoaded,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadDepartures,
    LoadHolidays,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    LoadStrip,
    MapData,
    MapDataFailed,
    MapRequest,
    MatchPlaces,
    ModesLoaded,
    PatternsLoaded,
    PlacesFailed,
    PlacesMatched,
    PositionsFetched,
    ProbeDevice,
    QuitRequested,
    Render,
    SaveState,
    ScreenView,
    SettingsChanged,
    ShapesLoaded,
    Stop,
    StripFailed,
    StripLoaded,
    Tick,
    TimetableSeen,
)
from paperroute.core.panel_views import SleepView
from paperroute.core.places import shown, stops_to_match
from paperroute.core.quota import QUOTA_SAVE_EVERY, quota_only
from paperroute.core.rebuild import building_view
from paperroute.core.rhythm import poll_every
from paperroute.core.saved import (
    DEFAULT_SAVED,
    LIVE_VIEWS,
    TOWN_HALL,
    BoardSettings,
    LiveView,
    Rate,
    Saved,
    Settings,
    seeded,
)
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import (
    CLOCK_WAIT,
    FEED_IDLE,
    LOOKBACK,
    POLL_EVERY,
    RELOAD_BEFORE,
    RETRY_AFTER,
    State,
)
from paperroute.core.state_model import clock_waiting as clock_held
from paperroute.core.state_model import load_window
from paperroute.core.state_saves import SAVE_AFTER
from paperroute.core.state_views import live_stop_view, saved_of
from paperroute.core.stop_view import (
    HORIZON,
    SIDEBAR_ROWS,
    VISIBLE_ROWS,
    StopView,
)
from paperroute.core.strip_follow import origin_of
from paperroute.core.strip_model import (
    PANE_ROWS,
    STRIP_ROWS,
    StripMapView,
    StripView,
    trip_day,
)
from paperroute.core.strip_select import panel_trains
from paperroute.core.strip_state import (
    STRIP_VIEWS,
    keys_in_use,
    picking_to,
    scroll_range,
    shown_reach,
    strip_key,
)
from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import PowerAction
from paperroute_device.core.device_settings import Window
from paperroute_device.core.panel_views import NoticeView
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_testkit.core.connection_factories import WORK
from paperroute_testkit.core.factories import (
    START,
    begun,
    departure,
    pattern,
    pin_at,
    place,
    snapshot,
    vehicle,
)
from paperroute_testkit.core.steps import (
    answer_fetches,
    step_answered,
    step_untouched,
    wakeful,
)
from paperroute_transit.core.auto_model import AutoState
from paperroute_transit.core.geo import Point, make_shape
from paperroute_transit.core.model import (
    SYDNEY,
    Departure,
    Mode,
    Pin,
    RouteKind,
    is_connection,
    pin_key,
    same_place,
    shifted,
)
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RowKind,
    RunTail,
    build_reach,
)
from paperroute_transit.core.realtime import (
    RealtimeSnapshot,
    StopUpdate,
    TripRealtime,
)
from paperroute_transit.core.tiles import CITY, ZOOMS
from tests.core.chooser_walks import (
    BUS_606,
    CHOSEN,
    DOWN,
    GROUPS,
    KINDS,
    LINE,
    METRO_LINES,
    NEXT,
    OPEN,
    PENRITH,
    ROUTES,
    T1_STATIONS,
    T1_TO,
    TOWARDS,
    TRAIN_LINES,
    UNTIL,
    chooser_departures,
)

PIN = Pin(
    origin=place('2145615', name='Hollis St'),
    destination=place('215020', name='Parramatta'),
)
BUSES = 'v1/gtfs/realtime/buses'
ALERTS: Final[str] = 'v2/gtfs/alerts/buses'
BUS_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/buses'
FERRY_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/ferries/sydneyferries'
CENTRE: Final[Point] = (-17_000.0, 1_000.0)
PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
PINS: Final[tuple[Pin, ...]] = (
    pin_at('first', x=100.0),
    pin_at('second', x=200.0),
    pin_at('third', x=300.0),
)
ELSEWHERE: Final[Pin] = pin_at('elsewhere', x=900.0)
POOL: Final[tuple[Pin, ...]] = (
    PIN,
    *PINS,
    ELSEWHERE,
    Pin(origin=TOWN_HALL, kind=RouteKind.TRAIN),
)


def fresh(*, now: datetime = START, centre: Point | None = None) -> State:
    """
    A state with nothing loaded, started long enough ago to draw.

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
    pin = replace(PIN, origin=replace(PIN.origin, point=centre))
    return begun(saved=seeded(pin=pin), now=now)


def every_position_fetch() -> list[FetchFeed]:
    """
    List the fetches for every mode's positions feeds.

    Returns
    -------
    list[FetchFeed]
        One per positions feed, in polling order.
    """
    return [
        FetchFeed(feed=feed, kind=FeedKind.POSITIONS)
        for feed in position_feeds(modes=ALL_POSITION_MODES)
    ]


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
        pin=shown(state=state),
        departures=deps,
        until=max(START, state.now) + HORIZON + RELOAD_BEFORE,
    )
    return step_answered(state=state, event=event)[0]


def test_initial_state_centres_on_the_pins_point() -> None:
    """The Map starts centred on the pin's From."""
    assert fresh(centre=CENTRE).map.centre == CENTRE
    assert fresh().map.centre is None


def test_start_has_step_zero() -> None:
    """The Map starts with no knob step, at the resting zoom."""
    assert fresh(centre=CENTRE).map.step == 0
    assert fresh(centre=CENTRE).map.frame is None


def test_stop_view_names_the_pins_places() -> None:
    """The Stop view's header names the pin's From and Destination."""
    state, _ = step(fresh(), Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert (state.last_view.origin, state.last_view.destination) == (
        'Hollis St',
        'Parramatta',
    )


def test_header_shows_nickname() -> None:
    """The tag reads '<nickname> n/total'; unpinning drops it."""
    pins = (replace(PIN, nickname='Home'), PINS[0])
    saved = replace(seeded(pin=PIN), pins=pins)
    state = begun(saved=saved)
    state, _ = step(state, Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'Home 1/2'
    state, _ = step(state, KnobHeld(knob=Knob.BOTTOM))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'not pinned'


def test_stop_view_without_a_destination_names_none() -> None:
    """A pin with no Destination gives the view no destination."""
    saved = seeded(pin=replace(PIN, destination=None))
    state = begun(saved=saved)
    state, _ = step(state, Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.destination is None


def test_first_tick_loads_departures() -> None:
    """The first tick loads from an hour back to the horizon plus 30 min."""
    _, effects = step(fresh(), Tick(now=START))
    start = START - LOOKBACK
    end = START + HORIZON + RELOAD_BEFORE
    assert LoadDepartures(pin=PIN, start=start, end=end) in effects


def test_load_follows_school_switch() -> None:
    """The load asks to skip school buses as the settings say."""
    state = fresh()
    board = replace(state.settings.board, skip_school_buses=False)
    state = replace(state, settings=replace(state.settings, board=board))
    _, effects = step(state, Tick(now=START))
    loads = [e for e in effects if isinstance(e, LoadDepartures)]
    assert [load.skip_school for load in loads] == [False]


def test_load_follows_route_pin_school_switch() -> None:
    """The load skips school on route pins only with both switches on."""
    state = fresh()
    effective = []
    for main, second in ((True, True), (True, False), (False, True)):
        board = replace(
            state.settings.board,
            skip_school_buses=main,
            skip_school_on_route_pins=second,
        )
        settings = replace(state.settings, board=board)
        _, effects = step(replace(state, settings=settings), Tick(now=START))
        loads = [e for e in effects if isinstance(e, LoadDepartures)]
        effective.append(loads[0].skip_school_pins)
    assert effective == [True, False, False]


def test_load_for_other_route_pin_switch_is_stale() -> None:
    """Departures loaded under the other route-pin switch are ignored."""
    state = replace(
        fresh(),
        settings=Settings(
            board=BoardSettings(skip_school_on_route_pins=True),
        ),
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=(departure(departs=START),),
        until=START + HORIZON,
        skip_school_pins=False,
    )
    after, effects = step(state, event)
    assert after == state
    assert not effects


def test_load_for_other_school_switch_is_stale() -> None:
    """Departures loaded under the other school switch are ignored."""
    state = fresh()
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=(departure(departs=START),),
        until=START + HORIZON,
        skip_school=False,
    )
    after, effects = step(state, event)
    assert after == state
    assert not effects


def test_loaded_departures_render_and_choose_feeds() -> None:
    """Loading renders and the next cycle fetches the bus feed."""
    state = loaded(state=fresh())
    assert state.feeds == (BUSES,)
    _, effects = step(state, Tick(now=START + POLL_EVERY))
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
    state = replace(wakeful(state=loaded(state=fresh())), last_fetch=first)
    _, effects = step(state, Tick(now=second))
    assert FetchFeed(feed=BUSES) in effects


def test_step_no_feed_for_ferry_pin() -> None:
    """A ferry pin fetches only alerts and shows the no-feed banner."""
    state, effects = step(
        loaded(state=fresh(), mode=Mode.FERRY), Tick(now=START)
    )
    assert state.feeds == ()
    assert not trip_fetches(effects=effects)
    assert isinstance(state.last_view, StopView)
    assert state.last_view.banner is not None
    assert 'no live feed' in state.last_view.banner


def test_feed_fetched_renders_once() -> None:
    """A cycle's answer renders; an identical view isn't rendered again."""
    state, _ = step(
        loaded(state=fresh()), Tick(now=START + timedelta(seconds=30)),
    )
    assert state.cycle.waiting == {BUSES}
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
    state, effects = step_answered(state=state, event=Tick(now=later))
    assert FetchFeed(feed=BUSES) in effects
    assert isinstance(state.last_view, StopView)
    assert state.last_view.hero is not None
    assert state.last_view.banner == 'No live data since 8:00'


def test_failed_load_retries_on_next_tick() -> None:
    """A failed timetable read clears the loading flag so it reloads."""
    state, _ = step(fresh(), Tick(now=START))
    assert state.board.loading
    state, _ = step(state, DeparturesFailed(pin=PIN))
    assert not state.board.loading
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
        pin=PIN, departures=deps, until=START + HORIZON + RELOAD_BEFORE
    )
    state, _ = step(fresh(), event)
    _, effects = step(state, Tick(now=START + POLL_EVERY))
    fetched = [
        e.feed
        for e in effects
        if isinstance(e, FetchFeed) and e.kind == FeedKind.TRIPS
    ]
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


def trip_fetches(*, effects: tuple[Effect, ...]) -> list[FetchFeed]:
    """
    Pick the feed fetches other than alert fetches.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[FetchFeed]
        The trip and position fetches, in order.
    """
    return [
        fetch
        for fetch in effects_of(effects=effects, kind=FetchFeed)
        if fetch.kind != FeedKind.ALERTS
    ]


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


def map_zoom(*, state: State) -> int:
    """
    Give the zoom of the map area shown.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    int
        The frame's zoom, else the resting zoom.
    """
    return area_frame(map_state=state.map)[1]


def test_bottom_turn_zooms_on_map_and_scrolls_on_stop() -> None:
    """On the Stop view the turn scrolls; on the Map it zooms."""
    state = loaded(state=fresh(), count=20)
    state, _ = turned(state=state, steps=2)
    assert (state.scroll, state.map.step) == (2, 0)
    state, _ = step(state, PRESS)
    state, effects = turned(state=state, steps=1)
    assert ZOOMS[map_zoom(state=state)] == 5
    assert state.scroll == 2
    renders = effects_of(effects=effects, kind=Render)
    assert len(renders) == 1
    assert isinstance(renders[0].view, MapView)
    assert renders[0].view.zoom == 1


def test_zoom_clamps_at_both_ends() -> None:
    """Spinning either way stops at 3 and 45 m/px."""
    state, _ = step(loaded(state=fresh()), PRESS)
    state, _ = turned(state=state, steps=10)
    assert ZOOMS[map_zoom(state=state)] == 3
    state, _ = turned(state=state, steps=1)
    assert ZOOMS[map_zoom(state=state)] == 3
    state, _ = turned(state=state, steps=-10)
    assert ZOOMS[map_zoom(state=state)] == 45
    state, _ = turned(state=state, steps=-1)
    assert ZOOMS[map_zoom(state=state)] == 45


def test_zoom_is_kept_after_returning_to_stop() -> None:
    """Leaving the Map and coming back keeps its zoom and the Stop scroll."""
    state = loaded(state=fresh(), count=20)
    state, _ = turned(state=state, steps=3)
    state, _ = step(state, PRESS)
    state, _ = turned(state=state, steps=-1)
    for _ in range(len(LIVE_VIEWS) - 1):
        state, _ = step(state, PRESS)
    assert isinstance(state.last_view, StopView)
    assert state.last_view.scroll == 3
    state, _ = step(state, PRESS)
    assert isinstance(state.last_view, MapView)
    assert ZOOMS[state.last_view.zoom] == 14


def test_modes_loaded_narrows_the_polled_feeds() -> None:
    """The area's modes replace the every-mode feeds already polled."""
    state, _ = step(fresh(centre=CENTRE), PRESS)
    event = ModesLoaded(
        mpp=8, box=area_box(centre=CENTRE, mpp=8), modes=frozenset({Mode.BUS})
    )
    state, effects = step(state, event)
    assert not effects_of(effects=effects, kind=FetchFeed)
    assert map_feeds(state=state) == (BUS_POSITIONS,)
    assert state.map.area_modes == {8: frozenset({Mode.BUS})}
    assert not state.map.pending_modes


def test_map_polls_positions_with_trip_updates() -> None:
    """On the Map a due tick fetches both trip updates and positions."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    state, effects = step(state, PRESS)
    fetches = every_position_fetch()
    assert effects_of(effects=effects, kind=FetchFeed) == fetches
    _, effects = step(state, Tick(now=START + timedelta(seconds=10)))
    assert not effects_of(effects=effects, kind=FetchFeed)
    _, effects = step(state, Tick(now=START + POLL_EVERY))
    assert effects_of(effects=effects, kind=FetchFeed) == [
        FetchFeed(feed=BUSES),
        *fetches,
    ]


def test_stop_view_never_polls_positions() -> None:
    """The Stop view fetches trip updates only."""
    state = loaded(state=fresh(centre=CENTRE))
    for seconds in (30, 60, 90):
        now = START + timedelta(seconds=seconds)
        state, effects = step(state, Tick(now=now))
        fetched = effects_of(effects=effects, kind=FetchFeed)
        assert [
            each for each in fetched if each.kind != FeedKind.ALERTS
        ] == [FetchFeed(feed=BUSES)]
    assert needed_feeds(state=state) == (BUSES,)


def test_map_polls_positions_for_a_ferry_pin() -> None:
    """A ferry pin has no trip feed, yet the Map polls ferry positions."""
    pin = loaded(state=fresh(centre=CENTRE), mode=Mode.FERRY)
    state, _ = step(pin, PRESS)
    assert state.feeds == ()
    ferry = ModesLoaded(
        mpp=8,
        box=area_box(centre=CENTRE, mpp=8),
        modes=frozenset({Mode.FERRY}),
    )
    state, _ = step(state, ferry)
    fetch = FetchFeed(feed=FERRY_POSITIONS, kind=FeedKind.POSITIONS)
    for seconds in (30, 60, 90):
        now = START + timedelta(seconds=seconds)
        state, effects = step(state, Tick(now=now))
        assert trip_fetches(effects=effects) == [fetch]


def test_empty_area_modes_fall_back_to_every_mode() -> None:
    """With no modes found in the area, every nearby feed is polled."""
    state, _ = step(loaded(state=fresh(centre=CENTRE)), PRESS)
    box = area_box(centre=CENTRE, mpp=8)
    empty = ModesLoaded(mpp=8, box=box, modes=frozenset())
    state, _ = step(state, empty)
    assert needed_feeds(state=state) == (
        BUSES,
        *feeds_near(feeds=position_feeds(modes=ALL_POSITION_MODES), box=box),
    )
    ferry = ModesLoaded(mpp=8, box=box, modes=frozenset({Mode.FERRY}))
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
    trips = frozenset(dep.trip_id for dep in state.board.departures)
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
    """Positions fetched on entering the Map wait for the next boundary."""
    state, effects = step(loaded(state=fresh(), mode=Mode.FERRY), PRESS)
    assert effects_of(effects=effects, kind=FetchFeed) == (
        every_position_fetch()
    )
    _, effects = step(state, Tick(now=START + timedelta(seconds=1)))
    assert not trip_fetches(effects=effects)
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    state, _ = step(state, Tick(now=START + timedelta(seconds=29)))
    state, _ = step(state, PRESS)
    _, effects = step(state, Tick(now=START + timedelta(seconds=29)))
    assert not effects_of(effects=effects, kind=FetchFeed)
    _, effects = step(state, Tick(now=START + POLL_EVERY))
    assert effects_of(effects=effects, kind=FetchFeed) == [
        FetchFeed(feed=BUSES),
        *every_position_fetch(),
    ]


def left_for_stop(*, state: State) -> State:
    """
    Press from the Map round to the Stop view, all at one moment.

    Parameters
    ----------
    state : State
        State on the Map.

    Returns
    -------
    State
        State on the Stop view, which polls no positions.
    """
    for _ in range(len(LIVE_VIEWS) - 1):
        state, _ = step(state, PRESS)
    assert state.view is LiveView.STOP
    return state


def test_returning_to_the_map_fetches_at_once() -> None:
    """Coming back to the Map after 30 s fetches positions straight away."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    state, _ = step(state, PRESS)
    state, _ = step(state, Tick(now=START + timedelta(seconds=29)))
    state = left_for_stop(state=state)
    state, _ = step(state, Tick(now=START + timedelta(seconds=151)))
    _, effects = step(state, PRESS)
    assert effects_of(effects=effects, kind=FetchFeed) == (
        every_position_fetch()
    )


def test_map_draws_only_the_feeds_it_polls() -> None:
    """A stored download from a feed the Map doesn't poll isn't drawn."""
    state, _ = step(loaded(state=fresh(centre=CENTRE)), PRESS)
    buses_only = ModesLoaded(
        mpp=8,
        box=area_box(centre=CENTRE, mpp=8),
        modes=frozenset({Mode.BUS}),
    )
    state, _ = step(state, buses_only)
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
    state = left_for_stop(state=state)
    almost = START + FEED_IDLE - timedelta(seconds=1)
    state, _ = step(state, Tick(now=almost))
    assert BUS_POSITIONS in state.map.positions
    state, _ = step(state, Tick(now=START + FEED_IDLE))
    assert not state.map.positions
    assert not state.map.fetched_at


def test_loaded_shapes_and_positions_are_drawn() -> None:
    """Shapes draw the route line and positions draw markers."""
    state, effects = step(
        loaded(state=fresh(centre=CENTRE), shaped=True), PRESS,
    )
    others = tuple(
        effect
        for effect in effects
        if isinstance(effect, FetchFeed) and effect.feed != BUS_POSITIONS
    )
    state, _ = answer_fetches(state=state, effects=others)
    assert state.cycle.waiting == {BUS_POSITIONS}
    line = make_shape(shape_id='b:0', points=[CENTRE, (-16_900.0, 1_000.0)])
    state, effects = step(state, ShapesLoaded(shapes={'b:0': line}))
    assert not effects_of(effects=effects, kind=Render)
    bus = vehicle(timestamp=int(START.timestamp()), point=CENTRE)
    positions = snapshot(fetched_at=START, vehicles=(bus,))
    event = PositionsFetched(feed=BUS_POSITIONS, snapshot=positions)
    state, effects = step(state, event)
    assert state.map.positions == {BUS_POSITIONS: positions}
    renders = effects_of(effects=effects, kind=Render)
    assert len(renders) == 1
    assert isinstance(state.last_view, MapView)
    assert state.last_view.base.route == (line.points,)
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
            box=area_box(centre=CENTRE, mpp=mpp),
            modes=frozenset(draw(st.sets(st.sampled_from(MODES)))),
        ),
        PositionsFetched(
            feed=feed,
            snapshot=snapshot(fetched_at=now, vehicles=(bus,)),
        ),
        MapDataFailed(request=request),
    ]
    return draw(st.sampled_from(options))


VERSIONS: Final[tuple[str | None, ...]] = (None, 'v1', 'v2')
STOP_IDS: Final[tuple[str, ...]] = (
    '2145615',
    '215020',
    'first',
    'second',
    'third',
    'elsewhere',
    TOWN_HALL.stop_id,
)


@st.composite
def found_places(draw: st.DrawFn) -> PlacesMatched:
    """
    A random answer to a re-match.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    PlacesMatched
        Some stop IDs each found under itself, another ID, or not at all.
    """
    found = draw(
        st.dictionaries(
            keys=st.sampled_from(STOP_IDS),
            values=st.one_of(
                st.none(),
                st.sampled_from(STOP_IDS).map(
                    lambda stop_id: place(
                        stop_id, name=stop_id, point=(1.0, 2.0)
                    )
                ),
            ),
        )
    )
    return PlacesMatched(found=found)


@st.composite
def place_events(draw: st.DrawFn) -> Event:
    """
    A random hold, timetable version or re-match.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Event
        A knob hold, a timetable seen or places matched.
    """
    options: list[Event] = [
        KnobHeld(knob=draw(st.sampled_from(list(Knob)))),
        TimetableSeen(version=draw(st.sampled_from(VERSIONS))),
        draw(found_places()),
    ]
    return draw(st.sampled_from(options))


def reach_through(*, origin: str, side: int) -> Reach:
    """
    Build a reach with the same number of stations each side of a From.

    Parameters
    ----------
    origin : str
        From stop ID.
    side : int
        Stations each side.

    Returns
    -------
    Reach
        The reach.
    """
    ups = tuple(f'up-{i}' for i in range(side))
    downs = tuple(f'down-{i}' for i in range(side))
    return build_reach(
        data=ReachInput(
            origin=place(origin, name=origin),
            tails=(
                RunTail(before=(), after=ups, trips=2),
                RunTail(before=(), after=downs, trips=1),
            ),
            places={key: place(key, name=key) for key in (*ups, *downs)},
            station_of={},
        )
    )


@st.composite
def strip_events(draw: st.DrawFn) -> Event:
    """
    A random reach built or failed, for any place's strip or another.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Event
        A StripLoaded with no reach, a short or a long one, or a
        StripFailed, from any timetable version.
    """
    pin = draw(st.sampled_from(POOL))
    key = draw(
        st.sampled_from(
            [strip_key(pin=pin), replace(strip_key(pin=pin), station='x')]
        )
    )
    side = draw(st.sampled_from([0, 2, 20]))
    reach = draw(
        st.sampled_from(
            [None, reach_through(origin=pin.origin.stop_id, side=side)]
        )
    )
    timetable = draw(st.sampled_from(VERSIONS))
    options: list[Event] = [
        StripLoaded(key=key, reach=reach, timetable=timetable),
        StripFailed(key=key, timetable=timetable),
    ]
    return draw(st.sampled_from(options))


class Outcome(StrEnum):
    """How a stand-in reply turns out."""

    EXPECTED = 'expected'
    EMPTY = 'empty'
    FAILED = 'failed'


@dataclass(frozen=True)
class Answer:
    """
    A stand-in for the reply to whatever the chooser waits for.

    Attributes
    ----------
    outcome : Outcome
        The expected answer, an empty one or a failure.
    current : bool
        Whether it comes from the timetable in use.
    side : int
        Stations each side of the From on a strip answered.
    """

    outcome: Outcome
    current: bool
    side: int


def column_for(*, path: ChooserPath) -> Column:
    """
    Answer a column request from a small catalogue.

    Parameters
    ----------
    path : ChooserPath
        The path asked for.

    Returns
    -------
    Column
        Bus or Metro routes and their towards rows, or T1's stations.
    """
    known = {
        (): KINDS,
        BUS_606[:1]: GROUPS,
        ((Level.KIND, 'train'),): TRAIN_LINES,
        ((Level.KIND, 'metro'),): METRO_LINES,
    }
    if path in known:
        return known[path]
    match path[-1][0]:
        case Level.GROUP:
            return ROUTES
        case Level.LINE if path[0][1] == 'train':
            return T1_STATIONS
        case Level.STATION:
            return T1_TO
    return TOWARDS


def picked_reply(
    *, state: State, answer: Answer, timetable: str | None
) -> Event:
    """
    Answer the TO stage's strip or departures load.

    Parameters
    ----------
    state : State
        State with the chooser in the TO stage.
    answer : Answer
        The stand-in.
    timetable : str | None
        The version the reply comes from.

    Returns
    -------
    Event
        The strip, built or not, while it is pending; else departures.
    """
    chooser = state.chooser
    assert chooser is not None and chooser.key is not None
    pin = chosen_pin(chooser=chooser, destination=None)
    pending = chooser.key in state.strip.pending
    if pending and answer.outcome is not Outcome.EMPTY:
        reach = (
            reach_through(origin=pin.origin.stop_id, side=answer.side)
            if answer.outcome is Outcome.EXPECTED
            else None
        )
        return StripLoaded(key=chooser.key, reach=reach, timetable=timetable)
    if answer.outcome is Outcome.FAILED:
        return ChooserDeparturesFailed(pin=pin, timetable=timetable)
    return ChooserDeparturesLoaded(
        pin=pin,
        departures=chooser_departures(),
        until=load_window(now=state.now)[1],
        timetable=timetable,
    )


def listed_reply(
    *,
    chooser: ChooserState,
    answer: Answer,
    now: datetime,
    timetable: str | None,
) -> Event:
    """
    Answer the departures load a station press asked for.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with a From chosen in its lists.
    answer : Answer
        The stand-in.
    now : datetime
        Time of the reply.
    timetable : str | None
        The version the reply comes from.

    Returns
    -------
    Event
        The departures, or their failure.
    """
    pin = chosen_pin(chooser=chooser, destination=None)
    if answer.outcome is Outcome.FAILED:
        return ChooserDeparturesFailed(pin=pin, timetable=timetable)
    return ChooserDeparturesLoaded(
        pin=pin,
        departures=chooser_departures(),
        until=load_window(now=now)[1],
        timetable=timetable,
    )


def answered(*, state: State, answer: Answer) -> Event:
    """
    Turn a stand-in into the reply the chooser waits for.

    Parameters
    ----------
    state : State
        State.
    answer : Answer
        The stand-in.

    Returns
    -------
    Event
        A column, line, strip or departures reply; a Tick when nothing is
        waited for.
    """
    chooser = state.chooser
    timetable = state.saving.timetable if answer.current else 'old'
    if chooser is None:
        return Tick(now=state.now)
    if chooser.stage is Stage.TO:
        return picked_reply(state=state, answer=answer, timetable=timetable)
    if chooser.origin is not None and chooser.waiting is None:
        return listed_reply(
            chooser=chooser, answer=answer, now=state.now,
            timetable=timetable,
        )
    if chooser.stage is Stage.FROM and chooser.towards is not None:
        towards = int(chooser.towards.key)
        if answer.outcome is Outcome.FAILED:
            return LineFailed(towards=towards, timetable=timetable)
        line = LINE if answer.outcome is Outcome.EXPECTED else None
        return LineLoaded(towards=towards, line=line, timetable=timetable)
    path = chooser.waiting
    if path is None:
        return Tick(now=state.now)
    if answer.outcome is Outcome.FAILED:
        return ColumnFailed(path=path, timetable=timetable)
    column = (
        column_for(path=path)
        if answer.outcome is Outcome.EXPECTED
        else Column(level=Level.ROUTE, items=())
    )
    return ColumnLoaded(path=path, column=column, timetable=timetable)


@st.composite
def chooser_events(draw: st.DrawFn) -> Event:
    """
    A random chooser reply, most likely for something not waited for.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Event
        A column, line or departures reply or failure, from any
        timetable version.
    """
    timetable = draw(st.sampled_from(VERSIONS))
    path = draw(st.sampled_from([(), BUS_606[:1], BUS_606[:2], BUS_606]))
    towards = draw(st.sampled_from([11, 12, 21]))
    pin = draw(st.sampled_from([CHOSEN, PENRITH, *POOL]))
    options: list[Event] = [
        ColumnLoaded(
            path=path, column=column_for(path=path), timetable=timetable,
        ),
        ColumnFailed(path=path, timetable=timetable),
        LineLoaded(towards=towards, line=LINE, timetable=timetable),
        LineFailed(towards=towards, timetable=timetable),
        ChooserDeparturesLoaded(
            pin=pin,
            departures=chooser_departures(),
            until=UNTIL,
            timetable=timetable,
        ),
        ChooserDeparturesFailed(pin=pin, timetable=timetable),
    ]
    return draw(st.sampled_from(options))


@st.composite
def train_walk(draw: st.DrawFn) -> list[Event | Answer]:
    """
    The start of a walk to a T1 station and its TO column and back.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    list[Event | Answer]
        Knob events, and stand-ins for the replies, cut short at random.
    """
    ok = Answer(outcome=Outcome.EXPECTED, current=True, side=0)
    up = KnobTurned(knob=Knob.BOTTOM, steps=-9)
    walk: list[Event | Answer] = [
        OPEN,
        *(ok, ok, up, ok, DOWN, ok, NEXT, ok, NEXT),
        KnobTurned(knob=Knob.BOTTOM, steps=draw(st.integers(0, 3))),
        *(NEXT, ok, ok, DOWN, OPEN, OPEN, NEXT, ok),
    ]
    return walk[:draw(st.integers(1, len(walk)))]


@st.composite
def event_lists(
    draw: st.DrawFn, start: datetime
) -> list[Event | Answer]:
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
    list[Event | Answer]
        Events, and stand-ins for the replies the chooser waits for.
    """
    now, events = start, list[Event | Answer]()
    kinds = [
        'tick',
        'turn',
        'press',
        'fail',
        'ok',
        'load',
        'unreadable',
        'map',
        'place',
        'strip',
        'quit',
        'walk',
        'train',
        'answer',
        'answer',
        'chooser',
        'held',
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
        elif kind == 'held':
            events.append(KnobHeldTurned(
                knob=draw(st.sampled_from(list(Knob))),
                steps=draw(st.integers(-6, 6)),
            ))
        elif kind == 'fail':
            events.append(FeedFailed(feed=BUSES, at=now, reason='timeout'))
        elif kind == 'unreadable':
            events.append(
                DeparturesFailed(
                    pin=draw(st.sampled_from(POOL)),
                    timetable=draw(st.sampled_from(VERSIONS)),
                )
            )
        elif kind == 'place':
            events.append(draw(place_events()))
        elif kind == 'strip':
            events.append(draw(strip_events()))
        elif kind == 'quit':
            events.append(QuitRequested())
        elif kind == 'walk':
            ok = Answer(
                outcome=Outcome.EXPECTED,
                current=True,
                side=draw(st.sampled_from([0, 2, 20])),
            )
            walk: list[Event | Answer] = [
                OPEN,
                *(ok, ok, NEXT),
                *(ok, NEXT),
                *(ok, NEXT),
                *(ok, NEXT),
                *(ok, DOWN, NEXT),
                *(ok, ok, DOWN, DOWN),
            ]
            events.extend(walk[:draw(st.integers(1, len(walk)))])
        elif kind == 'train':
            events.extend(draw(train_walk()))
        elif kind == 'answer':
            events.append(
                Answer(
                    outcome=draw(st.sampled_from(Outcome)),
                    current=draw(st.booleans()),
                    side=draw(st.sampled_from([0, 2, 20])),
                )
            )
        elif kind == 'chooser':
            events.append(draw(chooser_events()))
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
            events.append(
                DeparturesLoaded(
                    pin=draw(st.sampled_from(POOL)),
                    departures=deps,
                    until=until,
                    timetable=draw(st.sampled_from(VERSIONS)),
                )
            )
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
    assert abs(after.map.step) < len(ZOOMS)
    assert after.view in LIVE_VIEWS
    if after.view is LiveView.STOP and isinstance(after.last_view, StopView):
        assert after.scroll == after.last_view.scroll
    assert set(after.strip.reaches) <= keys_in_use(state=after)
    check_map_requests(before=before, after=after, effects=effects)
    check_chooser(before=before, after=after, event=event, effects=effects)
    check_selection(before=before, after=after, event=event)
    for effect in effects:
        if isinstance(effect, Render):
            check_render(state=after, view=effect.view)
            assert effect.view != before.last_view or (
                effect.full and isinstance(event, Tick)
            )
        if isinstance(effect, LoadStrip):
            check_strip_load(
                before=before, after=after, event=event, effect=effect
            )
        if isinstance(effect, LoadDepartures):
            assert not before.board.loading or not same_place(
                first=shown(state=before), second=effect.pin
            )
        if isinstance(effect, FetchFeed):
            check_fetch(before=before, after=after, effect=effect)


def check_selection(*, before: State, after: State, event: Event) -> None:
    """
    Assert a picked train is in the panel of the Strip on screen, and a
    turn while held changes nothing saved.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    """
    selected = after.strip.selected
    if selected is not None:
        assert after.view is LiveView.STRIP and after.chooser is None
        trains = panel_trains(stop_view=live_stop_view(state=after))
        assert selected in {trip_day(departure=train) for train in trains}
    if isinstance(event, KnobHeldTurned):
        counted = replace(saved_of(state=after), quota=before.quota)
        assert counted == saved_of(state=before)
        assert after.chooser == before.chooser
        if event.knob is Knob.TOP or before.view is not LiveView.STRIP:
            assert after.strip.selected == before.strip.selected


def check_chooser(
    *, before: State, after: State, event: Event, effects: tuple[Effect, ...]
) -> None:
    """
    Assert the chooser's cursors fit, nothing saved changes while it is
    open but the committing press, and its loads are not repeated.

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
    chooser = after.chooser
    if chooser is not None:
        check_cursors(state=after, chooser=chooser)
        check_list_from(chooser=chooser)
    commit = event == PRESS and after.chooser is None
    if before.chooser is not None and not commit:
        if not isinstance(event, PlacesMatched):
            counted = replace(saved_of(state=after), quota=before.quota)
            assert counted == saved_of(state=before)
    swapped = (
        isinstance(event, TimetableSeen)
        and event.version != before.saving.timetable
    )
    waiting = None if before.chooser is None else before.chooser.waiting
    for load in effects_of(effects=effects, kind=LoadColumn):
        assert load.timetable == after.saving.timetable
        assert swapped or waiting is None or load.path != waiting
    for asked in effects_of(effects=effects, kind=LoadChooserDepartures):
        assert chooser is not None and chooser.origin is not None
        assert chooser.stage is Stage.TO or chooser.stage is Stage.LISTS
        assert asked.pin.origin == chooser.origin


def check_list_from(*, chooser: ChooserState) -> None:
    """
    Assert a From chosen in the lists is the pressed station's.

    Parameters
    ----------
    chooser : ChooserState
        An open chooser.
    """
    if chooser.stage is not Stage.LISTS or chooser.origin is None:
        return
    level = chooser.columns[chooser.focus].level
    if level is Level.TO:
        return
    assert level is Level.STATION
    pressed = chosen(chooser=chooser, index=chooser.focus)
    assert pressed.key == chooser.origin.stop_id
    assert chooser.waiting is not None or chooser.failed


def check_cursors(*, state: State, chooser: ChooserState) -> None:
    """
    Assert every chooser cursor is within what it moves over.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Its open chooser.
    """
    assert len(chooser.cursors) == len(chooser.columns)
    for cursor, column in zip(chooser.cursors, chooser.columns, strict=True):
        assert 0 <= cursor < max(1, len(column.items))
    assert 0 <= chooser.line_cursor < max(1, len(chooser.line))
    if chooser.columns and chooser.columns[chooser.focus].level in (
        Level.STATION, Level.TO,
    ):
        choice = chosen(chooser=chooser, index=chooser.focus)
        assert focusable(choice=choice)
    if chooser.stage is not Stage.TO or chooser.key is None:
        return
    reach = state.strip.reaches.get(chooser.key)
    origin = None if reach is None else origin_of(reach=reach)
    if reach is not None and origin is not None:
        row = origin + chooser.to_cursor
        assert 0 <= row < len(reach.rows)
        assert reach.rows[row].kind is not RowKind.APPROACH


def check_strip_load(
    *, before: State, after: State, event: Event, effect: LoadStrip
) -> None:
    """
    Assert a reach is asked for on a strip view, from the timetable in
    use, and never while pending or built, unless the timetable just
    changed and forgot them.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    effect : LoadStrip
        The request.
    """
    picked = picking_to(state=after)
    if picked is None:
        assert after.chooser is None and after.view in STRIP_VIEWS
    else:
        assert effect.key == picked.key
    assert effect.timetable == after.saving.timetable
    swapped = (
        isinstance(event, TimetableSeen)
        and event.version != before.saving.timetable
    )
    if not swapped:
        assert effect.key not in before.strip.pending
        assert effect.key not in before.strip.reaches


def check_map_requests(
    *, before: State, after: State, effects: tuple[Effect, ...]
) -> None:
    """
    Assert map data is asked for only on the Map, and modes once per zoom
    while the map shows the same place in the same spot.

    Asleep, positions are never fetched, and the sleep screen's map asks
    for its shapes and patterns whatever the view.

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
    if after.panel.asleep:
        assert not any(isinstance(request, FetchFeed) for request in requests)
        requests = [
            request
            for request in requests
            if not isinstance(request, (LoadShapes, LoadPatterns))
        ]
    if not shows_vehicles(state=after):
        assert not requests
    if after.view is LiveView.STRIP or after.chooser is not None:
        assert all(
            isinstance(request, (LoadPatterns, FetchFeed))
            for request in requests
        )
    spot = area_frame(map_state=before.map)[0]
    moved = (
        before.map.centre != after.map.centre
        or spot != area_frame(map_state=after.map)[0]
        or not same_place(first=shown(state=before), second=shown(state=after))
    )
    if moved:
        return
    known = before.map.area_modes.keys() | before.map.pending_modes
    for load in effects_of(effects=effects, kind=LoadModes):
        assert load.mpp not in known


STAGE_TYPES: Final[dict[Stage, type]] = {
    Stage.LISTS: ChooserView,
    Stage.FROM: StripView,
    Stage.TO: StripView,
}
VIEW_TYPES: Final[dict[LiveView, type]] = {
    LiveView.STOP: StopView,
    LiveView.MAP: MapView,
    LiveView.STRIP: StripView,
    LiveView.STRIP_MAP: StripMapView,
}
CONNECTION_TYPES: Final[dict[LiveView, type]] = {
    LiveView.STOP: ConnectionView,
    LiveView.MAP: MapView,
    LiveView.STRIP: ConnectionStripView,
    LiveView.STRIP_MAP: ConnectionStripMapView,
}


def check_render(
    *,
    state: State,
    view: ScreenView,
) -> None:
    """
    Assert a rendered view is the sleep screen while asleep, else the
    Building screen with no timetable, else the chooser's stage or the Live view shown, and fits the
    screen, and the Strip's stored scroll is within its reach.

    Parameters
    ----------
    state : State
        State after the event.
    view : ScreenView
        The view drawn.
    """
    chooser = state.chooser
    if state.panel.asleep:
        assert isinstance(view, SleepView)
    elif state.rebuild.missing:
        assert view == building_view(state=state)
    elif chooser is not None:
        assert type(view) is STAGE_TYPES[chooser.stage]
    else:
        types = (
            VIEW_TYPES
            if not is_connection(pin=shown(state=state))
            else CONNECTION_TYPES
        )
        assert type(view) is types[state.view]
    match view:
        case StopView():
            assert len(view.rows) <= VISIBLE_ROWS
        case MapView():
            assert len(view.sidebar) <= SIDEBAR_ROWS
        case StripView():
            assert len(view.rows) <= STRIP_ROWS - bool(view.banner)
            destination = shown(state=state).destination
            low, high = scroll_range(
                reach=shown_reach(state=state),
                destination=(
                    None if destination is None else destination.stop_id
                ),
                banner=view.banner is not None,
            )
            assert low <= state.strip.scroll <= high
        case StripMapView():
            assert len(view.pane.rows) <= PANE_ROWS - bool(view.map.banner)
            assert view.map.base.width < MAP_WIDTH


def check_fetch(
    *, before: State, after: State, effect: FetchFeed
) -> None:
    """
    Assert a fetch is needed and either starts a cycle or joins it once.

    A cycle starts on a boundary, or off it when `cycle_reset` says so.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    effect : FetchFeed
        The fetch.
    """
    needed = (
        alert_feeds(state=after)
        if effect.kind is FeedKind.ALERTS
        else needed_feeds(state=after)
    )
    assert effect.feed in needed
    assert effect.feed in after.cycle.asked
    assert effect.feed in after.cycle.waiting
    started = after.cycle.started != before.cycle.started
    assert not started or cycle_reset(before=before, after=after) or any(
        cycle_due(
            cycle=before.cycle, now=after.now, every=poll_every(state=state),
        )
        for state in (before, after)
    )
    assert (
        started
        or cycle_reset(before=before, after=after)
        or effect.feed not in before.cycle.asked
    )


def cycle_reset(*, before: State, after: State) -> bool:
    """
    Say whether an event let a cycle start off its boundary.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.

    Returns
    -------
    bool
        True when a timetable arrived, the panel woke, a key was set,
        the clock was set or the place shown changed.
    """
    return (
        (before.rebuild.missing and not after.rebuild.missing)
        or (before.panel.asleep and not after.panel.asleep)
        or (before.panel.key_set is False and after.panel.key_set is True)
        or (
            before.clock_wait_until is not None
            and after.clock_wait_until is None
        )
        or not same_place(first=shown(state=before), second=shown(state=after))
    )


def check_places(*, before: State, after: State, event: Event) -> None:
    """
    Assert a place is shown, current is in range and pins are unique.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    """
    places = after.places
    shown(state=after)
    assert 0 <= places.current < max(1, len(places.pins))
    keys = [pin_key(pin=pin) for pin in places.pins]
    assert len(set(keys)) == len(keys)
    change = len(places.pins) - len(before.places.pins)
    if change:
        assert event == KnobHeld(knob=Knob.BOTTOM)
        assert abs(change) == 1


def check_saves(
    *,
    before: State,
    after: State,
    effects: tuple[Effect, ...],
    last: Saved | None,
) -> Saved | None:
    """
    Assert every save carries the state (its quota may lag a tick), differs
    from the last one and, unless quitting or nothing was stored, waits
    SAVE_AFTER, or QUOTA_SAVE_EVERY when only the quota changed.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects returned.
    last : Saved | None
        What was last saved, or None before the first save.

    Returns
    -------
    Saved | None
        What is now last saved.
    """
    changed_at = before.saving.changed_at
    for save in effects_of(effects=effects, kind=SaveState):
        counted = replace(save.saved, quota=after.quota)
        assert counted == saved_of(state=after)
        assert save.saved != last
        last = save.saved
        written = before.saving.saved
        if Stop() in effects or written is None:
            continue
        if quota_only(before=written, after=save.saved):
            quota_at = before.saving.quota_at
            assert quota_at is not None
            assert after.now - quota_at >= QUOTA_SAVE_EVERY
            continue
        if changed_at is not None:
            waited = after.now.timestamp() - changed_at.timestamp()
            assert waited >= SAVE_AFTER.total_seconds()
    return last


@st.composite
def start_saved(draw: st.DrawFn) -> Saved:
    """
    A random place to start from.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Saved
        One pin, three pins with or without an unpinned place, the
        default, or a damaged file with no place and current out of range.
    """
    centre = draw(st.sampled_from([None, CENTRE]))
    pin = replace(PIN, origin=replace(PIN.origin, point=centre))
    options = [
        seeded(pin=pin),
        seeded(pin=WORK),
        DEFAULT_SAVED,
        replace(DEFAULT_SAVED, unpinned=None, current=5),
        Saved(
            pins=PINS,
            current=draw(st.integers(-1, 4)),
            unpinned=draw(st.sampled_from([None, ELSEWHERE])),
            view=draw(st.sampled_from(LIVE_VIEWS)),
        ),
    ]
    return draw(st.sampled_from(options))


@pytest.mark.parametrize('view', [LiveView.STRIP, LiveView.STOP])
def test_step_invariants_asleep_on_a_view_without_a_map(
    view: LiveView,
) -> None:
    """Falling asleep off the Map loads the sleep map, not positions."""
    night = datetime(2026, 4, 12, 1, 30, tzinfo=SYDNEY)
    state = initial_state(
        saved=replace(DEFAULT_SAVED, view=view), now=night, stored=True,
    )
    trip = replace(departure(departs=night), shape_id='b:1')
    state, _ = step(
        state,
        DeparturesLoaded(
            pin=shown(state=state),
            departures=(trip,),
            until=night + HORIZON,
        ),
    )
    tick = Tick(now=night)
    after, effects = step(state, tick)
    assert after.panel.asleep
    assert effects_of(effects=effects, kind=LoadShapes)
    assert not effects_of(effects=effects, kind=FetchFeed)
    check_effects(before=state, after=after, event=tick, effects=effects)


@given(
    st.sampled_from([START, FALL_BACK_EVE]).flatmap(
        lambda start: st.tuples(st.just(start), event_lists(start))
    ),
    start_saved(),
    st.booleans(),
)
def test_step_invariants(
    case: tuple[datetime, list[Event | Answer]], saved: Saved, stored: bool
) -> None:
    """No event sequence breaks the reducer's invariants."""
    start, events = case
    state = initial_state(saved=saved, now=start, stored=stored)
    last = state.saving.saved
    for item in events:
        event = (
            answered(state=state, answer=item)
            if isinstance(item, Answer)
            else item
        )
        before = state
        state, effects = step(before, event)
        check_effects(
            before=before, after=state, event=event, effects=effects
        )
        check_places(before=before, after=state, event=event)
        last = check_saves(
            before=before, after=state, effects=effects, last=last
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
        return DeparturesLoaded(
            pin=effect.pin,
            departures=found,
            until=effect.end,
            timetable=effect.timetable,
        )


def run_tick(
    *, state: State, timetable: WindowedTimetable, now: datetime
) -> State:
    """
    Tick, then answer any load request at once, and the feeds with
    failures.

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
    state, effects = step_answered(state=state, event=Tick(now=now))
    for effect in effects:
        if isinstance(effect, LoadDepartures):
            state, _ = step_answered(
                state=state, event=timetable.load(effect=effect),
            )
    return state


def test_empty_never_shows_while_a_departure_is_within_horizon() -> None:
    """Across many load cycles the list always reaches a full horizon."""
    only = departure(departs=START + timedelta(hours=5, minutes=30))
    timetable = WindowedTimetable(departures=(only,))
    state = fresh()
    for minute in range(0, 6 * 60 + 1):
        now = START + timedelta(minutes=minute)
        state = run_tick(state=state, timetable=timetable, now=now)
        assert isinstance(state.last_view, StopView)
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
    state = run_tick(
        state=fresh(), timetable=timetable, now=START - timedelta(seconds=30),
    )
    state, _ = step(state, Tick(now=START))
    snapshot = RealtimeSnapshot(fetched_at=START, trips={late.trip_id: trip})
    state, _ = step(state, FeedFetched(feed=BUSES, snapshot=snapshot))
    state, _ = step(state, FeedFailed(feed=ALERTS, at=START, reason='test'))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.hero is not None
    assert state.last_view.hero.leaves_at == '8:04'


def test_view_says_loading_before_the_first_load() -> None:
    """Until departures have loaded, the empty text says so."""
    state, _ = step(fresh(), Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.empty == 'Loading timetable'
    loaded_state, _ = step(
        state,
        DeparturesLoaded(pin=PIN, departures=(), until=START + HORIZON),
    )
    assert isinstance(loaded_state.last_view, StopView)
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
    assert ZOOMS[map_zoom(state=state)] == CITY
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
    state = left_for_stop(state=state)
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
    """
    A pin trip whose vehicle went stale is asked for at once.

    The state jumps from the Map to the Stop view, since pressing round
    passes the Strip, which loads every strip trip's pattern.
    """
    state = city_map()
    [pin] = state.board.departures
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
    state = replace(state, view=LiveView.STOP)
    state, _ = step(state, Tick(now=START + timedelta(minutes=5)))
    _, effects = step(state, PRESS)
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert pin.trip_id in load.trip_ids


def test_stop_view_tag_counts_the_pins() -> None:
    """A single pin's tag reads pin 1/1."""
    state, _ = step(fresh(), Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'pin 1/1'


def test_no_destination_map_caps_patterns() -> None:
    """A place with 500 departures asks for no more than the sidebar shows."""
    pin = replace(
        PIN,
        origin=replace(PIN.origin, point=CENTRE),
        destination=None,
    )
    state = initial_state(saved=seeded(pin=pin), now=START, stored=True)
    state = loaded(state=state, count=500)
    _, effects = step(state, PRESS)
    loads = effects_of(effects=effects, kind=LoadPatterns)
    assert len(loads) == 1
    assert 0 < len(loads[0].trip_ids) <= SIDEBAR_ROWS


def no_destination_map(*, count: int) -> State:
    """
    Show the Map for a place with no destination and shaped departures.

    Parameters
    ----------
    count : int
        Departures, each with its own shape.

    Returns
    -------
    State
        The state before the Map's first request.
    """
    pin = replace(
        PIN,
        origin=replace(PIN.origin, point=CENTRE),
        destination=None,
    )
    state = initial_state(saved=seeded(pin=pin), now=START, stored=True)
    return loaded(state=state, count=count, shaped=True)


def test_no_destination_map_asks_for_and_keeps_only_sidebar_shapes() -> None:
    """500 departures ask for the sidebar's shapes; other shapes go."""
    state = no_destination_map(count=500)
    state, effects = step(state, PRESS)
    [load] = effects_of(effects=effects, kind=LoadShapes)
    assert load.shape_ids == {f'b:{i}' for i in range(SIDEBAR_ROWS)}
    line = make_shape(shape_id='b:400', points=[CENTRE, (0.0, 0.0)])
    state, _ = step(state, ShapesLoaded(shapes={'b:400': line}))
    assert not state.map.shapes
    assert state.map.requested_shapes == load.shape_ids


def test_no_destination_map_ignores_the_stop_scroll() -> None:
    """The sidebar and the drawn departures both start at the board's top."""
    state = no_destination_map(count=30)
    state, _ = turned(state=state, steps=4)
    assert state.scroll == 4
    state, effects = step(state, PRESS)
    assert isinstance(state.last_view, MapView)
    [load] = effects_of(effects=effects, kind=LoadShapes)
    assert load.shape_ids == {f'b:{i}' for i in range(SIDEBAR_ROWS)}
    assert [row.leaves_at for row in state.last_view.sidebar] == [
        f'8:{3 * (i + 1):02d}' for i in range(SIDEBAR_ROWS)
    ]


HOLD: Final[KnobHeld] = KnobHeld(knob=Knob.BOTTOM)


def pinned(
    *,
    current: int = 0,
    unpinned: Pin | None = None,
    view: LiveView = LiveView.STOP,
) -> State:
    """
    A state with three pins, drawn once.

    Parameters
    ----------
    current : int
        Index of the current pin.
    unpinned : Pin | None
        The unpinned place, if shown.
    view : LiveView
        The Live view shown.

    Returns
    -------
    State
        The state after its first tick.
    """
    saved = Saved(
        pins=PINS,
        current=current,
        unpinned=unpinned,
        view=view,
    )
    state = begun(saved=saved)
    state, _ = step(state, Tick(now=START))
    return state


def top(*, steps: int) -> KnobTurned:
    """
    Turn the top knob.

    Parameters
    ----------
    steps : int
        Detents.

    Returns
    -------
    KnobTurned
        The turn.
    """
    return KnobTurned(knob=Knob.TOP, steps=steps)


def tag_text(*, state: State) -> str:
    """
    Read the pin tag of the view last drawn.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str
        The tag's text.
    """
    assert isinstance(state.last_view, (StopView, MapView))
    return state.last_view.tag.text


def test_top_turn_flips_pins_and_wraps() -> None:
    """The top knob steps through the pins and wraps at both ends."""
    state, _ = step(pinned(), top(steps=1))
    assert (shown(state=state), tag_text(state=state)) == (PINS[1], 'pin 2/3')
    state, _ = step(state, top(steps=2))
    assert (shown(state=state), tag_text(state=state)) == (PINS[0], 'pin 1/3')
    state, _ = step(state, top(steps=-1))
    assert (shown(state=state), tag_text(state=state)) == (PINS[2], 'pin 3/3')


def test_top_turn_drops_unpinned_and_keeps_current() -> None:
    """The first turn away from the unpinned place shows the current pin."""
    state, _ = step(pinned(current=1, unpinned=ELSEWHERE), top(steps=5))
    assert shown(state=state) == PINS[1]
    assert state.places.unpinned is None
    assert state.places.current == 1


def test_top_turn_without_pins_does_nothing() -> None:
    """With only the unpinned place there is nothing to turn to."""
    state, _ = step(
        initial_state(saved=DEFAULT_SAVED, now=START, stored=True),
        Tick(now=START),
    )
    assert step_untouched(state=state, event=top(steps=1)) == (state, ())


def test_hold_pins_unpinned_at_the_end() -> None:
    """Holding pins the unpinned place last and selects it, in place."""
    state, effects = step(pinned(unpinned=ELSEWHERE), HOLD)
    assert state.places.pins == (*PINS, ELSEWHERE)
    assert (state.places.current, state.places.unpinned) == (3, None)
    assert tag_text(state=state) == 'pin 4/4'
    assert not effects_of(effects=effects, kind=LoadDepartures)


def test_hold_unpins_current_into_unpinned() -> None:
    """Holding unpins the current pin and keeps showing its departures."""
    state = loaded(state=pinned(current=1))
    state, effects = step(state, HOLD)
    assert state.places.pins == (PINS[0], PINS[2])
    assert state.places.unpinned == PINS[1]
    assert state.places.current == 1
    assert tag_text(state=state) == 'not pinned'
    assert state.board.departures
    assert not effects_of(effects=effects, kind=LoadDepartures)


def test_hold_with_duplicate_selects_existing_pin() -> None:
    """Pinning a place already pinned selects that pin, in place."""
    renamed = replace(PINS[2].origin, name='Renamed')
    duplicate = replace(PINS[2], origin=renamed)
    before = loaded(state=pinned(unpinned=duplicate))
    state, effects = step(before, HOLD)
    assert state.places.pins == PINS
    assert (state.places.current, state.places.unpinned) == (2, None)
    assert state.board == before.board
    assert not effects_of(effects=effects, kind=LoadDepartures)


@pytest.mark.parametrize('view', [LiveView.STRIP, LiveView.STRIP_MAP])
def test_flip_and_hold_on_the_strip_views(view: LiveView) -> None:
    """The top turn and the bottom hold work on both strip views."""
    state, effects = step(pinned(view=view), top(steps=1))
    assert (state.view, shown(state=state)) == (view, PINS[1])
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == PINS[1]
    state, _ = step(state, HOLD)
    assert state.places.pins == (PINS[0], PINS[2])
    assert (state.view, state.places.unpinned) == (view, PINS[1])


@pytest.mark.parametrize('view', [LiveView.STRIP, LiveView.STRIP_MAP])
def test_top_turn_while_held_does_nothing(view: LiveView) -> None:
    """A top turn while the top knob is held is left unassigned."""
    state = pinned(view=view)
    held = KnobHeldTurned(knob=Knob.TOP, steps=1)
    assert step_untouched(state=state, event=held) == (state, ())


@pytest.mark.parametrize('view', list(LiveView))
def test_top_hold_does_nothing(view: LiveView) -> None:
    """Holding the top knob alone does nothing, on every Live view."""
    state = pinned(unpinned=ELSEWHERE, view=view)
    assert step_untouched(
        state=state, event=KnobHeld(knob=Knob.TOP),
    ) == (state, ())


def test_bottom_press_cycles_four_views() -> None:
    """Presses go Stop, Map, Strip, Strip + map and back to Stop."""
    state, _ = step(fresh(centre=CENTRE), Tick(now=START))
    views: list[LiveView] = []
    drawn: list[type[ScreenView]] = []
    for _ in LIVE_VIEWS:
        state, effects = step(state, PRESS)
        views.append(state.view)
        drawn.extend(
            type(effect.view) for effect in effects_of(
                effects=effects, kind=Render
            )
        )
    assert views == [
        LiveView.MAP,
        LiveView.STRIP,
        LiveView.STRIP_MAP,
        LiveView.STOP,
    ]
    assert drawn == [MapView, StripView, StripMapView, StopView]


def test_bottom_turn_scrolls_stop_and_zooms_map_and_strip_map() -> None:
    """The Stop view scrolls; both map views zoom; the Strip moves neither."""
    state = loaded(state=fresh(centre=CENTRE), count=20)
    state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=2))
    assert (state.scroll, state.map.step) == (2, 0)
    zooms = []
    for _ in range(3):
        state, _ = step(state, PRESS)
        state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=-1))
        zooms.append(map_zoom(state=state))
    assert zooms == [DEFAULT_ZOOM + 1, DEFAULT_ZOOM + 1, DEFAULT_ZOOM + 2]
    assert state.scroll == 2


def test_flip_reloads_departures_and_recentres_map() -> None:
    """A new pin empties the board, loads its departures and moves the map."""
    state = loaded(state=pinned(view=LiveView.MAP))
    state, effects = step(state, top(steps=1))
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == PINS[1]
    assert state.board.departures == ()
    assert state.board.loading
    assert state.map.centre == PINS[1].origin.point


def test_departures_for_another_place_are_dropped() -> None:
    """A load that lands after a flip doesn't fill the new place's board."""
    state, _ = step(pinned(), top(steps=1))
    late = departure(departs=START + timedelta(minutes=3))
    event = DeparturesLoaded(
        pin=PINS[0], departures=(late,), until=START + HORIZON
    )
    state, _ = step(state, event)
    assert state.board.departures == ()
    assert state.board.loading


def test_stale_departures_failure_keeps_the_new_load() -> None:
    """A failure for the old place doesn't clear the new place's load."""
    state, _ = step(pinned(), top(steps=1))
    state, effects = step(state, DeparturesFailed(pin=PINS[0]))
    assert state.board.loading
    assert effects == ()


def test_modes_for_the_old_area_are_dropped_after_a_flip() -> None:
    """Modes or a modes failure for the old area land nowhere."""
    mpp = ZOOMS[DEFAULT_ZOOM]
    old = area_box(centre=PINS[0].origin.point or CENTRE, mpp=mpp)
    state, effects = step(pinned(view=LiveView.MAP), top(steps=1))
    [load] = effects_of(effects=effects, kind=LoadModes)
    assert load.box == area_box(centre=PINS[1].origin.point or CENTRE, mpp=mpp)
    event = ModesLoaded(mpp=mpp, box=old, modes=frozenset({Mode.FERRY}))
    state, _ = step(state, event)
    assert not state.map.area_modes
    request = LoadModes(mpp=mpp, box=old)
    state, _ = step(state, MapDataFailed(request=request))
    assert not state.map.failed_at
    assert state.map.pending_modes == {mpp}


def test_flip_forgets_the_old_places_map_failures() -> None:
    """A failure at the old place doesn't hold back the new place's map."""
    mpp = ZOOMS[DEFAULT_ZOOM]
    box = area_box(centre=PINS[0].origin.point or CENTRE, mpp=mpp)
    state = pinned(view=LiveView.MAP)
    request = LoadModes(mpp=mpp, box=box)
    state, _ = step(state, MapDataFailed(request=request))
    assert MapData.MODES in state.map.failed_at
    state, effects = step(state, top(steps=1))
    assert not state.map.failed_at
    assert effects_of(effects=effects, kind=LoadModes)


def saves(*, effects: tuple[Effect, ...]) -> list[SaveState]:
    """
    Pick the saves from some effects.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[SaveState]
        The saves, in order.
    """
    return effects_of(effects=effects, kind=SaveState)


def at(*, seconds: int) -> Tick:
    """
    Tick some seconds after START.

    Parameters
    ----------
    seconds : int
        Seconds after START.

    Returns
    -------
    Tick
        The tick.
    """
    return Tick(now=START + timedelta(seconds=seconds))


def test_save_waits_three_seconds_after_last_change() -> None:
    """A change is saved once, on the first tick 3 s after it."""
    state, _ = step(pinned(), at(seconds=10))
    state, _ = step(state, HOLD)
    state, _ = step(state, at(seconds=11))
    state, _ = step(state, top(steps=1))
    state, effects = step(state, at(seconds=13))
    assert not saves(effects=effects)
    state, effects = step(state, at(seconds=11 + 3))
    assert saves(effects=effects) == [SaveState(saved=saved_of(state=state))]
    assert state.saving.saved == saved_of(state=state)
    _, effects = step(state, at(seconds=20))
    assert not saves(effects=effects)
    assert SAVE_AFTER == timedelta(seconds=3)


def test_first_tick_saves_when_never_stored() -> None:
    """With no file yet, the first tick writes one."""
    saved = seeded(pin=PIN)
    state = initial_state(saved=saved, now=START, stored=False)
    assert state.saving.saved is None
    state, effects = step(state, Tick(now=START))
    assert saves(effects=effects) == [SaveState(saved=saved)]
    _, effects = step(state, at(seconds=60))
    assert not saves(effects=effects)


def test_no_save_without_change() -> None:
    """Ticks, scrolling, loads and views save only the call count."""
    state, _ = turned(state=loaded(state=pinned(), count=20), steps=2)
    for _ in LIVE_VIEWS:
        state, _ = step(state, PRESS)
    base = saved_of(state=state)
    writes: list[SaveState] = []
    for seconds in range(0, 3600, 5):
        state, effects = step(state, at(seconds=seconds))
        writes.extend(saves(effects=effects))
    assert writes
    assert len(writes) <= 6
    for save in writes:
        assert replace(save.saved, quota=base.quota) == base


def test_quit_saves_unsaved_changes_first() -> None:
    """Quitting with an unsaved change saves before stopping."""
    state, _ = step(pinned(), HOLD)
    state, effects = step(state, QuitRequested())
    assert effects == (SaveState(saved=saved_of(state=state)), Stop())
    assert not state.running


def test_new_timetable_version_matches_every_place() -> None:
    """A new version re-matches every place and reloads departures."""
    far = place('far', name='Far', point=(5.0, 5.0))
    pins = (replace(PINS[0], destination=far), *PINS[1:])
    saved = Saved(
        pins=pins,
        current=0,
        unpinned=ELSEWHERE,
        view=LiveView.STOP,
    )
    state = initial_state(saved=saved, now=START, stored=True)
    state = loaded(state=state)
    state, effects = step(state, TimetableSeen(version='v2'))
    origins = tuple(pin.origin for pin in PINS)
    assert MatchPlaces(
        places=(origins[0], far, *origins[1:], ELSEWHERE.origin),
        timetable='v2',
    ) in effects
    assert state.saving.timetable == 'v2'
    assert state.board.loaded_until is None
    _, effects = step(state, Tick(now=START))
    assert effects_of(effects=effects, kind=LoadDepartures)
    assert step(state, TimetableSeen(version='v2')) == (state, ())


def test_failed_match_is_retried_after_retry_after() -> None:
    """A failed place look-up asks again on the first tick 30 s later."""
    state, _ = step(loaded(state=pinned()), TimetableSeen(version='v2'))
    state, effects = step(state, PlacesFailed(timetable='v2'))
    assert not effects
    _, effects = step(state, at(seconds=25))
    assert not effects_of(effects=effects, kind=MatchPlaces)
    state, effects = step(state, at(seconds=30))
    assert effects_of(effects=effects, kind=MatchPlaces) == [
        MatchPlaces(places=stops_to_match(state=state), timetable='v2')
    ]
    _, effects = step(state, at(seconds=90))
    assert not effects_of(effects=effects, kind=MatchPlaces)


def test_failed_match_from_an_old_timetable_is_ignored() -> None:
    """A failure for another timetable version schedules no retry."""
    state, _ = step(loaded(state=pinned()), TimetableSeen(version='v2'))
    after, effects = step(state, PlacesFailed(timetable='v1'))
    assert (after, effects) == (state, ())


def test_new_timetable_cancels_a_match_retry() -> None:
    """A new version matches at once and drops the pending retry."""
    state, _ = step(loaded(state=pinned()), TimetableSeen(version='v2'))
    state, _ = step(state, PlacesFailed(timetable='v2'))
    state, effects = step(state, TimetableSeen(version='v3'))
    assert effects_of(effects=effects, kind=MatchPlaces)
    assert state.saving.match_failed_at is None


def test_unmatched_shown_pin_says_stop_not_in_the_new_timetable() -> None:
    """A missing shown pin's Stop view says it is not in the timetable."""
    state = loaded(state=pinned(), count=0)
    state, _ = step(state, PlacesMatched(found={'first': None}))
    assert state.places.pins[0].missing
    assert isinstance(state.last_view, StopView)
    assert state.last_view.empty == 'Stop not in the new timetable'


def test_matched_id_change_updates_pin_and_reloads() -> None:
    """A re-match onto a new ID shows the new stop and loads it."""
    moved = place('first-2', name='first', point=(105.0, 0.0))
    state = loaded(state=pinned(view=LiveView.MAP))
    state, effects = step(state, PlacesMatched(found={'first': moved}))
    assert shown(state=state) == Pin(origin=moved)
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == Pin(origin=moved)
    assert state.board.departures == ()
    assert state.map.centre == moved.point


def test_matched_name_only_refresh_keeps_the_board() -> None:
    """A re-match under the same ID keeps the departures loaded."""
    renamed = replace(PINS[0].origin, name='First St')
    state = loaded(state=pinned())
    before = state.board
    state, effects = step(state, PlacesMatched(found={'first': renamed}))
    assert state.board == before
    assert not effects_of(effects=effects, kind=LoadDepartures)
    assert isinstance(state.last_view, StopView)
    assert state.last_view.origin == 'First St'


def test_no_timetable_matches_nothing() -> None:
    """With no timetable there is nothing to match against."""
    state, _ = step(pinned(), TimetableSeen(version='v1'))
    state, effects = step(state, TimetableSeen(version=None))
    assert not effects_of(effects=effects, kind=MatchPlaces)
    assert state.saving.timetable is None


@pytest.mark.parametrize('failed', [False, True])
def test_load_from_the_old_timetable_is_dropped(failed: bool) -> None:
    """A reply to a load asked before a timetable swap is ignored."""
    state, _ = step(pinned(), TimetableSeen(version='v1'))
    state, effects = step(state, at(seconds=5))
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.timetable == 'v1'
    state, _ = step(state, TimetableSeen(version='v2'))
    late = departure(departs=START + timedelta(minutes=3))
    reply: Event = DeparturesLoaded(
        pin=load.pin, departures=(late,), until=load.end, timetable='v1'
    )
    if failed:
        reply = DeparturesFailed(pin=load.pin, timetable='v1')
    state, _ = step(state, reply)
    assert state.board.loaded_until is None
    assert state.board.departures == ()
    _, effects = step(state, at(seconds=10))
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.timetable == 'v2'


NOON = datetime(2026, 10, 16, 12, 0, tzinfo=SYDNEY)


def fetches_at(*, state: State, now: datetime) -> list[Effect]:
    """
    Give the trip-updates fetches a Tick asks for.

    Parameters
    ----------
    state : State
        State before the Tick.
    now : datetime
        The Tick's time.

    Returns
    -------
    list[Effect]
        The FetchFeed effects.
    """
    _, effects = step(state, Tick(now=now))
    return [effect for effect in effects if isinstance(effect, FetchFeed)]


def test_faster_rate_applies_next_tick() -> None:
    """A faster off-peak rate fetches on the next Tick past it."""
    state, _ = step(loaded(state=fresh(now=NOON)), Tick(now=NOON))
    later = NOON + timedelta(seconds=40)
    assert not fetches_at(state=state, now=later)
    state, _ = step(state, SettingsChanged(offpeak_rate=Rate.FAST))
    assert fetches_at(state=state, now=later) == [FetchFeed(feed=BUSES)]


def test_off_peak_waits_two_minutes() -> None:
    """Off-peak, a weekday polls every two minutes."""
    eleven = NOON - timedelta(hours=1)
    state, _ = step(loaded(state=fresh(now=eleven)), Tick(now=eleven))
    assert not fetches_at(state=state, now=eleven + timedelta(seconds=60))
    assert fetches_at(state=state, now=eleven + timedelta(seconds=120))


def test_timetable_seen_loads_holidays() -> None:
    """A new timetable asks for its holidays; another version's are stale."""
    state, effects = step(pinned(), TimetableSeen(version='v'))
    assert LoadHolidays(timetable='v') in effects
    holidays = frozenset({date(2026, 1, 26)})
    other = HolidaysLoaded(dates=holidays, timetable='w')
    assert step(state, other) == (state, ())
    state, _ = step(state, HolidaysLoaded(dates=holidays, timetable='v'))
    assert state.calendar.holidays == holidays


def test_settings_reach_stop_view() -> None:
    """Larger text and inverted lists show in the redrawn Stop view."""
    state, _ = step(pinned(), SettingsChanged(larger_text=True))
    state, _ = step(state, Tick(now=START + timedelta(seconds=5)))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.large
    assert not state.last_view.inverted
    state, _ = step(state, SettingsChanged(inverted_lists=True))
    state, _ = step(state, Tick(now=START + timedelta(seconds=10)))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.inverted


def test_alerts_counted_in_quota() -> None:
    """The alert fetches of a tick count against the day's quota."""
    state = loaded(state=fresh())
    after, effects = step(state, Tick(now=START + ALERT_EVERY))
    fetches = [e for e in effects if isinstance(e, FetchFeed)]
    assert FetchFeed(feed=ALERTS, kind=FeedKind.ALERTS) in fetches
    assert after.quota.calls - state.quota.calls == len(fetches)


def test_auto_saved_and_restored() -> None:
    """The auto pin state is saved and comes back at start-up."""
    auto = AutoState(overridden_at=START)
    state = fresh()
    assert saved_of(state=replace(state, auto=auto)).auto == auto
    saved = replace(seeded(pin=PIN), auto=auto)
    assert initial_state(saved=saved, now=START, stored=True).auto == auto


UNSYNCED_AT: Final[datetime] = datetime(2026, 11, 5, 23, 10, tzinfo=SYDNEY)
SYNCED_AT: Final[datetime] = datetime(2026, 11, 6, 7, 42, tzinfo=SYDNEY)


def clock_waiting(
    *, state: State | None = None
) -> tuple[State, tuple[Effect, ...]]:
    """
    Start on an unsynced clock and load departures.

    Parameters
    ----------
    state : State | None
        State before the first tick; a fresh one by default.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with departures loaded, and every effect so far.
    """
    state, effects = step(state or fresh(), Tick(now=START, synced=False))
    deps = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=deps,
        until=START + HORIZON + RELOAD_BEFORE,
    )
    state, more = step(state, event)
    return state, (*effects, *more)


def drawn_or_fetched(*, effects: tuple[Effect, ...]) -> list[Effect]:
    """
    Pick out the renders and feed fetches.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[Effect]
        The Render and FetchFeed effects, in order.
    """
    return [
        effect
        for effect in effects
        if isinstance(effect, Render | FetchFeed)
    ]


def test_unsynced_clock_draws_nothing() -> None:
    """For 90 s of unsynced ticks nothing is drawn or fetched."""
    state, effects = clock_waiting()
    for seconds in range(5, 95, 5):
        state, more = step(
            state, Tick(now=START + timedelta(seconds=seconds), synced=False),
        )
        effects = (*effects, *more)
    assert drawn_or_fetched(effects=effects) == []
    assert state.last_view is None


def test_clock_wait_gives_up_after_two_minutes() -> None:
    """With no sync, the first frame comes once `CLOCK_WAIT` has passed."""
    state, effects = clock_waiting()
    late = START + CLOCK_WAIT - timedelta(seconds=5)
    state, more = step(state, Tick(now=late, synced=False))
    assert drawn_or_fetched(effects=(*effects, *more)) == []
    state, more = step_answered(
        state=state, event=Tick(now=START + CLOCK_WAIT, synced=False),
    )
    assert any(isinstance(effect, FetchFeed) for effect in more)
    assert sum(isinstance(effect, Render) for effect in more) == 1


def test_sync_jump_starts_afresh() -> None:
    """A synced tick hours later reloads, fires a cycle and draws once."""
    state, _ = step(fresh(now=UNSYNCED_AT), Tick(now=UNSYNCED_AT, synced=False))
    state, early = step(
        state, Tick(now=UNSYNCED_AT + timedelta(seconds=5), synced=False),
    )
    state, effects = step(state, Tick(now=SYNCED_AT))
    assert state.clock_wait_until is None
    assert state.refresh.started_at == SYNCED_AT
    assert LoadDepartures(
        pin=PIN,
        start=SYNCED_AT - LOOKBACK,
        end=SYNCED_AT + HORIZON + RELOAD_BEFORE,
    ) in effects
    assert not any(isinstance(effect, ProbeDevice) for effect in early)
    assert any(isinstance(effect, ProbeDevice) for effect in effects)
    deps = tuple(
        departure(departs=SYNCED_AT + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    state, loads = step_answered(
        state=state,
        event=DeparturesLoaded(
            pin=PIN,
            departures=deps,
            until=SYNCED_AT + HORIZON + RELOAD_BEFORE,
        ),
    )
    assert any(isinstance(effect, FetchFeed) for effect in loads)
    renders = [e for e in (*effects, *loads) if isinstance(e, Render)]
    assert len(renders) == 1
    assert state.last_view is not None


def test_sync_jump_fires_a_cycle_on_the_tick() -> None:
    """Feeds needed during the wait are fetched on the synced tick."""
    state, _ = clock_waiting()
    later = START + timedelta(hours=3, seconds=7)
    state, effects = step(state, Tick(now=later))
    assert state.cycle.started == later
    assert any(isinstance(effect, FetchFeed) for effect in effects)
    assert not any(isinstance(effect, Render) for effect in effects)


def test_sync_clears_the_wrong_date_board() -> None:
    """Departures loaded before the clock was set are dropped at once."""
    state, _ = clock_waiting()
    assert state.board.departures
    later = START + timedelta(hours=3, seconds=7)
    state, effects = step(state, Tick(now=later))
    assert state.board.departures == ()
    assert state.board.loading
    assert not any(isinstance(effect, Render) for effect in effects)


def test_load_ending_before_now_is_dropped() -> None:
    """A load whose window has ended when it lands is not shown."""
    state, _ = clock_waiting()
    later = START + timedelta(hours=5, seconds=7)
    state, _ = step(state, Tick(now=later))
    old = DeparturesLoaded(
        pin=shown(state=state),
        departures=(departure(departs=START + timedelta(minutes=3)),),
        until=START + HORIZON + RELOAD_BEFORE,
    )
    state, effects = step(state, old)
    assert state.board.departures == ()
    assert state.board.loaded_until is None
    assert not any(isinstance(effect, Render) for effect in effects)
    state, more = step(state, Tick(now=later + timedelta(seconds=5)))
    assert any(isinstance(effect, LoadDepartures) for effect in more)


def test_clock_wait_expiry_makes_probe_due() -> None:
    """The device is probed on the tick the clock wait gives up."""
    state, _ = clock_waiting()
    state, _ = step(
        state, Tick(now=START + timedelta(seconds=5), synced=False),
    )
    late = START + CLOCK_WAIT - timedelta(seconds=5)
    state, _ = step(state, Tick(now=late, synced=False))
    state = replace(
        state,
        panel=replace(state.panel, probed_at=late, probing=False),
    )
    state, effects = step(
        state, Tick(now=START + CLOCK_WAIT, synced=False),
    )
    assert any(isinstance(effect, ProbeDevice) for effect in effects)
    state, effects = step(
        state,
        Tick(now=START + CLOCK_WAIT + timedelta(seconds=5), synced=False),
    )
    assert not any(isinstance(effect, ProbeDevice) for effect in effects)


def test_clock_wait_holds_only_once() -> None:
    """A clock stepping back after the give-up does not hold again."""
    state, _ = clock_waiting()
    state, _ = step(state, Tick(now=START + CLOCK_WAIT, synced=False))
    back = START + timedelta(seconds=30)
    state, _ = step(state, Tick(now=back, synced=False))
    assert not clock_held(state=state)
    state, _ = step(state, Tick(now=back + timedelta(hours=3)))
    assert state.refresh.started_at == back + timedelta(hours=3)
    assert state.clock_wait_until is None


def test_synced_start_never_waits() -> None:
    """A synced first tick sets no clock wait."""
    state, _ = step(fresh(), Tick(now=START))
    assert state.clock_wait_until is None


def test_events_before_the_first_tick_wait_for_it() -> None:
    """Nothing is drawn or fetched until a tick reports the clock."""
    state = replace(fresh(), clock_known=False)
    deps = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    state, effects = step(
        state,
        DeparturesLoaded(
            pin=shown(state=state),
            departures=deps,
            until=START + HORIZON + RELOAD_BEFORE,
        ),
    )
    assert drawn_or_fetched(effects=effects) == []
    assert state.last_view is None
    state, effects = step_answered(
        state=state, event=Tick(now=START + timedelta(seconds=5)),
    )
    assert state.clock_known
    assert any(isinstance(effect, FetchFeed) for effect in effects)
    assert sum(isinstance(effect, Render) for effect in effects) == 1


def test_unsynced_first_tick_starts_the_wait() -> None:
    """An unsynced first tick after start-up waits as usual."""
    state = replace(fresh(), clock_known=False)
    state, effects = clock_waiting(state=state)
    assert state.clock_known
    assert state.clock_wait_until == START + CLOCK_WAIT
    assert drawn_or_fetched(effects=effects) == []


def test_unsynced_again_after_drawing_does_not_wait() -> None:
    """Once a frame is drawn, an unsynced tick starts no new wait."""
    state = loaded(state=fresh())
    assert state.last_view is not None
    state, _ = step(
        state, Tick(now=START + timedelta(seconds=5), synced=False),
    )
    assert state.clock_wait_until is None


def test_knob_draws_during_clock_wait() -> None:
    """A person's turn draws at once while the clock is unsynced."""
    state, _ = clock_waiting()
    _, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=1))
    assert sum(isinstance(effect, Render) for effect in effects) == 1


def test_menu_draws_during_clock_wait() -> None:
    """A menu opened by a person draws while the clock is unsynced."""
    state, _ = clock_waiting()
    _, effects = step(state, KnobsHeld())
    assert sum(isinstance(effect, Render) for effect in effects) == 1


def test_notice_draws_during_clock_wait() -> None:
    """Shutting down is drawn while the clock is unsynced."""
    state, _ = clock_waiting()
    _, effects = step(state, PowerRequested(action=PowerAction.OFF))
    renders = [e for e in effects if isinstance(e, Render)]
    assert [type(render.view) for render in renders] == [NoticeView]


def test_sleep_screen_held_during_clock_wait() -> None:
    """In quiet hours the sleep screen waits for the clock too."""
    quiet = Window(start=time(7), end=time(9))
    state = fresh()
    device = replace(state.settings.device, my_hours=quiet)
    state = replace(state, settings=replace(state.settings, device=device))
    state, effects = clock_waiting(state=state)
    state, more = step(
        state, Tick(now=START + timedelta(seconds=5), synced=False),
    )
    assert state.panel.asleep
    assert drawn_or_fetched(effects=(*effects, *more)) == []
    _, more = step(state, Tick(now=START + CLOCK_WAIT, synced=False))
    renders = [e for e in more if isinstance(e, Render)]
    assert [type(render.view) for render in renders] == [SleepView]
