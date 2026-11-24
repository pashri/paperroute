"""Golden stop scenes."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_cambridge.city import CAMBRIDGE
from paperroute_sydney.city import SydneyFeed, SydneyMode
from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    pattern,
    place,
    snapshot,
    vehicle,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import on_view
from paperroute_transit.core.device import on_settings
from paperroute_transit.core.geo import Point, make_shape
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    FeedFetched,
    PositionsFetched,
    SettingsChanged,
    StripLoaded,
)
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.places import shown
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RunTail,
    build_reach,
)
from paperroute_transit.core.realtime import (
    EVERY_STOP,
    RealtimeSnapshot,
    StopUpdate,
    TripRealtime,
)
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LiveView, StopSides, seeded
from paperroute_transit.core.state_model import RELOAD_BEFORE, State
from paperroute_transit.core.state_views import LOADING_WAIT
from paperroute_transit.core.stop_view import HORIZON
from paperroute_transit.core.strip_state import strip_key
from tests.core.state.helpers import BUS_POSITIONS, CENTRE, fresh, loaded
from tests.core.test_stop_hero import FROM, RUN, STAND
from tests.golden.scenes.base import (
    CAMBRIDGE_AT,
    CAMBRIDGE_TO_ELY,
    ELY_TRAINS,
    bus_departures,
)
from tests.golden.scenes.strips import measured, patterns_loaded


def stop() -> State:
    """
    Show three buses on the Stop view.

    Returns
    -------
    State
        State at `START`.
    """
    return loaded(state=fresh(centre=CENTRE))


def stop_larger_inverted() -> State:
    """
    Show the Stop view in larger text with inverted lists.

    Returns
    -------
    State
        State at `START`.
    """
    state = stop()
    device = replace(
        state.settings.device, larger_text=True, inverted_lists=True,
    )
    settings = replace(state.settings, device=device)
    return replace(state, settings=settings)


def stop_no_key() -> State:
    """
    Show the Stop view with no API key set.

    Returns
    -------
    State
        State at `START`.
    """
    state = stop()
    return replace(state, panel=replace(state.panel, missing_keys=('TfNSW',)))


def estimated(
    *, state: State, lates: Mapping[int, tuple[int, bool]],
) -> State:
    """
    Give some board departures estimates, by index.

    Parameters
    ----------
    state : State
        A state with departures loaded.
    lates : Mapping[int, tuple[int, bool]]
        Minutes late and whether uncertain, by departure index.

    Returns
    -------
    State
        The state after its feeds deliver the estimates, heard at START.
    """
    trips = {
        state.board.departures[index].trip_id: TripRealtime(
            trip_id=state.board.departures[index].trip_id,
            cancelled=False,
            updates=(StopUpdate(
                sequence=EVERY_STOP,
                stop_id=None,
                arrival_delay=None,
                departure_delay=60 * late,
                arrival_time=None,
                departure_time=None,
            ),),
            start_date=None,
            estimated=True,
            uncertain=uncertain,
            heard_at=START,
        )
        for index, (late, uncertain) in lates.items()
    }
    snap = RealtimeSnapshot(fetched_at=START, trips=trips)
    for feed in state.feeds:
        state, _ = step(state, FeedFetched(feed=feed, snapshot=snap))
    return state


def stop_trainlink() -> State:
    """
    Show three TrainLink trains on the Stop view.

    Returns
    -------
    State
        State at `START`.
    """
    state = stop()
    departures = tuple(
        replace(
            departure,
            source=SydneyFeed.REGIONAL,
            route='WST',
            facts=replace(departure.facts, kind=SydneyMode.TRAIN),
        )
        for departure in state.board.departures
    )
    board = replace(state.board, departures=departures)
    return replace(state, board=board)


def stop_cambridge_trains() -> State:
    """
    Show four invented Cambridge trains on the Stop view.

    Returns
    -------
    State
        State at `CAMBRIDGE_AT`.
    """
    state = initial_state(
        saved=seeded(pin=CAMBRIDGE_TO_ELY, city=CAMBRIDGE),
        now=CAMBRIDGE_AT,
        city=CAMBRIDGE,
    )
    refresh = replace(state.refresh, started_at=CAMBRIDGE_AT - LOADING_WAIT)
    state = replace(state, refresh=refresh)
    departures = tuple(
        departure(
            departs=CAMBRIDGE_AT + timedelta(minutes=after),
            ride=ride,
            route=badge,
            mode='train',
            kind='train',
            headsign=headsign,
            platform=platform,
        )
        for after, ride, badge, headsign, platform in ELY_TRAINS
    )
    state, _ = step(state, DeparturesLoaded(
        pin=shown(state=state),
        departures=departures,
        until=CAMBRIDGE_AT + HORIZON + RELOAD_BEFORE,
    ))
    return state


BOTH_PIN: Final[Pin] = Pin(
    origin=place(STAND, name='King St At Missenden Rd', point=CENTRE),
    opposite=place(
        'opp', name='King St Opp Missenden Rd',
        point=(CENTRE[0], CENTRE[1] + 37.0),
    ),
    kind=SydneyMode.BUS,
    towards=('Leichhardt', 'Newtown'),
)


HERE_WAY: Final[tuple[str, ...]] = (
    'H3', 'H2', 'H1', STAND, 'L1', 'L2', 'L3', 'L4', 'L5', 'L6',
)


ACROSS_WAY: Final[tuple[str, ...]] = (
    'M3', 'M2', 'M1', 'opp', 'N1', 'N2', 'N3', 'N4', 'N5', 'N6',
)


STREETS: Final[dict[str, str]] = {
    'H3': 'Enmore Rd',
    'H2': 'Stanmore Rd',
    'H1': 'Station St',
    'L1': 'Cardigan St',
    'L2': 'Australia St',
    'L3': 'Church St',
    'L4': 'Smith St',
    'L5': 'Pyrmont Bridge Rd',
    'L6': 'Parramatta Rd',
    'M3': 'Brown St',
    'M2': 'Alice St',
    'M1': 'Wilson St',
    'N1': 'Carillon Ave',
    'N2': 'Egan St',
    'N3': 'Hawken St',
    'N4': 'Lennox St',
    'N5': 'Erskineville Rd',
    'N6': 'Camperdown Rd',
}


STOP_SPACING: Final[float] = 250.0  # metres


FACING: Final[dict[str, str]] = {
    'H3': 'N3', 'H2': 'N2', 'H1': 'N1', 'M3': 'L3', 'M2': 'L2', 'M1': 'L1',
}  # the stop across the road from each stop before a kerb


def both_departures() -> tuple[Departure, ...]:
    """
    Make a bus 2, 5, 8 and 11 minutes out, the 2nd and 8th from across.

    Returns
    -------
    tuple[Departure, ...]
        The departures, in time order.
    """
    departures = []
    for minutes in (2, 5, 8, 11):
        across = minutes in (2, 8)
        bus = departure(
            departs=START + timedelta(minutes=minutes),
            mode=SydneyFeed.BUS,
            kind=SydneyMode.BUS,
            route='370',
            trip_id=f'trip-{minutes}',
            headsign='Newtown' if across else 'Leichhardt',
        )
        departures.append(
            replace(bus, facts=replace(bus.facts, across=across)),
        )
    return tuple(departures)


def way_points(*, way: tuple[str, ...], across: bool) -> dict[str, Point]:
    """
    Place a way's stops on a straight street, the From's kerb at index 3.

    Parameters
    ----------
    way : tuple[str, ...]
        Stop IDs in running order.
    across : bool
        Whether the street is the kerb across, running the other way.

    Returns
    -------
    dict[str, Point]
        Position in metres by stop ID.
    """
    sign = 1.0 if across else -1.0
    north = 37.0 if across else 0.0
    return {
        stop: (
            CENTRE[0] + sign * STOP_SPACING * (index - 3),
            CENTRE[1] + north,
        )
        for index, stop in enumerate(way)
    }


def both_sides_reach() -> Reach:
    """
    Lay out a street of stops each way past a both-sides From.

    Returns
    -------
    Reach
        The reach, the kerb across's stops above the From.
    """
    places = {
        stop: place(stop, name=name, point=point)
        for way, across in ((HERE_WAY, False), (ACROSS_WAY, True))
        for stop, point in way_points(way=way, across=across).items()
        if (name := STREETS.get(stop))
    }
    return build_reach(data=ReachInput(
        origin=BOTH_PIN.origin,
        tails=(RunTail(before=HERE_WAY[:3], after=HERE_WAY[4:], trips=40),),
        places=places,
        station_of={'opp': STAND},
        across_tails=(
            RunTail(before=ACROSS_WAY[:3], after=ACROSS_WAY[4:], trips=40),
        ),
        facing=FACING,
    ))


def both_sides_live(*, view: LiveView) -> State:
    """
    Show a both-sides pin on a Map or Strip view, a bus each way.

    Parameters
    ----------
    view : LiveView
        The Map, Strip or Strip + map.

    Returns
    -------
    State
        State at `START` with the reach, both ways' shapes and patterns,
        and a bus on each way.
    """
    state, _ = on_view(
        view=view, pin=BOTH_PIN, departures=both_departures(),
    )
    key = strip_key(pin=BOTH_PIN, end_points=end_point_modes(
        modes=city_under_test().modes,
    ))
    state, _ = step(state, StripLoaded(key=key, reach=both_sides_reach()))
    ways = {False: HERE_WAY, True: ACROSS_WAY}
    points = {
        across: way_points(way=way, across=across)
        for across, way in ways.items()
    }
    lines = {
        across: make_shape(
            shape_id=f'370-{across}',
            points=[points[across][stop] for stop in way],
        )
        for across, way in ways.items()
    }
    patterns: dict[str, TripPattern | None] = {}
    for item in both_departures():
        across = item.facts.across
        leaves = (
            int(item.departs.timestamp() - START.timestamp()) + EIGHT
        )
        patterns[item.trip_id] = measured(
            made=pattern(
                trip_id=item.trip_id,
                route='370',
                stops=calls(
                    stations=ways[across],
                    start=leaves - 3 * 90,
                    step=90,
                    points=points[across],
                ),
            ),
            line=lines[across],
        )
    state = patterns_loaded(
        state=state, patterns=patterns, shapes=list(lines.values()),
    )
    buses = tuple(
        vehicle(
            timestamp=int(START.timestamp()),
            trip_id=trip,
            label='370',
            point=points[across][stop],
        )
        for trip, across, stop in (
            ('trip-2', True, 'M1'), ('trip-5', False, 'H2'),
        )
    )
    state, _ = step(state, PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=START, vehicles=buses),
    ))
    return state


def both_sides_scene(*, sides: StopSides) -> State:
    """
    Show the Stop view of a both-sides pin, a bus each way.

    Parameters
    ----------
    sides : StopSides
        How the Stop view shows the two kerbs.

    Returns
    -------
    State
        State at `START`.
    """
    state, _ = on_view(
        view=LiveView.STOP, pin=BOTH_PIN, departures=both_departures(),
    )
    state, _ = on_settings(
        state=state, event=SettingsChanged(stop_sides=sides),
    )
    return state


def stop_no_destination() -> State:
    """
    Show the Stop view of a pin with no Destination and a seen bus.

    Returns
    -------
    State
        State at `START`.
    """
    state, _ = on_view(
        view=LiveView.STOP,
        pin=Pin(
            origin=place(STAND, name='Hollis St', point=CENTRE),
            kind=SydneyMode.BUS,
        ),
        departures=bus_departures(platform='B'),
        station=FROM,
    )
    state = estimated(state=state, lates={0: (2, False)})
    state = patterns_loaded(state=state, patterns={'trip-0803': RUN})
    report = vehicle(
        timestamp=int(START.timestamp()),
        trip_id='trip-0803',
        point=(-75.0, 0.0),
    )
    state, _ = step(state, PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=START, vehicles=(report,)),
    ))
    return state
