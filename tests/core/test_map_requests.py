"""Tests for the positions feeds the Map and Strip poll."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
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
    on_view,
    trains,
    turn,
)
from paperroute_transit.core.cycle import cycle_effects
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_fit import BANNER_ROOM, CREDIT_ROOM
from paperroute_transit.core.map_requests import map_feeds, pin_ends
from paperroute_transit.core.map_state import area_box, area_frame
from paperroute_transit.core.map_view import DEFAULT_ZOOM
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
from paperroute_transit.core.state_views import live_stop_view
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
    assert map_feeds(state=state) == positions_of(sources={SydneyFeed.TRAIN})


def test_positions_follow_rate() -> None:
    """Off-peak, the Map's positions are due on two-minute boundaries."""
    noon = datetime(2026, 10, 16, 12, 0, tzinfo=SYDNEY_ZONE)
    state = on_map()
    cycle = Cycle(started=noon, asked=frozenset(map_feeds(state=state)))
    for later, due in ((60, False), (119, False), (120, True)):
        now = noon + timedelta(seconds=later)
        _, effects = cycle_effects(state=replace(state, now=now, cycle=cycle))
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
    second = tuple(replace(item, source=SydneyFeed.LIGHT_RAIL) for item in LEG2)
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
        area_modes={5: frozenset({SydneyFeed.FERRY}), 8: frozenset({SydneyFeed.BUS})},
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


ALONG: Final[dict[str, Point]] = {
    'W': (CENTRE[0] - 1_600.0, CENTRE[1]),
    'V': (CENTRE[0] - 800.0, CENTRE[1]),
    'A': CENTRE,
    'X': (CENTRE[0] + 800.0, CENTRE[1]),
}


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
    *, departures: tuple[Departure, ...],
) -> PatternsLoaded:
    """
    Load patterns calling at W, V, the From and X, 3 minutes apart.

    Parameters
    ----------
    departures : tuple[Departure, ...]
        The departures; each reaches the From when it leaves.

    Returns
    -------
    PatternsLoaded
        Their patterns.
    """
    patterns: dict[str, TripPattern | None] = {
        item.trip_id: pattern(
            trip_id=item.trip_id,
            stops=calls(
                stations='WVAX',
                start=(
                    EIGHT
                    + int((item.departs - START).total_seconds())
                    - 360
                ),
                step=180,
                points=ALONG,
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


def test_plain_map_frame_holds_still_as_the_next_bus_leaves() -> None:
    """The 8:03 leaving the sidebar moves nothing."""
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
