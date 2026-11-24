"""Tests for state: map views."""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Final

from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_sydney.city import SYDNEY, SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import START, snapshot, vehicle
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import answer_fetches
from paperroute_transit.core.cycle import needed_feeds
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.map_requests import map_feeds
from paperroute_transit.core.map_state import area_box
from paperroute_transit.core.map_view import MapView
from paperroute_transit.core.messages import (
    FetchFeed,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    MapRequest,
    ModesLoaded,
    PatternsLoaded,
    PositionsFetched,
    ShapesLoaded,
)
from paperroute_transit.core.saved import LIVE_VIEWS
from paperroute_transit.core.state_model import (
    FEED_IDLE,
    POLL_EVERY,
    RETRY_AFTER,
)
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.core.stop_view import SIDEBAR_ROWS, StopView
from paperroute_transit.core.tiles import ZOOMS
from tests.core.state.helpers import (
    BUS_POSITIONS,
    BUSES,
    CENTRE,
    FERRY_POSITIONS,
    PRESS,
    effects_of,
    fresh,
    left_for_stop,
    loaded,
    map_zoom,
    trip_fetches,
    turned,
)

ALL_SOURCES: Final[frozenset[str]] = SYDNEY.feeds.position_sources()


def positions_of(*, sources: Iterable[str]) -> tuple[str, ...]:
    """
    List Sydney's positions feeds of some feed sources, anywhere.

    Parameters
    ----------
    sources : Iterable[str]
        Feed sources.

    Returns
    -------
    tuple[str, ...]
        Feed paths, sorted.
    """
    return SYDNEY.feeds.position_feeds(sources=sources, box=None)


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
        for feed in positions_of(sources=ALL_SOURCES)
    ]


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
        mpp=8, box=area_box(
            centre=CENTRE,
            mpp=8,
        ), modes=frozenset({SydneyFeed.BUS})
    )
    state, effects = step(state, event)
    assert not effects_of(effects=effects, kind=FetchFeed)
    assert map_feeds(state=state) == (BUS_POSITIONS,)
    assert state.map.area_modes == {8: frozenset({SydneyFeed.BUS})}
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


def test_stop_view_polls_trips_and_its_heros_positions() -> None:
    """With a Destination, the Stop view fetches trips and positions."""
    state = loaded(state=fresh(centre=CENTRE), kind=SydneyMode.BUS)
    for seconds in (30, 60, 90):
        now = START + timedelta(seconds=seconds)
        state, effects = step(state, Tick(now=now))
        fetched = effects_of(effects=effects, kind=FetchFeed)
        assert [
            each for each in fetched if each.kind != FeedKind.ALERTS
        ] == [
            FetchFeed(feed=BUSES),
            FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS),
        ]
    assert needed_feeds(
        state=state, stop_view=live_stop_view(state=state),
    ) == (BUSES, BUS_POSITIONS)


def test_map_polls_positions_for_a_ferry_pin() -> None:
    """A ferry pin has no trip feed, yet the Map polls ferry positions."""
    pin = loaded(state=fresh(centre=CENTRE), mode=SydneyFeed.FERRY)
    state, _ = step(pin, PRESS)
    assert state.feeds == ()
    ferry = ModesLoaded(
        mpp=8,
        box=area_box(centre=CENTRE, mpp=8),
        modes=frozenset({SydneyFeed.FERRY}),
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
    assert needed_feeds(
        state=state, stop_view=live_stop_view(state=state),
    ) == (
        BUSES,
        *SYDNEY.feeds.position_feeds(
            sources=ALL_SOURCES, box=box,
        ),
    )
    ferry = ModesLoaded(mpp=8, box=box, modes=frozenset({SydneyFeed.FERRY}))
    state, _ = step(state, ferry)
    assert needed_feeds(
        state=state, stop_view=live_stop_view(state=state),
    ) == (BUSES, FERRY_POSITIONS)


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
    assert state.map.pending_patterns == {'trip-0803'}
    state, effects = step(state, PRESS)
    load = LoadPatterns(trip_ids=trips - {'trip-0803'}, day=START.date())
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
    trips = load.trip_ids | {'trip-0803'}
    event = PatternsLoaded(trip_ids=trips, patterns={}, shapes={})
    state, _ = step(state, event)
    assert not state.map.pending_patterns
    assert state.map.patterns == dict.fromkeys(trips)
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
    state, effects = step(loaded(state=fresh(), mode=SydneyFeed.FERRY), PRESS)
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
        modes=frozenset({SydneyFeed.BUS}),
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
    assert [x.shape_id for x in state.last_view.base.lines] == ['b:0']
    assert [marker.live for marker in state.last_view.markers] == [True]
