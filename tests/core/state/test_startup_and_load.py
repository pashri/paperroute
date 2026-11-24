"""Tests for state: startup and load."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute_device.core.kit_effects import Render, Stop
from paperroute_device.core.kit_events import QuitRequested, Tick
from paperroute_hardware.core.knob_events import Knob, KnobHeld, KnobTurned
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyFeed
from paperroute_testkit.core.factories import START, begun, departure
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import step_answered, wakeful
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    LoadDepartures,
)
from paperroute_transit.core.places import shown
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import BoardSettings, Settings, seeded
from paperroute_transit.core.state_model import (
    LOOKBACK,
    POLL_EVERY,
    RELOAD_BEFORE,
)
from paperroute_transit.core.stop_view import HORIZON, StopView
from tests.core.state.helpers import (
    BUSES,
    CENTRE,
    PIN,
    PINS,
    fresh,
    loaded,
    trip_fetches,
)


def test_initial_state_centres_on_the_pins_point() -> None:
    """The Map starts centred on the pin's From."""
    assert fresh(centre=CENTRE).map.centre == CENTRE
    assert fresh().map.centre is None


def test_initial_state_carries_city() -> None:
    """The state carries the city it was started for, out of its repr."""
    state = initial_state(
        saved=seeded(
            pin=PIN,
            city=SYDNEY,
        ), now=START, city=SYDNEY,
    )
    assert state.city is SYDNEY
    assert 'city' not in repr(state)


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
    saved = replace(seeded(pin=PIN, city=SYDNEY), pins=pins)
    state = begun(saved=saved)
    state, _ = step(state, Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'Home 1/2'
    state, _ = step(state, KnobHeld(knob=Knob.BOTTOM))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'not pinned'


def test_stop_view_without_a_destination_names_none() -> None:
    """A pin with no Destination gives the view no destination."""
    saved = seeded(pin=replace(PIN, destination=None), city=SYDNEY)
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
    first = datetime(2026, 4, 5, 2, 30, tzinfo=SYDNEY_ZONE)
    second = datetime(2026, 4, 5, 2, 30, 10, tzinfo=SYDNEY_ZONE, fold=1)
    assert second.timestamp() - first.timestamp() == 3610
    state = replace(wakeful(state=loaded(state=fresh())), last_fetch=first)
    _, effects = step(state, Tick(now=second))
    assert FetchFeed(feed=BUSES) in effects


def test_step_no_feed_for_ferry_pin() -> None:
    """A ferry pin fetches only alerts and shows the no-feed banner."""
    state, effects = step(
        loaded(state=fresh(), mode=SydneyFeed.FERRY), Tick(now=START)
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
    modes = [
        SydneyFeed.BUS,
        SydneyFeed.TRAIN,
        SydneyFeed.REGIONAL,
        SydneyFeed.FERRY,
    ]
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
    now = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY_ZONE)
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
