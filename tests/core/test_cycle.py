"""Tests for the poll cycle timing."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import PowerAction, WifiStatus
from paperroute_device.core.device_settings import Window
from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import DeviceProbed, Tick
from paperroute_device.core.panel_views import NoticeView
from paperroute_device_testkit.device_fakes import device_facts
from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_sydney.city import SYDNEY as SYDNEY_CITY
from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import START, begun, departure, snapshot
from paperroute_testkit.core.kit_steps import settings_posted, step
from paperroute_testkit.core.steps import slept
from paperroute_testkit.fake_city import TEST_PROJECTION, NorthbyFeeds
from paperroute_transit.core.alert_model import AlertsFetched, AlertsSnapshot
from paperroute_transit.core.alert_state import ALERT_EVERY
from paperroute_transit.core.city import City
from paperroute_transit.core.cycle import (
    alerts_due,
    answered,
    boundary,
    cycle_due,
)
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Effect,
    Estimating,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    PositionsFetched,
    ScreenView,
    SettingsChanged,
)
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.places import shown
from paperroute_transit.core.realtime import (
    RealtimeSnapshot,
    StopUpdate,
    TripRealtime,
)
from paperroute_transit.core.rhythm import stale_after
from paperroute_transit.core.saved import seeded
from paperroute_transit.core.state_model import RELOAD_BEFORE, Cycle, State
from paperroute_transit.core.state_views import current_view, live_stop_view
from paperroute_transit.core.stop_view import HORIZON
from tests.core.test_quiet import at as quiet_at
from tests.core.test_quiet import evening
from tests.core.test_state import (
    ALERTS,
    BUSES,
    CENTRE,
    PIN,
    PRESS,
    fresh,
    loaded,
)

SYDNEY = ZoneInfo('Australia/Sydney')
MIDNIGHT: Final[datetime] = START.replace(hour=0, minute=0)
ESTIMATING_CITY: Final[City] = replace(
    SYDNEY_CITY,
    feeds=NorthbyFeeds(
        projection=TEST_PROJECTION, estimated=frozenset({'bus'}),
    ),
)


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 6, hour, minute, second, tzinfo=SYDNEY)


@pytest.mark.parametrize(
    ('now', 'every', 'expected'),
    [
        (_at(10, 0, 47), timedelta(seconds=30), _at(10, 0, 30)),
        (_at(10, 0, 47), timedelta(seconds=60), _at(10, 0, 0)),
        (_at(10, 3, 10), timedelta(minutes=2), _at(10, 2, 0)),
        (_at(10, 4, 59), timedelta(minutes=5), _at(10, 0, 0)),
    ],
)
def test_boundary_aligns_to_clock(
    now: datetime,
    every: timedelta,
    expected: datetime,
) -> None:
    assert boundary(now=now, every=every) == expected


def test_boundary_on_fall_back_night() -> None:
    every = timedelta(seconds=60)
    start = datetime(2026, 4, 5, 1, 58, tzinfo=SYDNEY)
    found = [
        boundary(
            now=datetime.fromtimestamp(
                start.timestamp() + 45 * i,
                tz=SYDNEY,
            ),
            every=every,
        )
        for i in range(240)
    ]
    stamps = [moment.timestamp() for moment in found]
    distinct = sorted(set(stamps))
    assert len(distinct) > 100
    gaps = {
        later - earlier
        for earlier, later in zip(distinct, distinct[1:])
    }
    assert gaps == {60.0}
    assert stamps == sorted(stamps)


def test_cycle_due_after_clock_steps_back() -> None:
    now = _at(10, 0, 5)
    cycle = Cycle(started=now + timedelta(hours=1))
    assert cycle_due(cycle=cycle, now=now, every=timedelta(seconds=30))


def test_cycle_due_when_none_started() -> None:
    assert cycle_due(
        cycle=Cycle(),
        now=_at(10, 0, 5),
        every=timedelta(seconds=30),
    )


def test_cycle_due_only_after_boundary() -> None:
    every = timedelta(seconds=30)
    cycle = Cycle(started=_at(10, 0, 31))
    assert not cycle_due(cycle=cycle, now=_at(10, 0, 59), every=every)
    assert cycle_due(cycle=cycle, now=_at(10, 1, 1), every=every)


def test_alerts_due_only_on_five_minute_boundary() -> None:
    cycle = Cycle(alerts_at=_at(10, 0, 3))
    assert ALERT_EVERY == timedelta(minutes=5)
    assert not alerts_due(cycle=cycle, now=_at(10, 4, 59))
    assert alerts_due(cycle=cycle, now=_at(10, 5, 1))
    assert alerts_due(cycle=Cycle(), now=_at(10, 5, 1))


def test_cycle_fires_once_per_boundary_on_fall_back_night() -> None:
    every = timedelta(seconds=60)
    start = datetime(2026, 4, 5, 1, 58, tzinfo=SYDNEY)
    cycle = Cycle()
    fired = 0
    for i in range(int(timedelta(hours=2) / timedelta(seconds=5))):
        now = datetime.fromtimestamp(start.timestamp() + 5 * i, tz=SYDNEY)
        if cycle_due(cycle=cycle, now=now, every=every):
            fired += 1
            cycle = Cycle(started=now)
    assert fired == 120


def _fetches(*, effects: tuple[Effect, ...]) -> list[FetchFeed]:
    """Keep the FetchFeed effects."""
    return [effect for effect in effects if isinstance(effect, FetchFeed)]


def _kinds(*, effects: tuple[Effect, ...]) -> set[FeedKind]:
    """List the kinds of feed fetched."""
    return {fetch.kind for fetch in _fetches(effects=effects)}


def _offline() -> DeviceProbed:
    """Facts saying no Wi-Fi network is joined."""
    facts = replace(device_facts(), wifi=WifiStatus(managed=True))
    return DeviceProbed(facts=facts)


def _online() -> DeviceProbed:
    """Facts saying a Wi-Fi network is joined."""
    wifi = WifiStatus(managed=True, ssid='home')
    return DeviceProbed(facts=replace(device_facts(), wifi=wifi))


def _ticked(*, state: State, seconds: int) -> tuple[State, tuple[Effect, ...]]:
    """Tick some seconds after START."""
    return step(state, Tick(now=START + timedelta(seconds=seconds)))


def test_cycle_fires_every_due_feed_together() -> None:
    """A Tick past a boundary on the Map fetches every kind of feed at once."""
    state, _ = step(loaded(state=fresh()), PRESS)
    state, effects = _ticked(state=state, seconds=5 * 60 + 2)
    assert _kinds(effects=effects) == {
        FeedKind.TRIPS,
        FeedKind.POSITIONS,
        FeedKind.ALERTS,
    }
    assert state.cycle.started == START + timedelta(seconds=5 * 60 + 2)
    assert state.cycle.waiting == frozenset(
        fetch.feed for fetch in _fetches(effects=effects)
    )


def test_no_second_cycle_before_next_boundary() -> None:
    """At 30 s, Ticks between boundaries fetch nothing."""
    state, effects = _ticked(state=loaded(state=fresh()), seconds=30)
    assert FetchFeed(feed=BUSES) in effects
    for seconds in (35, 55):
        state, effects = _ticked(state=state, seconds=seconds)
        assert not _fetches(effects=effects)
    _, effects = _ticked(state=state, seconds=60)
    assert FetchFeed(feed=BUSES) in effects


def test_stop_view_cycle_polls_its_heros_positions() -> None:
    """The Stop view's cycle fetches its hero's positions."""
    state = loaded(state=fresh(centre=CENTRE), kind=SydneyMode.BUS)
    _, effects = _ticked(state=state, seconds=5 * 60)
    assert _kinds(effects=effects) == {
        FeedKind.TRIPS, FeedKind.ALERTS, FeedKind.POSITIONS,
    }


def test_new_feed_fetched_at_once() -> None:
    """Switching to the Map fetches its positions and joins the cycle."""
    state, _ = _ticked(state=loaded(state=fresh()), seconds=30)
    started = state.cycle.started
    state, effects = step(state, PRESS)
    positions = {
        fetch.feed
        for fetch in _fetches(effects=effects)
        if fetch.kind == FeedKind.POSITIONS
    }
    assert positions
    assert positions <= state.cycle.waiting
    assert state.cycle.started == started


def test_departures_loading_after_first_cycle_fetch_at_once() -> None:
    """Departures loading mid-cycle fetch their trip feed at once."""
    state, effects = step(fresh(centre=CENTRE), PRESS)
    started = state.cycle.started
    assert started is not None
    assert FetchFeed(feed=BUSES) not in effects
    state = replace(state, now=START + timedelta(seconds=10))
    state, effects = step(
        state,
        DeparturesLoaded(
            pin=shown(state=state),
            departures=(departure(departs=START + timedelta(minutes=3)),),
            until=START + HORIZON + RELOAD_BEFORE,
        ),
    )
    assert FetchFeed(feed=BUSES) in effects
    assert BUSES in state.cycle.waiting
    assert state.cycle.started == started


def test_nothing_needed_starts_no_cycle() -> None:
    """With no feed needed, no cycle starts."""
    state, effects = _ticked(state=fresh(), seconds=0)
    assert not _fetches(effects=effects)
    assert state.cycle.started is None


def test_held_feeds_record_no_cycle() -> None:
    """Offline, no cycle runs; back online, one starts at once."""
    state, _ = step(loaded(state=fresh()), _offline())
    state, effects = _ticked(state=state, seconds=30)
    assert not _fetches(effects=effects)
    assert state.cycle.started is None
    assert not state.cycle.waiting
    state, probed = step(state, _online())
    state, effects = _ticked(state=state, seconds=35)
    assert _fetches(effects=(*probed, *effects)).count(
        FetchFeed(feed=BUSES),
    ) == 1


def test_wake_starts_cycle_at_once() -> None:
    """Waking from Sleep now starts a cycle off the boundary."""
    state, _ = _ticked(state=loaded(state=fresh()), seconds=30)
    state = replace(slept(state=state), now=START + timedelta(seconds=40))
    state, effects = step(state, PRESS)
    assert not state.panel.asleep
    assert FetchFeed(feed=BUSES) in effects
    assert state.cycle.started == START + timedelta(seconds=40)


def test_fetch_times_still_recorded() -> None:
    """A cycle records the fetch times other code reads."""
    state, _ = step(loaded(state=fresh()), PRESS)
    state, effects = _ticked(state=state, seconds=30)
    now = START + timedelta(seconds=30)
    positions = [
        fetch.feed
        for fetch in _fetches(effects=effects)
        if fetch.kind == FeedKind.POSITIONS
    ]
    assert positions
    assert state.last_fetch == now
    assert all(state.map.fetched_at[feed] == now for feed in positions)
    assert set(state.alerts.fetched_at.values()) == {START}


def test_slow_cycle_does_not_delay_next() -> None:
    """Unanswered feeds do not hold back the next cycle."""
    state, _ = _ticked(state=loaded(state=fresh()), seconds=30)
    assert BUSES in state.cycle.waiting
    state, effects = _ticked(state=state, seconds=60)
    assert FetchFeed(feed=BUSES) in effects
    assert state.cycle.started == START + timedelta(seconds=60)


def test_answers_leave_waiting() -> None:
    """A failure and an alerts download each stop the wait for their feed."""
    state, _ = _ticked(state=loaded(state=fresh()), seconds=5 * 60)
    assert state.cycle.waiting == {BUSES, ALERTS}
    later = START + timedelta(seconds=5 * 60 + 1)
    state, _ = step(state, FeedFailed(feed=BUSES, at=later, reason='HTTP 500'))
    assert state.cycle.waiting == {ALERTS}
    state, _ = step(
        state,
        AlertsFetched(
            feed=ALERTS,
            snapshot=AlertsSnapshot(
                feed=ALERTS, fetched_at=later, alerts=(),
            ),
        ),
    )
    assert not state.cycle.waiting


def test_positions_answer_leaves_waiting() -> None:
    """A positions download stops the wait for its feed."""
    state, _ = step(loaded(state=fresh()), PRESS)
    feed = next(iter(state.cycle.waiting - {BUSES, ALERTS}))
    state, _ = step(
        state,
        PositionsFetched(
            feed=feed,
            snapshot=snapshot(fetched_at=START, vehicles=()),
        ),
    )
    assert feed not in state.cycle.waiting


def test_answered_ignores_unknown_feed() -> None:
    """Only a feed waited on is removed."""
    state = replace(fresh(), cycle=Cycle(waiting=frozenset({BUSES})))
    assert answered(state=state, feed='other').cycle.waiting == {BUSES}
    assert not answered(state=state, feed=BUSES).cycle.waiting


def test_quiet_sleep_leaves_nothing_waiting() -> None:
    """Falling asleep for quiet hours leaves no cycle under way."""
    state, effects = step(evening(), Tick(now=quiet_at(23)))
    assert state.panel.asleep
    assert not _fetches(effects=effects)
    assert state.cycle == Cycle()


HELD_AT: datetime = START + timedelta(seconds=5 * 60 + 2)


def _renders(*, effects: tuple[Effect, ...]) -> list[Render]:
    """Keep the Render effects."""
    return [effect for effect in effects if isinstance(effect, Render)]


def _answer(*, fetch: FetchFeed) -> Event:
    """A download answering a fetch."""
    match fetch.kind:
        case FeedKind.POSITIONS:
            return PositionsFetched(
                feed=fetch.feed,
                snapshot=snapshot(fetched_at=HELD_AT, vehicles=()),
            )
        case FeedKind.ALERTS:
            return AlertsFetched(
                feed=fetch.feed,
                snapshot=AlertsSnapshot(
                    feed=fetch.feed, fetched_at=HELD_AT, alerts=(),
                ),
            )
    return FeedFetched(
        feed=fetch.feed,
        snapshot=RealtimeSnapshot(fetched_at=HELD_AT, trips={}),
    )


def _held(*, on_map: bool = True) -> tuple[State, list[FetchFeed]]:
    """A cycle started on a boundary that moves the clock, on the Map."""
    state = loaded(state=fresh())
    if on_map:
        state, _ = step(state, PRESS)
    state, effects = step(state, Tick(now=HELD_AT))
    assert not _renders(effects=effects)
    return state, _fetches(effects=effects)


def test_held_until_last_response() -> None:
    """A cycle's frame is drawn once, when its last feed answers."""
    state, fetches = _held()
    assert {fetch.kind for fetch in fetches} == {
        FeedKind.TRIPS,
        FeedKind.POSITIONS,
        FeedKind.ALERTS,
    }
    *first, last = fetches
    for fetch in first:
        state, effects = step(state, _answer(fetch=fetch))
        assert not _renders(effects=effects)
    _, effects = step(state, _answer(fetch=last))
    assert len(_renders(effects=effects)) == 1


def test_all_failed_releases_hold() -> None:
    """Feeds all backing off still release the frame on the last one."""
    state, fetches = _held()
    *first, last = fetches
    for fetch in first:
        state, effects = step(
            state,
            FeedFailed(
                feed=fetch.feed, at=HELD_AT, reason='backing off', sent=False,
            ),
        )
        assert not _renders(effects=effects)
    _, effects = step(
        state,
        FeedFailed(feed=last.feed, at=HELD_AT, reason='backing off', sent=False),
    )
    assert len(_renders(effects=effects)) == 1


def test_hold_ends_at_deadline() -> None:
    """With one feed never answering, the frame is drawn after 8 s."""
    state, fetches = _held()
    for fetch in fetches[1:]:
        state, _ = step(state, _answer(fetch=fetch))
    state, effects = step(state, Tick(now=HELD_AT + timedelta(seconds=5)))
    assert not _renders(effects=effects)
    _, effects = step(state, Tick(now=HELD_AT + timedelta(seconds=10)))
    assert len(_renders(effects=effects)) == 1


def _delayed(*, state: State, feed: str) -> FeedFetched:
    """A download delaying the first trip shown by six minutes."""
    first = state.board.departures[0]
    trip = TripRealtime(
        trip_id=first.trip_id,
        cancelled=False,
        updates=(
            StopUpdate(
                sequence=first.origin_seq,
                stop_id=None,
                arrival_delay=None,
                departure_delay=360,
                arrival_time=None,
                departure_time=None,
            ),
        ),
        start_date=None,
    )
    return FeedFetched(
        feed=feed,
        snapshot=RealtimeSnapshot(
            fetched_at=state.now, trips={first.trip_id: trip},
        ),
    )


def _view_now(*, state: State) -> ScreenView:
    """The view the state would draw now."""
    return current_view(state=state, stop_view=live_stop_view(state=state))


def test_late_response_waits_for_next_frame() -> None:
    """A response after the hold is stored but drawn by the next frame."""
    state, fetches = _held()
    state, _ = step(state, Tick(now=HELD_AT + timedelta(seconds=10)))
    late = next(
        fetch for fetch in fetches if fetch.kind == FeedKind.TRIPS
    )
    before = state.last_view
    state, effects = step(state, _delayed(state=state, feed=late.feed))
    assert not _renders(effects=effects)
    assert late.feed in state.snapshots
    assert state.last_view == before
    assert _view_now(state=state) != before
    state, effects = step(state, Tick(now=HELD_AT + timedelta(seconds=15)))
    [render] = _renders(effects=effects)
    assert render.view == state.last_view != before


def test_response_outside_cycle_not_drawn() -> None:
    """A response nothing waits on is stored without drawing."""
    state, fetches = _held()
    for fetch in fetches:
        state, _ = step(state, _answer(fetch=fetch))
    assert not state.cycle.waiting
    before = state.last_view
    state, effects = step(state, _delayed(state=state, feed=BUSES))
    assert not _renders(effects=effects)
    assert not _fetches(effects=effects)
    assert state.snapshots[BUSES].trips
    assert _view_now(state=state) != before


def test_knob_turn_not_held() -> None:
    """A knob turn during a held cycle draws at once."""
    state, _ = _held()
    state, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=1))
    assert len(_renders(effects=effects)) == 1
    assert not state.cycle.free


def test_settings_change_not_held() -> None:
    """A Settings edit during a held cycle draws at once."""
    state, _ = _held(on_map=False)
    state, effects = step(state, settings_posted(larger_text=True))
    assert len(_renders(effects=effects)) == 1
    assert not state.cycle.free


def test_sleep_screen_not_held() -> None:
    """Quiet hours sleeping on a cycle's boundary draw the sleep screen."""
    _, effects = step(evening(), Tick(now=quiet_at(23)))
    [render] = _renders(effects=effects)
    assert isinstance(render.view, SleepView)


def test_notice_not_held() -> None:
    """Shutting down during a held cycle shows its notice at once."""
    state, _ = _held()
    _, effects = step(state, PowerRequested(action=PowerAction.OFF))
    [render] = _renders(effects=effects)
    assert isinstance(render.view, NoticeView)


def estimating_board() -> State:
    """Three buses on the Stop view of a city that estimates bus times."""
    pin = replace(PIN, origin=replace(PIN.origin, point=CENTRE))
    state = begun(saved=seeded(pin=pin, city=ESTIMATING_CITY))
    return loaded(state=replace(state, city=ESTIMATING_CITY))


def asked_of(*, state: State) -> Estimating:
    """The estimating request a state's board should send."""
    return Estimating(
        feed='estimates/bus',
        trip_ids=frozenset(dep.trip_id for dep in state.board.departures),
        stale_after=stale_after(state=state),
    )


def test_estimates_feed_is_never_fetched() -> None:
    """The derived feed is asked through its positions feed."""
    state = estimating_board()
    _, effects = _ticked(state=state, seconds=5 * 60)
    fetches = _fetches(effects=effects)
    assert 'estimates/bus' not in {fetch.feed for fetch in fetches}
    assert FetchFeed(
        feed='positions/bus',
        kind=FeedKind.POSITIONS,
        estimating=asked_of(state=state),
    ) in fetches


def test_estimates_are_asked_not_waited_on() -> None:
    """The cycle waits for the positions, never for the derived feed."""
    state, _ = _ticked(state=estimating_board(), seconds=5 * 60)
    assert 'estimates/bus' in state.cycle.asked
    assert 'estimates/bus' not in state.cycle.waiting
    assert 'positions/bus' in state.cycle.waiting


def test_map_positions_also_estimate() -> None:
    """On the Map, the one positions fetch carries the estimates."""
    state, _ = step(estimating_board(), PRESS)
    _, effects = _ticked(state=state, seconds=5 * 60)
    positions = [
        fetch for fetch in _fetches(effects=effects)
        if fetch.feed == 'positions/bus'
    ]
    assert [fetch.estimating for fetch in positions] == [
        asked_of(state=state),
    ]


def test_departures_loading_on_the_map_ask_for_estimates_at_once() -> None:
    """Departures arriving mid-cycle on the Map ask the positions again."""
    pin = replace(PIN, origin=replace(PIN.origin, point=CENTRE))
    state = begun(saved=seeded(pin=pin, city=ESTIMATING_CITY))
    state, _ = step(replace(state, city=ESTIMATING_CITY), PRESS)
    started = state.cycle.started
    assert started is not None
    state = replace(state, now=START + timedelta(seconds=10))
    state, effects = step(
        state,
        DeparturesLoaded(
            pin=shown(state=state),
            departures=(departure(departs=START + timedelta(minutes=3)),),
            until=START + HORIZON + RELOAD_BEFORE,
        ),
    )
    assert state.cycle.started == started
    assert 'estimates/bus' in state.cycle.asked
    assert FetchFeed(
        feed='positions/bus',
        kind=FeedKind.POSITIONS,
        estimating=asked_of(state=state),
    ) in _fetches(effects=effects)


def test_estimates_poll_once_a_cycle() -> None:
    """Over an hour the Stop view asks positions as often as Sydney's trips."""
    counts = []
    for state, feed in (
        (estimating_board(), 'positions/bus'),
        (loaded(state=fresh(centre=CENTRE)), BUSES),
    ):
        sent = 0
        for seconds in range(0, 3600, 15):
            state, effects = _ticked(state=state, seconds=seconds)
            sent += sum(
                fetch.feed == feed for fetch in _fetches(effects=effects)
            )
        counts.append(sent)
    assert counts[0] == counts[1] > 0


def test_sydney_fetches_estimate_nothing() -> None:
    """No Sydney fetch carries an estimating request."""
    state, _ = step(loaded(state=fresh()), PRESS)
    _, effects = _ticked(state=state, seconds=5 * 60)
    assert all(
        fetch.estimating is None for fetch in _fetches(effects=effects)
    )


def test_an_estimating_board_shows_the_usual_banners() -> None:
    """No data yet, then nothing, then no live data since the fetch."""
    state = estimating_board()
    assert live_stop_view(state=state).banner == (
        'Timetable times · no live data yet'
    )
    state, _ = step(
        state,
        FeedFetched(
            feed='estimates/bus',
            snapshot=RealtimeSnapshot(fetched_at=START, trips={}),
        ),
    )
    assert live_stop_view(state=state).banner is None
    late = START + stale_after(state=state) + timedelta(minutes=1)
    state, _ = step(state, Tick(now=late))
    assert live_stop_view(state=state).banner == 'No live data since 8:00'


def test_estimates_day_of_polling_stays_inside_the_first_guard() -> None:
    """A weekday polls positions at most 1,170 times, never the derived feed."""
    city = replace(
        ESTIMATING_CITY,
        peak_windows=(
            Window(start=time(7), end=time(9, 30)),
            Window(start=time(16), end=time(18, 30)),
        ),
    )
    pin = replace(PIN, origin=replace(PIN.origin, point=CENTRE))
    state = begun(saved=seeded(pin=pin, city=city), now=MIDNIGHT)
    state = loaded(state=replace(state, city=city, now=MIDNIGHT))
    feeds: list[str] = []
    for seconds in range(0, 24 * 3600, 15):
        state, effects = step(
            state, Tick(now=MIDNIGHT + timedelta(seconds=seconds)),
        )
        feeds += [fetch.feed for fetch in _fetches(effects=effects)]
    positions = [feed for feed in feeds if feed == 'positions/bus']
    assert 'estimates/bus' not in feeds
    assert 600 <= len(positions) <= 1_170
