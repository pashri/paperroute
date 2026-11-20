"""Tests for the reducer's Strip and Strip + map views."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Final

from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.text import clock_text
from paperroute_hardware.core.knob_events import Knob, KnobPressed, KnobTurned
from paperroute_sydney.city import SYDNEY, SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import (
    START,
    departure,
    line_pattern,
    place,
    snapshot,
    stop_view_of,
    vehicle,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import answer_fetches
from paperroute_testkit.core.strip_factories import (
    CENTRE,
    KEY,
    PIN,
    on_view,
    run,
    trains,
    turn,
)
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.map_state import area_frame
from paperroute_transit.core.map_view import DEFAULT_ZOOM, MapView
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Effect,
    FetchFeed,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    LoadStrip,
    PositionsFetched,
    StripFailed,
    StripLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import CarryOn, Departure, Pin
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    ReachRow,
    RowKind,
    StripKey,
    build_reach,
)
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LiveView, Saved, default_settings
from paperroute_transit.core.state_model import RETRY_AFTER, State
from paperroute_transit.core.stop_view import HORIZON
from paperroute_transit.core.strip_model import (
    LOADING,
    NEEDS_UPDATE,
    STRIP_ROWS,
    StripInputs,
    StripMapView,
    StripView,
)
from paperroute_transit.core.strip_state import (
    keys_in_use,
    request_strips,
    shown_reach,
    strip_inputs,
    strip_key,
    strip_patterns,
)
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun
from tests.core.chooser_walks import CHOSEN, CHOSEN_KEY, OPEN, REACH, TO_STAGE
from tests.core.test_connection_screens import started, with_legs

ALL_SOURCES: Final[frozenset[str]] = SYDNEY.feeds.position_sources()
ENDS: Final[frozenset[str]] = end_point_modes(modes=SYDNEY.modes)
PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
METRO_POSITIONS: Final[str] = 'v2/gtfs/vehiclepos/metro'
BUS_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/buses'


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


def test_strip_key_takes_the_pins_from_kind_and_routes() -> None:
    """The key is the From's ID, the kind and the routes."""
    pin = replace(PIN, kind=SydneyMode.BUS, routes=('606',))
    assert strip_key(pin=pin, end_points=ENDS) == StripKey(
        station='C', kind=SydneyMode.BUS, routes=('606',),
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


def inputs_for(*, pin: Pin) -> StripInputs:
    """
    Build the Strip inputs of a state showing one pin.

    Parameters
    ----------
    pin : Pin
        The shown pin.

    Returns
    -------
    StripInputs
        The inputs, with no departures.
    """
    state, _ = on_view(view=LiveView.STRIP, pin=pin, departures=())
    return strip_inputs(
        state=state,
        stop_view=stop_view_of(departures=(), now=START),
        departures=(),
        positions={},
    )


def test_strip_inputs_flag_a_train_pin() -> None:
    """A pin of an end-point kind sets `end_points`; a line pin does not."""
    train = replace(PIN, kind=SydneyMode.TRAIN)
    assert inputs_for(pin=train).end_points
    assert not inputs_for(pin=PIN).end_points


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
    assert area_frame(map_state=state.map)[1] == DEFAULT_ZOOM + 1
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
    """Each view keeps its own scroll; the long ride to S5 ends at FROM.

    The 'no live data yet' banner leaves eleven rows: S10 to S20.
    """
    state, _ = on_view(view=LiveView.STOP, departures=trains(count=20))
    state, _ = step(state, turn(steps=2))
    state, effects = run(state=state, events=[PRESS, PRESS])
    state, _ = answer_fetches(state=state, effects=effects)
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    assert first_name(state=state) == 'S10'
    state, _ = step(state, turn(steps=1))
    assert (state.scroll, state.strip.scroll) == (2, 1)
    assert first_name(state=state) == 'S11'
    state, _ = run(state=state, events=[PRESS, PRESS])
    assert state.view is LiveView.STOP
    assert state.scroll == 2


def test_strip_scroll_is_clamped() -> None:
    """The stored strip scroll stops where the view stops moving.

    The ride to S5 is longer than the strip, so it rests with FROM, S20,
    in the bottom slot: under the 'no live data yet' banner, eleven rows
    S10 to S20.
    """
    state, _ = on_view(view=LiveView.STRIP)
    state, _ = step(state, turn(steps=5))
    assert state.strip.scroll == 0
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    assert isinstance(state.last_view, StripView)
    assert state.last_view.banner is not None
    state, _ = step(state, turn(steps=100))
    assert state.strip.scroll == 40 - (STRIP_ROWS - 1) - 10
    state, effects = step(state, turn(steps=1))
    assert not effects
    state, _ = step(state, turn(steps=-100))
    assert state.strip.scroll == -10
    assert first_name(state=state) == 'S0'


def test_flip_resets_the_strip_scroll() -> None:
    """A new place starts its strip at the default rows."""
    other = replace(PIN, origin=place('D', name='D', point=CENTRE))
    saved = Saved(
        settings=default_settings(city=SYDNEY),
        pins=(PIN, other),
        current=0,
        unpinned=None,
        view=LiveView.STRIP,
    )
    state = initial_state(saved=saved, now=START, city=SYDNEY)
    state, _ = step(state, StripLoaded(key=KEY, reach=long_reach()))
    state, _ = step(state, turn(steps=4))
    assert state.strip.scroll == 4
    state, effects = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert state.strip.scroll == 0
    assert KEY not in state.strip.reaches
    assert kinds(effects=effects, kind=LoadStrip) == [
        LoadStrip(
            key=strip_key(pin=other, end_points=ENDS),
            origin=other.origin,
        ),
    ]


def test_strip_polls_positions_for_departure_modes() -> None:
    """The Strip polls only its own kind's positions, never area modes."""
    bus = departure(departs=START + timedelta(minutes=2), trip_id='bus')
    _, effects = on_view(view=LiveView.STRIP, departures=(bus, *trains()))
    fetches = kinds(effects=effects, kind=FetchFeed)
    assert FetchFeed(feed=METRO_POSITIONS, kind=FeedKind.POSITIONS) in fetches
    assert all(fetch.feed != BUS_POSITIONS for fetch in fetches)
    assert not kinds(effects=effects, kind=LoadModes)
    assert not kinds(effects=effects, kind=LoadShapes)


def test_strip_map_polls_every_position_feed_until_modes_known() -> None:
    """Strip + map polls all nearby feeds while area modes are unknown."""
    _, effects = on_view(view=LiveView.STRIP_MAP, departures=trains())
    fetches = kinds(effects=effects, kind=FetchFeed)
    assert {fetch.feed for fetch in fetches} >= {
        *positions_of(sources=ALL_SOURCES - {SydneyFeed.LIGHT_RAIL}),
        'v1/gtfs/vehiclepos/lightrail/parramatta',
    }
    assert 'v1/gtfs/vehiclepos/lightrail/newcastle' not in {
        fetch.feed for fetch in fetches
    }
    assert kinds(effects=effects, kind=LoadModes)


def test_strip_trips_patterns_requested() -> None:
    """The Strip asks for the patterns of its trips in the window."""
    late = departure(
        departs=START + timedelta(minutes=45),
        mode=SydneyFeed.METRO,
        trip_id='late',
        kind=SydneyMode.METRO,
    )
    _, effects = on_view(view=LiveView.STRIP, departures=(*trains(), late))
    [load] = kinds(effects=effects, kind=LoadPatterns)
    assert load.trip_ids == {'train-0', 'train-1', 'train-2'}


def test_strip_trips_include_next_trips() -> None:
    """The Strip asks for the pattern of the trip a train carries on as."""
    t8 = departure(
        departs=START + timedelta(minutes=3),
        mode=SydneyFeed.METRO,
        route='T8',
        trip_id='a',
        kind=SydneyMode.METRO,
        then=CarryOn(route='T2', headsign='Leppington', trip_id='b'),
    )
    _, effects = on_view(view=LiveView.STRIP, departures=(*trains(), t8))
    [load] = kinds(effects=effects, kind=LoadPatterns)
    assert {'a', 'b'} <= load.trip_ids


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
            mode=SydneyFeed.METRO,
            route='T1',
            trip_id=f'train-{i}',
            kind=SydneyMode.METRO,
        )
        for i in range(60)
    )
    buses = tuple(
        departure(
            departs=at + timedelta(seconds=15 * i),
            trip_id=f'bus-{i}',
            kind=SydneyMode.BUS,
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
        mode=SydneyFeed.METRO,
        trip_id='early',
        kind=SydneyMode.METRO,
    )
    state, _ = on_view(view=LiveView.STRIP, departures=(early,))
    seen = vehicle(timestamp=int(START.timestamp()), trip_id='train-0')
    state, _ = step(
        state,
        PositionsFetched(
            feed=METRO_POSITIONS,
            snapshot=snapshot(fetched_at=START, vehicles=(seen,)),
        ),
    )
    state, effects = step(state, Tick(now=START + RETRY_AFTER))
    fetch = FetchFeed(feed=METRO_POSITIONS, kind=FeedKind.POSITIONS)
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
    pin = replace(PIN, kind=SydneyMode.BUS)
    state, _ = on_view(view=LiveView.STOP, pin=pin)
    state, _ = run(state=state, events=list(TO_STAGE))
    assert keys_in_use(state=state) == {
        strip_key(pin=pin, end_points=ENDS),
        CHOSEN_KEY,
    }
    state, _ = step(state, StripLoaded(key=CHOSEN_KEY, reach=REACH))
    assert state.strip.reaches == {CHOSEN_KEY: REACH}
    state, _ = step(state, OPEN)
    assert keys_in_use(state=state) == {strip_key(pin=pin, end_points=ENDS)}
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


def test_connection_strip_requests_selected_pair() -> None:
    """The connection Strip wants the selected pair's patterns."""
    state = with_legs(state=started(view=LiveView.STRIP))
    state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=1))
    wanted = strip_patterns(state=state)
    assert {'a0748', 'b0801a'} <= wanted
    assert 'a0733' not in wanted


def test_connection_strip_no_load_strip() -> None:
    """Switching to the connection Strip builds no reach."""
    state = with_legs(state=started(view=LiveView.MAP))
    state, effects = step(state, KnobPressed(knob=Knob.BOTTOM))
    assert state.view is LiveView.STRIP
    assert not kinds(effects=effects, kind=LoadStrip)


TRAIN_KEY: Final[StripKey] = StripKey(
    station='C', kind=SydneyMode.TRAIN, routes=(),
)
NO_TRACK: Final[TrainNetwork] = TrainNetwork(hops={}, runs=())
TWO_WAYS: Final[TrainNetwork] = TrainNetwork(
    hops={},
    runs=(
        WeekdayRun(route='R', stations=('C', 'N1', 'N2'), trains=5),
        WeekdayRun(route='R', stations=('C', 'S1', 'S2'), trains=6),
    ),
)
TRAIN_PLACES: Final = {
    'C': place('C', name='C', point=(0.0, 0.0)),
    'N1': place('N1', name='N1', point=(0.0, 1000.0)),
    'S1': place('S1', name='S1', point=(0.0, -1000.0)),
}


def train_trip(
    *, trip_id: str, stations: tuple[str, ...], at: int,
) -> tuple[Departure, TripPattern]:
    """
    Make a train that leaves C a number of minutes after 8:00.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    stations : tuple[str, ...]
        The stations it calls at, C among them, three minutes apart.
    at : int
        Minutes after 8:00 that it leaves C.

    Returns
    -------
    tuple[Departure, TripPattern]
        The departure from C and the pattern.
    """
    return (
        departure(
            departs=START + timedelta(minutes=at),
            mode=SydneyFeed.TRAIN,
            route='T1',
            trip_id=trip_id,
            kind=SydneyMode.TRAIN,
        ),
        line_pattern(
            trip_id=trip_id,
            stations=stations,
            minutes=at - 3 * stations.index('C'),
        ),
    )


def train_state(
    *,
    trips: list[tuple[Departure, TripPattern]],
    destination: str | None = None,
    network: TrainNetwork | None = NO_TRACK,
    selected: tuple[str, date] | None = None,
) -> State:
    """
    Show a train Strip on a loaded reach, with some trains' patterns.

    Parameters
    ----------
    trips : list[tuple[Departure, TripPattern]]
        The trains, soonest first.
    destination : str | None
        The Destination station ID, or None.
    network : TrainNetwork | None
        The reach's hops and runs; None builds the reach with none.
    selected : tuple[str, date] | None
        The picked train.

    Returns
    -------
    State
        State on the Strip after one tick.
    """
    pin = replace(
        PIN,
        kind=SydneyMode.TRAIN,
        destination=(
            None
            if destination is None
            else place(destination, name=destination)
        ),
    )
    state, _ = on_view(
        view=LiveView.STRIP,
        pin=pin,
        departures=tuple(item for item, _ in trips),
    )
    reach = build_reach(data=ReachInput(
        origin=TRAIN_PLACES['C'],
        tails=(),
        places=TRAIN_PLACES,
        station_of={},
        network=network,
    ))
    state = replace(
        state,
        strip=replace(
            state.strip, reaches={TRAIN_KEY: reach}, selected=selected,
        ),
        map=replace(state.map, patterns={
            shaped.trip_id: shaped for _, shaped in trips
        }),
    )
    state, _ = step(state, Tick(now=START))
    return state


def shown_ids(*, state: State) -> list[str]:
    """
    List the shown reach's station IDs.

    Parameters
    ----------
    state : State
        State on the Strip.

    Returns
    -------
    list[str]
        Station IDs, top to bottom.
    """
    reach = shown_reach(state=state)
    assert reach is not None
    return [row.place.stop_id for row in reach.rows]


def view_rows(*, state: State) -> dict[str, str]:
    """
    Read each drawn row's time by station.

    Parameters
    ----------
    state : State
        State drawn on the Strip.

    Returns
    -------
    dict[str, str]
        Clock time by station ID; rows with no station are left out.
    """
    assert isinstance(state.last_view, StripView)
    return {
        row.station: row.time for row in state.last_view.rows if row.station
    }


def test_a_train_strip_follows_the_next_train_to_the_destination() -> None:
    """The rows are the train's earlier calls, the From and its route."""
    line = ('E3', 'E2', 'C', 'D', 'F', 'G')
    state = train_state(
        trips=[
            train_trip(trip_id='a', stations=line, at=5),
            train_trip(trip_id='b', stations=line, at=15),
        ],
        destination='F',
    )
    reach = shown_reach(state=state)
    assert reach is not None
    assert [row.kind for row in reach.rows] == [
        RowKind.EARLIER, RowKind.EARLIER, RowKind.ORIGIN,
        RowKind.MAIN, RowKind.MAIN, RowKind.MAIN,
    ]
    assert shown_ids(state=state) == list(line)
    assert reach.trip == 'a'
    assert isinstance(state.last_view, StripView)
    assert state.last_view.rows[0].kind is RowKind.DASHED
    assert state.last_view.rows[1].station == 'E2'


def test_a_picked_train_is_the_main_line() -> None:
    """The panel's picked train, not the next, is the one followed."""
    line = ('C', 'D', 'F')
    state = train_state(
        trips=[
            train_trip(trip_id='a', stations=line, at=5),
            train_trip(trip_id='b', stations=('C', 'X', 'F'), at=15),
        ],
        destination='F',
        selected=('b', START.date()),
    )
    assert shown_ids(state=state) == ['C', 'X', 'F']


def test_with_no_destination_a_train_runs_each_way() -> None:
    """One train reads upward and one downward from the From."""
    state = train_state(
        trips=[
            train_trip(trip_id='north', stations=('C', 'N1', 'N2'), at=5),
            train_trip(trip_id='south', stations=('C', 'S1', 'S2'), at=6),
        ],
        network=TWO_WAYS,
    )
    reach = shown_reach(state=state)
    assert reach is not None
    assert shown_ids(state=state) == ['N2', 'N1', 'C', 'S1', 'S2']
    assert (reach.trip, reach.above_trip) == ('south', 'north')
    times = view_rows(state=state)
    assert times['N1'] == clock_text(
        moment=START + timedelta(minutes=8), zone=SYDNEY.zone,
    )
    assert times['S1'] == clock_text(
        moment=START + timedelta(minutes=9), zone=SYDNEY.zone,
    )
    assert times['C'] == clock_text(
        moment=START + timedelta(minutes=6), zone=SYDNEY.zone,
    )


def test_train_rows_are_timed_but_not_the_passed_ones() -> None:
    """Earlier calls and calls have times; passed stations have none."""
    line = ('E3', 'E2', 'C', 'D', 'F')
    track = TrainNetwork(
        hops={('D', 'F'): Hop(passed=('P',), inside=True)}, runs=(),
    )
    state = train_state(
        trips=[train_trip(trip_id='a', stations=line, at=5)],
        destination='F',
        network=track,
    )
    assert shown_ids(state=state) == ['E3', 'E2', 'C', 'D', 'P', 'F']
    assert 'E3' not in view_rows(state=state)
    state, _ = step(state, turn(steps=-1))
    times = view_rows(state=state)
    assert times['E3'] == '7:59'
    assert times['E2'] == '8:02'
    assert times['D'] == '8:08'
    assert times['P'] == ''
    assert times['F'] == '8:11'
    assert isinstance(state.last_view, StripView)
    new = [
        row
        for row in state.last_view.rows
        if row.kind in (RowKind.PASSED, RowKind.EARLIER)
    ]
    assert new
    assert not any(row.bold or row.tag for row in new)


def test_a_train_strip_has_no_fold_rows() -> None:
    """A long run is listed in full, never closed into a fold."""
    line = ('C', *(f'L{i}' for i in range(30)))
    for destination in (None, 'L29'):
        state = train_state(
            trips=[train_trip(trip_id='a', stations=line, at=5)],
            destination=destination,
        )
        reach = shown_reach(state=state)
        assert reach is not None
        assert len(reach.rows) == 31
        assert RowKind.FOLD not in {row.kind for row in reach.rows}
        assert isinstance(state.last_view, StripView)
        assert RowKind.FOLD not in {row.kind for row in state.last_view.rows}


def test_an_old_file_builds_with_no_passed_or_exit_rows() -> None:
    """An empty or missing network still gives the train's own rows."""
    trip = train_trip(trip_id='a', stations=('C', 'D', 'F'), at=5)
    state = train_state(trips=[trip], network=NO_TRACK)
    reach = shown_reach(state=state)
    assert reach is not None
    assert [row.kind for row in reach.rows] == [
        RowKind.ORIGIN, RowKind.MAIN, RowKind.MAIN,
    ]
    bare = train_state(trips=[trip], network=None)
    assert shown_reach(state=bare) is not None
    assert isinstance(bare.last_view, StripView)


def test_a_train_strip_with_no_pattern_draws_the_from_alone() -> None:
    """With no loaded train the Strip shows the From and says nothing."""
    trip = train_trip(trip_id='a', stations=('C', 'D', 'F'), at=5)
    state = train_state(trips=[(trip[0], line_pattern(
        trip_id='other', stations=('C', 'D'), minutes=5,
    ))])
    assert shown_ids(state=state) == ['C']
    assert isinstance(state.last_view, StripView)
    assert state.last_view.empty is None
    assert [row.station for row in state.last_view.rows] == ['C']


def test_a_new_main_train_sends_the_scroll_home() -> None:
    """Scrolling holds while a train is followed; a new one resets it."""
    line = ('C', *(f'L{i}' for i in range(19)))
    state = train_state(
        trips=[
            train_trip(trip_id='a', stations=line, at=5),
            train_trip(trip_id='b', stations=line, at=15),
        ],
        destination='L18',
    )
    state, _ = step(state, turn(steps=3))
    assert state.strip.scroll == 3
    state, _ = step(state, Tick(now=START + timedelta(minutes=2)))
    assert state.strip.scroll == 3
    state, _ = step(state, Tick(now=START + timedelta(minutes=7)))
    reach = shown_reach(state=state)
    assert reach is not None and reach.trip == 'b'
    assert (state.strip.scroll, state.strip.asked) == (0, 0)


def test_a_train_strip_notes_where_the_train_runs_on() -> None:
    """The carry-on note sits on the followed train's last station."""
    ride, shaped = train_trip(
        trip_id='a', stations=('C', 'D', 'F'), at=5,
    )
    then = CarryOn(route='T2', headsign='Leppington', trip_id='b')
    ride = replace(ride, facts=replace(ride.facts, then=then))
    state = train_state(trips=[(ride, shaped)], destination='F')
    assert isinstance(state.last_view, StripView)
    noted = [row for row in state.last_view.rows if row.note is not None]
    assert [row.station for row in noted] == ['F']


def test_a_pick_replaces_only_its_own_side() -> None:
    """Picking a southbound train leaves the northbound rows alone."""
    trips = [
        train_trip(trip_id='north', stations=('C', 'N1', 'N2'), at=5),
        train_trip(trip_id='south', stations=('C', 'S1', 'S2'), at=6),
        train_trip(trip_id='later', stations=('C', 'S1', 'S3'), at=12),
    ]
    before = train_state(trips=trips, network=TWO_WAYS)
    after = train_state(
        trips=trips, network=TWO_WAYS, selected=('later', START.date()),
    )
    assert shown_ids(state=before) == ['N2', 'N1', 'C', 'S1', 'S2']
    assert shown_ids(state=after)[:4] == ['N2', 'N1', 'C', 'S1']
    assert 'S3' in shown_ids(state=after)
    picked = shown_reach(state=after)
    assert picked is not None
    assert (picked.trip, picked.above_trip) == ('later', 'north')
