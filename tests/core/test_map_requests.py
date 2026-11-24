"""Tests for the positions feeds the Map and Strip poll."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final

from paperroute_device.core.kit_events import Tick
from paperroute_hardware.core.knob_events import Knob, KnobPressed
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyFeed, SydneyMode
from paperroute_testkit.core.connection_factories import LEG1, LEG2
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    pattern,
    place,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import (
    CENTRE,
    PIN,
    on_view,
    trains,
    turn,
)
from paperroute_transit.core.cycle import cycle_effects, needed_feeds
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import Point, make_shape
from paperroute_transit.core.map_fit import BANNER_ROOM, CREDIT_ROOM, FrameKey
from paperroute_transit.core.map_lines import first_per_line
from paperroute_transit.core.map_requests import map_feeds, map_inputs, pruned
from paperroute_transit.core.map_state import area_box, area_frame
from paperroute_transit.core.map_view import DEFAULT_ZOOM, MAP_WIDTH
from paperroute_transit.core.messages import (
    Effect,
    FetchFeed,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    ModesLoaded,
    PatternsLoaded,
    SettingsChanged,
)
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.saved import LiveView, MapFraming
from paperroute_transit.core.state_model import Cycle, State
from paperroute_transit.core.state_views import live_stop_view, live_view
from paperroute_transit.core.strip_model import StripMapView
from paperroute_transit.core.strip_state import strip_window
from paperroute_transit.core.tiles import ZOOMS
from tests.core.test_connection_screens import legs_loaded, started

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



def bus_pin() -> tuple[Departure, ...]:
    """
    Make one bus departure.

    Returns
    -------
    tuple[Departure, ...]
        The departure.
    """
    return (
        departure(departs=START + timedelta(minutes=3), mode=SydneyFeed.BUS),
    )


def on_map() -> State:
    """
    Start on the Map with a bus pin and no area modes.

    Returns
    -------
    State
        The state.
    """
    state, _ = on_view(view=LiveView.MAP, departures=bus_pin())
    return state


def area(*, state: State, centre: Point = CENTRE) -> LoadModes:
    """
    Ask for the modes in the area at the state's zoom.

    Parameters
    ----------
    state : State
        State on the Map.
    centre : Point
        Map centre.

    Returns
    -------
    LoadModes
        The request.
    """
    mpp = ZOOMS[area_frame(map_state=state.map)[1]]
    return LoadModes(mpp=mpp, box=area_box(centre=centre, mpp=mpp))


def every_feed_near() -> tuple[str, ...]:
    """
    List every position feed but the light rail lines far from `CENTRE`.

    `CENTRE` is near Parramatta.

    Returns
    -------
    tuple[str, ...]
        Feed paths.
    """
    far = {
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v1/gtfs/vehiclepos/lightrail/newcastle',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
    }
    return tuple(
        feed
        for feed in positions_of(sources=ALL_SOURCES)
        if feed not in far
    )


def test_map_polls_all_feeds_before_modes_load() -> None:
    """Until the area's modes are known, every nearby feed is polled."""
    feeds = map_feeds(state=on_map())
    assert feeds == every_feed_near()
    assert len(feeds) == 6


def test_map_polls_all_feeds_after_modes_fail() -> None:
    """A failed modes load leaves every nearby feed polled."""
    state = on_map()
    state, _ = step(state, MapDataFailed(request=area(state=state)))
    assert map_feeds(state=state) == every_feed_near()


def test_map_polls_all_feeds_for_empty_modes() -> None:
    """An area with no shapes polls every nearby feed."""
    state = on_map()
    request = area(state=state)
    event = ModesLoaded(
        mpp=request.mpp, box=request.box, modes=frozenset()
    )
    state, _ = step(state, event)
    assert map_feeds(state=state) == every_feed_near()


def test_map_polls_only_the_light_rail_line_in_its_area() -> None:
    """Light rail in the area polls that line's feed, not all four."""
    state = on_map()
    request = area(state=state)
    modes = frozenset({SydneyFeed.LIGHT_RAIL})
    state, _ = step(
        state, ModesLoaded(mpp=request.mpp, box=request.box, modes=modes),
    )
    assert map_feeds(state=state) == (
        'v1/gtfs/vehiclepos/lightrail/parramatta',
    )


def test_map_polls_area_modes_when_known() -> None:
    """Known area modes poll exactly their feeds."""
    state = on_map()
    request = area(state=state)
    modes = frozenset({SydneyFeed.TRAIN, SydneyFeed.BUS})
    event = ModesLoaded(mpp=request.mpp, box=request.box, modes=modes)
    state, _ = step(state, event)
    assert map_feeds(state=state) == positions_of(sources=modes)
    assert len(map_feeds(state=state)) == 2


def test_strip_feeds_unchanged() -> None:
    """The Strip polls only its departures' modes."""
    state, _ = on_view(
        view=LiveView.STRIP, departures=(*bus_pin(), *trains()),
    )
    assert map_feeds(state=state) == positions_of(sources={SydneyFeed.METRO})


def test_positions_follow_rate() -> None:
    """Off-peak, the Map's positions are due on two-minute boundaries."""
    noon = datetime(2026, 10, 16, 12, 0, tzinfo=SYDNEY_ZONE)
    state = on_map()
    cycle = Cycle(started=noon, asked=frozenset(map_feeds(state=state)))
    for later, due in ((60, False), (119, False), (120, True)):
        now = noon + timedelta(seconds=later)
        _, effects = cycle_effects(
            state=replace(state, now=now, cycle=cycle),
            stop_view=live_stop_view(state=state),
        )
        positions = [
            effect
            for effect in effects
            if isinstance(effect, FetchFeed)
            and effect.kind == FeedKind.POSITIONS
        ]
        assert bool(positions) is due


def test_connection_map_requests_both_shapes() -> None:
    """The connection Map asks for both legs' shapes and patterns."""
    first = tuple(replace(item, shape_id='s606') for item in LEG1)
    second = tuple(replace(item, shape_id='s665') for item in LEG2)
    _, effects = legs_loaded(
        state=started(view=LiveView.MAP), first=first, second=second,
    )
    assert LoadShapes(shape_ids=frozenset({'s606', 's665'})) in effects
    [load] = [item for item in effects if isinstance(item, LoadPatterns)]
    assert {'a0733', 'b0746'} <= load.trip_ids


def test_connection_positions_union() -> None:
    """The connection Map polls every leg's positions feed."""
    second = tuple(replace(item, source=SydneyFeed.TRAIN) for item in LEG2)
    state, _ = legs_loaded(state=started(view=LiveView.MAP), second=second)
    mpp = ZOOMS[0]  # the Changes fit
    state = replace(state, map=replace(
        state.map, area_modes={mpp: frozenset({SydneyFeed.FERRY})},
    ))
    assert set(map_feeds(state=state)) == set(positions_of(
        sources={SydneyFeed.BUS, SydneyFeed.TRAIN, SydneyFeed.FERRY},
    ))


def test_connection_legs_poll_their_light_rail_anywhere() -> None:
    """A light rail leg polls every light rail feed, near the map or not."""
    second = tuple(
        replace(item, source=SydneyFeed.LIGHT_RAIL)
        for item in LEG2
    )
    state, _ = legs_loaded(state=started(view=LiveView.MAP), second=second)
    mpp = ZOOMS[0]  # the Changes fit
    state = replace(state, map=replace(
        state.map, area_modes={mpp: frozenset({SydneyFeed.BUS})},
    ))
    assert set(map_feeds(state=state)) == set(positions_of(
        sources={SydneyFeed.BUS, SydneyFeed.LIGHT_RAIL},
    ))


def test_connection_modes_around_drawn_map() -> None:
    """The connection Map asks for the modes around the change it shows."""
    state, effects = legs_loaded(state=started(view=LiveView.MAP))
    mpp = ZOOMS[0]  # the Changes fit
    assert state.map.frame is not None
    centre, zoom = state.map.frame
    assert (centre[0], zoom) == (72.0, 0)
    assert LoadModes(mpp=mpp, box=area_box(centre=centre, mpp=mpp)) in effects
    state = replace(state, map=replace(
        state.map, area_modes={mpp: frozenset({SydneyFeed.FERRY})},
    ))
    assert set(map_feeds(state=state)) >= set(
        positions_of(sources={SydneyFeed.FERRY}),
    )
    strip, _ = step(state, KnobPressed(knob=Knob.BOTTOM))
    assert strip.view is LiveView.STRIP
    assert strip.map.frame is None


SOUTH_TO: Point = (CENTRE[0], CENTRE[1] - 1600.0)
EAST_TO: Point = (CENTRE[0] + 2300.0, CENTRE[1])


def fitted_on(
    *, view: LiveView = LiveView.MAP, to: Point = SOUTH_TO,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Show a bus pin with a To and load its next trip's pattern, which
    calls where the To is 3 minutes before the From.

    Parameters
    ----------
    view : LiveView
        The view shown.
    to : Point
        Where the To is.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after the pattern loads, and the effects of both steps.
    """
    pin = Pin(
        origin=place('C', name='Central', point=CENTRE),
        destination=place('S5', name='S5', point=to),
        kind=SydneyMode.BUS,
    )
    state, first = on_view(view=view, pin=pin, departures=bus_pin())
    stops = calls(
        stations='BA',
        start=EIGHT,
        step=180,
        points={'B': to, 'A': CENTRE},
    )
    state, then = step(
        state,
        PatternsLoaded(
            trip_ids=frozenset({'trip-0803'}),
            patterns={'trip-0803': pattern(trip_id='trip-0803', stops=stops)},
            shapes={},
        ),
    )
    return state, (*first, *then)


def test_frame_recorded_for_plain_pins() -> None:
    """A plain pin's fitted frame is recorded and its area asked about."""
    state, effects = fitted_on()
    assert live_stop_view(state=state).banner is not None
    centre = (
        CENTRE[0],
        CENTRE[1] - 800.0 + (BANNER_ROOM - CREDIT_ROOM) * 5 / 2,
    )
    assert state.map.frame == (centre, ZOOMS.index(5))
    assert LoadModes(mpp=5, box=area_box(centre=centre, mpp=5)) in effects


def test_strip_map_frame_uses_strip_width() -> None:
    """On Strip + map the frame is fitted at the strip map's width."""
    state, _ = fitted_on(view=LiveView.STRIP_MAP, to=EAST_TO)
    assert state.map.frame is not None
    assert state.map.frame[1] == ZOOMS.index(8)
    wide, _ = fitted_on(view=LiveView.MAP, to=EAST_TO)
    assert wide.map.frame is not None
    assert wide.map.frame[1] == ZOOMS.index(5)


def test_new_frame_drops_area_modes() -> None:
    """A new frame forgets the area modes asked for the old one."""
    state, _ = fitted_on()
    state = replace(state, map=replace(
        state.map,
        area_modes={
            5: frozenset({SydneyFeed.FERRY}),
            8: frozenset({SydneyFeed.BUS}),
        },
        pending_modes=frozenset({14}),
    ))
    state, effects = step(state, turn(steps=-1))
    assert state.map.frame is not None
    assert state.map.frame[1] == ZOOMS.index(8)
    assert state.map.area_modes == {}
    assert state.map.pending_modes == {8}
    [load] = [item for item in effects if isinstance(item, LoadModes)]
    assert load.mpp == 8



def test_coarser_detent_moves_after_the_fit_gets_finer() -> None:
    """Hidden finer detents are dropped when a finer fit clamps them."""
    state, _ = fitted_on(view=LiveView.STRIP_MAP, to=EAST_TO)
    state, _ = step(state, turn(steps=2))
    assert state.map.frame is not None
    assert state.map.frame[1] == 0
    state, _ = step(replace(state, view=LiveView.MAP), Tick(now=state.now))
    assert state.map.frame is not None
    assert state.map.frame[1] == 0
    state, _ = step(state, turn(steps=-1))
    assert state.map.frame is not None
    assert state.map.frame[1] == 1


ALONG: Final[Mapping[str, Point]] = MappingProxyType({
    'W': (CENTRE[0] - 1_600.0, CENTRE[1]),
    'V': (CENTRE[0] - 800.0, CENTRE[1]),
    'A': CENTRE,
    'X': (CENTRE[0] + 800.0, CENTRE[1]),
})


def buses_every_ten() -> tuple[State, tuple[Departure, ...]]:
    """
    Show a no-To bus pin on the Map with five 606s ten minutes apart.

    Returns
    -------
    tuple[State, tuple[Departure, ...]]
        State on the Map before any pattern loads, and the departures.
    """
    departures = tuple(
        departure(
            departs=START + timedelta(minutes=minutes), mode=SydneyFeed.BUS,
        )
        for minutes in (3, 13, 23, 33, 43)
    )
    pin = Pin(
        origin=place('C', name='Central', point=CENTRE),
        destination=None,
        kind=SydneyMode.BUS,
    )
    state, _ = on_view(view=LiveView.MAP, pin=pin, departures=departures)
    return state, departures


def along_patterns(
    *,
    departures: tuple[Departure, ...],
    stations: str = 'WVAX',
    line: Mapping[str, Point] = ALONG,
) -> PatternsLoaded:
    """
    Load patterns calling along a line, 3 minutes apart.

    Parameters
    ----------
    departures : tuple[Departure, ...]
        The departures; each reaches the From when it leaves.
    stations : str
        The calls, in order; A is the From.
    line : dict[str, Point]
        Where each call is.

    Returns
    -------
    PatternsLoaded
        Their patterns.
    """
    patterns: dict[str, TripPattern | None] = {
        item.trip_id: pattern(
            trip_id=item.trip_id,
            stops=calls(
                stations=stations,
                start=(
                    EIGHT
                    + int((item.departs - START).total_seconds())
                    - 180 * stations.index('A')
                ),
                step=180,
                points=line,
            ),
        )
        for item in departures
    }
    return PatternsLoaded(
        trip_ids=frozenset(patterns), patterns=patterns, shapes={},
    )


def test_plain_map_refits_once_patterns_load() -> None:
    """The From-only frame gives way to the stretch when patterns arrive."""
    state, departures = buses_every_ten()
    assert state.map.frame == (CENTRE, DEFAULT_ZOOM)
    state, _ = step(state, along_patterns(departures=departures))
    assert state.map.frame is not None
    (x, _), zoom = state.map.frame
    assert (x, zoom) == (CENTRE[0] - 400.0, ZOOMS.index(8))


def test_plain_map_frame_is_the_union_as_the_next_bus_leaves() -> None:
    """The 8:03 leaving the sidebar leaves the union of stretches alone."""
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures))
    before = state.map.frame
    first = live_stop_view(state=state).head_departures[0]
    state, _ = step(state, Tick(now=START + timedelta(minutes=4)))
    assert live_stop_view(state=state).head_departures[0] != first
    assert state.map.frame == before


def test_centred_setting_centres_the_map() -> None:
    """With Centred, the From is the frame's centre at 8 m/px."""
    state, departures = buses_every_ten()
    state, _ = step(state, SettingsChanged(map_framing=MapFraming.CENTRED))
    state, _ = step(state, along_patterns(departures=departures))
    assert state.map.frame == (CENTRE, DEFAULT_ZOOM)


def test_framing_change_refits_at_once() -> None:
    """Switching the setting on the Map redraws in the new rule, both ways."""
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures))
    approach = state.map.frame
    assert approach is not None and approach[0] != CENTRE
    state, _ = step(state, SettingsChanged(map_framing=MapFraming.CENTRED))
    assert state.map.frame == (CENTRE, DEFAULT_ZOOM)
    state, _ = step(state, SettingsChanged(map_framing=MapFraming.APPROACH))
    assert state.map.frame == approach


def test_plain_map_without_a_location_asks_for_no_patterns() -> None:
    """A From with no point draws no map, so no pattern is asked for."""
    pin = Pin(
        origin=place('C', name='Central'),
        destination=None,
        kind=SydneyMode.BUS,
    )
    _, effects = on_view(view=LiveView.MAP, pin=pin, departures=bus_pin())
    assert not [item for item in effects if isinstance(item, LoadPatterns)]


FURTHER: Final[dict[str, Point]] = {
    **ALONG,
    'X': (CENTRE[0] + 1_400.0, CENTRE[1]),
}


def test_plain_map_settles_on_its_first_pattern_then_holds() -> None:
    """The first pattern refits and keys the frame; a later one keeps it."""
    state, departures = buses_every_ten()
    assert state.map.frame_key is None
    state, _ = step(state, along_patterns(departures=departures[:1]))
    settled = state.map.frame
    assert settled == ((CENTRE[0] - 400.0, CENTRE[1] + 24.0), 2)
    assert state.map.frame_key == FrameKey(
        ends=(CENTRE, None), step=0, width=MAP_WIDTH, asleep=False,
    )
    state, _ = step(
        state, along_patterns(departures=departures[1:], line=FURTHER),
    )
    assert state.map.frame == settled
    unheld = replace(state, map=replace(state.map, frame_key=None))
    fit = map_inputs(state=unheld, stop_view=live_stop_view(state=unheld))
    assert fit.centre is not None and fit.centre[0] == CENTRE[0] - 100.0


def test_knob_moves_one_level_from_a_held_coarser_frame() -> None:
    """Held at 8 m/px where 5 would fit, one detent goes to 5 or 14."""
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures[:1]))
    state, _ = step(
        state, along_patterns(departures=departures[1:], stations='VAX'),
    )
    state, _ = step(state, Tick(now=START + timedelta(minutes=4)))
    assert state.map.frame == ((CENTRE[0] - 400.0, CENTRE[1] + 24.0), 2)
    finer, _ = step(state, turn(steps=1))
    coarser, _ = step(state, turn(steps=-1))
    assert [
        None if moved.map.frame is None else ZOOMS[moved.map.frame[1]]
        for moved in (finer, coarser)
    ] == [5, 14]


OPEN: Final[Pin] = Pin(
    origin=place('2145615', name='Hollis St'), kind=SydneyMode.BUS,
)


def buses() -> tuple[Departure, ...]:
    """
    Make three Sydney buses, 3, 6 and 9 minutes after START.

    Returns
    -------
    tuple[Departure, ...]
        The departures.
    """
    return tuple(
        departure(
            departs=START + timedelta(minutes=minutes), mode=SydneyFeed.BUS,
        )
        for minutes in (3, 6, 9)
    )


def test_the_stop_view_asks_for_its_heros_pattern() -> None:
    """A pin with no Destination asks for the next departure's pattern."""
    _, effects = on_view(
        view=LiveView.STOP, pin=OPEN, departures=buses(),
    )
    assert LoadPatterns(
        trip_ids=frozenset({'trip-0803'}), day=START.date(),
    ) in effects


def test_the_stop_view_drops_patterns_of_trips_it_no_longer_holds() -> None:
    """A Tick drops a pattern no departure and no hero needs."""
    state, _ = on_view(view=LiveView.STOP, pin=OPEN, departures=buses())
    held = {
        trip_id: pattern(trip_id=trip_id)
        for trip_id in ('gone', 'trip-0803')
    }
    state = replace(state, map=replace(state.map, patterns=held))
    state, _ = step(state, Tick(now=state.now + timedelta(seconds=1)))
    assert set(state.map.patterns) == {'trip-0803'}


def held_state() -> State:
    """
    Make a state holding one pattern and the shape it follows.

    Returns
    -------
    State
        State whose Map holds pattern ``keep`` and shape ``s``.
    """
    state, _ = on_view(view=LiveView.STOP)
    shape = make_shape(shape_id='s', points=[(0.0, 0.0), (1.0, 0.0)])
    return replace(
        state,
        map=replace(
            state.map,
            patterns={'keep': pattern(trip_id='keep', shape_id='s')},
            shapes={'s': shape},
        ),
    )


def test_pruning_nothing_keeps_the_same_mappings() -> None:
    """With nothing to drop, the pattern and shape mappings are the same."""
    state = held_state()
    result = pruned(state=state, wanted=frozenset({'keep'}), departures=())
    assert result.map.patterns is state.map.patterns
    assert result.map.shapes is state.map.shapes


def test_pruning_drops_what_is_not_wanted() -> None:
    """A pattern not wanted goes, with the shape only it used."""
    state = held_state()
    result = pruned(state=state, wanted=frozenset(), departures=())
    assert not result.map.patterns
    assert not result.map.shapes


def with_extra_shape(*, state: State) -> State:
    """
    Add a shape no pattern uses, and a request for it, to a held state.

    Parameters
    ----------
    state : State
        State from ``held_state``.

    Returns
    -------
    State
        State whose Map also holds, and has asked for, shape ``x``.
    """
    shape = make_shape(shape_id='x', points=[(0.0, 0.0), (2.0, 0.0)])
    return replace(
        state,
        map=replace(
            state.map,
            shapes={**state.map.shapes, 'x': shape},
            requested_shapes=frozenset({'s', 'x'}),
        ),
    )


def test_pruning_a_pattern_keeps_the_shape_a_departure_follows() -> None:
    """A pattern not wanted goes while a shown departure keeps its shape."""
    state = with_extra_shape(state=held_state())
    shown = replace(departure(departs=START), shape_id='s')
    result = pruned(state=state, wanted=frozenset(), departures=(shown,))
    assert not result.map.patterns
    assert set(result.map.shapes) == {'s'}
    assert result.map.requested_shapes == {'s'}


def test_pruning_a_shape_keeps_the_wanted_patterns() -> None:
    """A shape nothing uses goes while the wanted pattern stays."""
    state = with_extra_shape(state=held_state())
    result = pruned(state=state, wanted=frozenset({'keep'}), departures=())
    assert result.map.patterns == state.map.patterns
    assert set(result.map.shapes) == {'s'}
    assert result.map.requested_shapes == {'s'}


def test_pruning_only_a_shape_request_trims_the_request() -> None:
    """A request for a shape no longer held changes the state alone."""
    state = held_state()
    state = replace(
        state,
        map=replace(
            state.map, requested_shapes=frozenset({'s', 'gone'}),
        ),
    )
    result = pruned(state=state, wanted=frozenset({'keep'}), departures=())
    assert result is not state
    assert result.map.requested_shapes == {'s'}
    assert result.map.patterns == state.map.patterns
    assert result.map.shapes == state.map.shapes


TO_TOWN: Final[Pin] = replace(OPEN, destination=place('200060'))


def test_a_destination_stop_view_asks_for_its_heros_pattern() -> None:
    """A Stop view with a Destination asks for its hero's pattern."""
    _, effects = on_view(
        view=LiveView.STOP, pin=TO_TOWN, departures=buses(),
    )
    assert LoadPatterns(
        trip_ids=frozenset({'trip-0803'}), day=START.date(),
    ) in effects


def test_a_destination_stop_view_drops_patterns_it_no_longer_needs(
) -> None:
    """Patterns other than the hero's are pruned."""
    state, _ = on_view(view=LiveView.STOP, pin=TO_TOWN, departures=buses())
    held = {
        trip_id: pattern(trip_id=trip_id)
        for trip_id in ('gone', 'trip-0803')
    }
    state = replace(state, map=replace(state.map, patterns=held))
    state, _ = step(state, Tick(now=state.now + timedelta(seconds=1)))
    assert set(state.map.patterns) == {'trip-0803'}


BUSES: Final[str] = 'v1/gtfs/realtime/buses'
BUS_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/buses'
PARRAMATTA_LINE: Final[str] = 'v1/gtfs/vehiclepos/lightrail/parramatta'


def hero_of(*, mode: str, source: str) -> tuple[Departure, ...]:
    """
    Make three departures of one mode and feed source.

    Parameters
    ----------
    mode : str
        Mode id.
    source : str
        Feed source.

    Returns
    -------
    tuple[Departure, ...]
        The departures, 3, 6 and 9 minutes after START.
    """
    return tuple(
        departure(
            departs=START + timedelta(minutes=minutes),
            mode=source,
            kind=mode,
        )
        for minutes in (3, 6, 9)
    )


def position_fetches(*, effects: Iterable[Effect]) -> set[str]:
    """
    Pick the positions feeds fetched.

    Parameters
    ----------
    effects : Iterable[Effect]
        Effects.

    Returns
    -------
    set[str]
        Feed paths.
    """
    return {
        effect.feed
        for effect in effects
        if isinstance(effect, FetchFeed) and effect.kind is FeedKind.POSITIONS
    }


def stop_positions_after_a_tick(
    *, pin: Pin, departures: tuple[Departure, ...],
) -> tuple[State, set[str]]:
    """
    Open the Stop view and give the positions feeds fetched by a Tick.

    Parameters
    ----------
    pin : Pin
        The only pin.
    departures : tuple[Departure, ...]
        Departures loaded.

    Returns
    -------
    tuple[State, set[str]]
        The state after the Tick, and the positions feeds fetched
        through it.
    """
    state, first = on_view(view=LiveView.STOP, pin=pin, departures=departures)
    state, later = step(state, Tick(now=START + timedelta(seconds=30)))
    return state, position_fetches(effects=(*first, *later))


def test_the_stop_view_polls_its_heros_positions() -> None:
    """With no Destination, a bus hero's positions join the poll."""
    state, fetched = stop_positions_after_a_tick(
        pin=OPEN, departures=hero_of(mode='bus', source='bus'),
    )
    assert fetched == {BUS_POSITIONS}
    stop_view = live_stop_view(state=state)
    assert needed_feeds(state=state, stop_view=stop_view) == (
        BUSES, BUS_POSITIONS,
    )


def test_a_destination_stop_view_polls_its_heros_positions() -> None:
    """With a Destination, a bus hero's positions join the poll too."""
    _, fetched = stop_positions_after_a_tick(
        pin=TO_TOWN, departures=hero_of(mode='bus', source='bus'),
    )
    assert fetched == {BUS_POSITIONS}


def test_an_unflagged_mode_still_polls_positions_for_the_gauge() -> None:
    """The gauge polls the hero's source whatever the mode's own flag."""
    state, _ = on_view(
        view=LiveView.STOP,
        pin=OPEN,
        departures=hero_of(mode='bus', source='bus'),
    )
    modes = tuple(
        replace(spec, stop_positions=False) for spec in state.city.modes
    )
    state = replace(state, city=replace(state.city, modes=modes))
    state, effects = step(state, Tick(now=START + timedelta(seconds=30)))
    assert position_fetches(effects=effects) == {BUS_POSITIONS}
    stop_view = live_stop_view(state=state)
    assert needed_feeds(state=state, stop_view=stop_view) == (
        BUSES, BUS_POSITIONS,
    )


def test_a_coach_hero_polls_its_sources_positions() -> None:
    """A coach asks for its source's positions feed, if the source has one."""
    pin = replace(OPEN, kind=SydneyMode.COACH)
    _, regional = stop_positions_after_a_tick(
        pin=pin, departures=hero_of(mode='coach', source='regional'),
    )
    _, own = stop_positions_after_a_tick(
        pin=pin, departures=hero_of(mode='coach', source='coach'),
    )
    assert regional == {'v1/gtfs/vehiclepos/nswtrains'}
    assert not own


def test_a_light_rail_hero_polls_every_line_of_its_source() -> None:
    """Light rail asks for all its lines, not only the one near the From."""
    pin = Pin(
        origin=place('light', name='Westmead', point=CENTRE),
        kind=SydneyMode.LIGHT_RAIL,
    )
    _, fetched = stop_positions_after_a_tick(
        pin=pin, departures=hero_of(mode='light_rail', source='light_rail'),
    )
    assert fetched == set(positions_of(sources={'light_rail'}))
    assert PARRAMATTA_LINE in fetched


def test_stop_view_positions_count_as_calls() -> None:
    """Each positions fetch of the Stop view counts against the quota."""
    state, _ = on_view(
        view=LiveView.STOP,
        pin=OPEN,
        departures=hero_of(mode='bus', source='bus'),
    )
    before = state.quota.calls
    state, effects = step(state, Tick(now=START + timedelta(seconds=30)))
    sent = [effect for effect in effects if isinstance(effect, FetchFeed)]
    assert position_fetches(effects=sent) == {BUS_POSITIONS}
    assert state.quota.calls - before == len(sent)


def test_strip_map_lines_are_the_strips_trips() -> None:
    """On Strip + map with no To, the solid lines are the strip's trips."""
    state, _ = on_view(
        view=LiveView.STRIP_MAP,
        pin=replace(PIN, destination=None),
        departures=tuple(
            replace(found, route=route, shape_id=shape)
            for found, route, shape in zip(
                trains(count=4), ('T1', 'T2', 'T1', 'T3'), 'abac',
                strict=True,
            )
        ),
    )
    state = replace(
        state,
        map=replace(
            state.map,
            shapes={
                name: make_shape(
                    shape_id=name, points=[CENTRE, (CENTRE[0], 5000.0)],
                )
                for name in 'abc'
            },
        ),
    )
    window = strip_window(state=state)
    shown = live_view(state=state, stop_view=live_stop_view(state=state))
    assert isinstance(shown, StripMapView)
    solid = [
        line.shape_id
        for line in shown.map.base.lines
        if line.solid is not None
    ]
    assert solid == [
        found.shape_id for found in first_per_line(departures=window)
    ]
