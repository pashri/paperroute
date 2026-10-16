"""Tests for the reducer."""

from dataclasses import replace
from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.model import SYDNEY, Mode, Pin, shifted
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.state import (
    POLL_EVERY,
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
    QuitRequested,
    Render,
    State,
    Stop,
    Tick,
    initial_state,
    step,
)
from paperroute.core.stop_view import HORIZON, VISIBLE_ROWS
from tests.core.factories import START, departure

PIN = Pin(origin='2145615', destination='215020')
BUSES = 'v1/gtfs/realtime/buses'


def fresh(*, now: datetime = START) -> State:
    """
    A state with nothing loaded.

    Parameters
    ----------
    now : datetime
        Start time.

    Returns
    -------
    State
        Initial state.
    """
    return initial_state(
        pin=PIN, origin='Hollis St', destination='Parramatta', now=now
    )


def loaded(
    *, state: State, count: int = 3, mode: Mode = Mode.BUS
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

    Returns
    -------
    State
        State after.
    """
    deps = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)), mode=mode)
        for i in range(count)
    )
    event = DeparturesLoaded(
        departures=deps, until=START + timedelta(hours=3)
    )
    return step(state, event)[0]


def test_first_tick_loads_departures() -> None:
    """The first tick asks for three hours of departures."""
    _, effects = step(fresh(), Tick(now=START))
    end = START + timedelta(hours=3)
    assert LoadDepartures(pin=PIN, start=START, end=end) in effects


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
    assert state.last_view is not None
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
    """Departures reload when less than 30 minutes remain loaded."""
    state = loaded(state=fresh())
    now = START + timedelta(hours=2, minutes=45)
    _, effects = step(state, Tick(now=now))
    assert any(isinstance(effect, LoadDepartures) for effect in effects)


def test_scroll_is_clamped_and_recorded() -> None:
    """Over-scrolling stores the clamped offset; turning back moves rows."""
    state = loaded(state=fresh(), count=20)
    state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=50))
    assert state.last_view is not None
    assert state.scroll == state.last_view.scroll
    assert state.scroll < 50
    clamped_rows = state.last_view.rows
    state, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=-1))
    assert state.last_view is not None
    assert state.scroll == state.last_view.scroll
    renders = [e for e in effects if isinstance(e, Render)]
    assert [r.view.rows for r in renders] != [clamped_rows]
    assert len(renders) == 1


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
        departures=deps, until=START + timedelta(hours=3)
    )
    state, _ = step(fresh(), event)
    _, effects = step(state, Tick(now=START))
    fetched = [e.feed for e in effects if isinstance(e, FetchFeed)]
    assert sorted(fetched) == [
        'v1/gtfs/realtime/buses',
        'v1/gtfs/realtime/nswtrains',
        'v2/gtfs/realtime/sydneytrains',
    ]


def test_load_end_is_horizon_real_seconds_on_fall_back_night() -> None:
    """The load window ends three real hours on, across the repeated hour."""
    now = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY)
    _, effects = step(fresh(now=now), Tick(now=now))
    loads = [e for e in effects if isinstance(e, LoadDepartures)]
    assert len(loads) == 1
    gap = loads[0].end.timestamp() - now.timestamp()
    assert gap == HORIZON.total_seconds()


def test_quit_stops() -> None:
    """Quitting stops the loop."""
    state, effects = step(fresh(), QuitRequested())
    assert not state.running
    assert effects == (Stop(),)


FALL_BACK_EVE = datetime(2026, 4, 5, 1, 30, tzinfo=SYDNEY)
MODES = [Mode.BUS, Mode.TRAIN, Mode.REGIONAL, Mode.FERRY]


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
    kinds = ['tick', 'turn', 'press', 'fail', 'ok', 'load', 'unreadable']
    for kind in draw(st.lists(st.sampled_from(kinds), max_size=40)):
        if kind == 'tick':
            now = shifted(moment=now, seconds=draw(st.integers(0, 900)))
            events.append(Tick(now=now))
        elif kind == 'turn':
            knob = draw(st.sampled_from(list(Knob)))
            steps = draw(st.integers(-6, 6))
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
        else:
            count = draw(st.integers(0, 25))
            deps = tuple(
                departure(
                    departs=shifted(moment=now, seconds=120 * i),
                    mode=draw(st.sampled_from(MODES)),
                )
                for i in range(count)
            )
            until = shifted(moment=now, seconds=3 * 3600)
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
    if after.last_view is not None:
        assert after.scroll == after.last_view.scroll
    for effect in effects:
        if isinstance(effect, Render):
            assert len(effect.view.rows) <= VISIBLE_ROWS
            assert effect.view != before.last_view
        if isinstance(effect, LoadDepartures):
            assert not before.loading
        if isinstance(effect, FetchFeed):
            assert effect.feed in after.feeds
            assert isinstance(event, Tick)
            if before.last_fetch is not None:
                gap = event.now.timestamp() - before.last_fetch.timestamp()
                assert gap >= POLL_EVERY.total_seconds()


@given(
    st.sampled_from([START, FALL_BACK_EVE]).flatmap(
        lambda start: st.tuples(st.just(start), event_lists(start))
    )
)
def test_step_invariants(case: tuple[datetime, list[Event]]) -> None:
    """No event sequence breaks the reducer's invariants."""
    start, events = case
    state = fresh(now=start)
    for event in events:
        before = state
        state, effects = step(before, event)
        check_effects(
            before=before, after=state, event=event, effects=effects
        )
