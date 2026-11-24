"""Fixed states, one for each kind of screen, and how they are drawn."""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

import numpy as np
from numpy.typing import NDArray

from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_ZONE
from paperroute_device.core.banners import banner_line
from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import (
    Confirm,
    ConfirmAction,
    Menu,
    Origin,
    PanelScreen,
    PowerAction,
    WifiPhase,
    WifiSetup,
)
from paperroute_device.core.panel_screens import panel_view
from paperroute_device.runtime.outputs import drawing
from paperroute_device_testkit.device_pages import FACTS
from paperroute_sydney.city import SYDNEY, SydneyFeed, SydneyMode
from paperroute_testkit.city_setting import city_under_test, train_mode
from paperroute_testkit.core.connection_factories import (
    LEG1,
    LEG2,
    PARRAMATTA,
    TWO_WAY,
    WORK,
    at,
)
from paperroute_testkit.core.factories import (
    EAST,
    EIGHT,
    POINTS,
    START,
    calls,
    departure,
    example_reach,
    line_pattern,
    pattern,
    place,
    snapshot,
    train,
    vehicle,
)
from paperroute_testkit.core.kit_steps import QUIET_PARTS, device_of, step
from paperroute_testkit.core.steps import answer_fetches
from paperroute_testkit.core.strip_factories import (
    KEY,
    PIN,
    SOUTH,
    TRAIN_TRIP,
    WEST,
    on_view,
    train_departure,
    train_pattern,
    train_reach_example,
)
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.core.chains import Legs, leg_key
from paperroute_transit.core.chooser_lists import turned
from paperroute_transit.core.connection_model import way_legs
from paperroute_transit.core.device import on_settings
from paperroute_transit.core.geo import Point, Shape, make_shape
from paperroute_transit.core.messages import (
    DayLinesLoaded,
    DeparturesLoaded,
    FeedFetched,
    FetchFeed,
    PatternsLoaded,
    PositionsFetched,
    ScreenView,
    SettingsChanged,
    ShapesLoaded,
    StripLoaded,
)
from paperroute_transit.core.model import Departure, Pin, StopPlace
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.panel_screens import TilesFacts
from paperroute_transit.core.patterns import TripPattern, measure
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
from paperroute_transit.core.saved import (
    LiveView,
    MapFraming,
    StopSides,
    seeded,
)
from paperroute_transit.core.sleep import sleep_view
from paperroute_transit.core.state_model import RELOAD_BEFORE, State
from paperroute_transit.core.state_views import (
    LOADING_WAIT,
    banner_live_view,
    current_view,
)
from paperroute_transit.core.stop_view import HORIZON
from paperroute_transit.core.strip_state import strip_key
from paperroute_transit.core.tiles import (
    TILE,
    LabelKind,
    StreetLabel,
    TileVariant,
)
from paperroute_transit.transit_app import TransitApp
from tests.core.chains.helpers import TRAINS
from tests.core.chooser_walks import BUSY_PAIR, at_kerbs, at_routes, at_towards
from tests.core.state.helpers import (
    BUS_POSITIONS,
    CENTRE,
    PRESS,
    fresh,
    loaded,
)
from tests.core.test_alerts_view import on_alerts
from tests.core.test_connection import late_legs
from tests.core.test_connection_screens import started, with_legs
from tests.core.test_connection_strip import PATTERNS as LEG_PATTERNS
from tests.core.test_stop_hero import FROM, RUN, STAND
from tests.core.test_stop_hero import stop as call_at

GOLDEN: Final[Path] = Path(__file__).parent
UPDATING: Final[bool] = os.getenv('GOLDEN_UPDATE') == '1'
TWO_CHANGES: Final[Pin] = replace(
    WORK, destination=PARRAMATTA, ways=(TWO_WAY,),
)
ROUTE: Final[list[tuple[float, float]]] = [
    CENTRE,
    (-16_200.0, 1_000.0),
    (-15_600.0, 300.0),
    (-15_000.0, -400.0),
]
APPROACH: Final[dict[str, tuple[float, float]]] = {
    'W2': (-18_200.0, 1_600.0),
    'W1': (-17_800.0, 1_400.0),
    'W0': (-17_400.0, 1_200.0),
    'C': CENTRE,
    'E1': ROUTE[1],
    'E2': ROUTE[2],
}
LABEL: Final[str] = 'Golden St'
DAY_STOPS: Final[dict[str, tuple[float, float]]] = {
    'N2': (-17_000.0, 2_000.0),
    'N1': (-17_000.0, 1_600.0),
    'C': CENTRE,
    'S1': (-17_000.0, 500.0),
    'S2': (-17_000.0, 100.0),
}
NORTH_EAST: Final[dict[str, tuple[float, float]]] = {
    'C': CENTRE,
    'B1': (-16_600.0, 1_500.0),
    'B2': (-16_100.0, 2_000.0),
}
STRIP_TRIPS: Final[dict[str, tuple[tuple[str, ...], int]]] = {
    'e0': (EAST, -2),
    'e': (EAST, 0),
    'w': (WEST, 3),
    'r': (SOUTH, 4),
}


def baseline_bytes(*, path: Path, data: bytes) -> bytes:
    """
    Read a baseline file, first writing it when updating baselines.

    Parameters
    ----------
    path : Path
        The baseline file.
    data : bytes
        What the code gives today.

    Returns
    -------
    bytes
        The file's contents; `data` itself when updating.
    """
    if UPDATING:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path.read_bytes()


class GoldenTiles:
    """Tiles drawn from their own address, with one street name."""

    def tile(
        self, *, zoom: int, col: int, row: int, variant: TileVariant
    ) -> NDArray[np.uint8]:
        """
        Draw one tile from its zoom, column and row.

        Parameters
        ----------
        zoom : int
            Metres per pixel.
        col : int
            Tile column.
        row : int
            Tile row.
        variant : TileVariant
            Ignored.

        Returns
        -------
        NDArray[np.uint8]
            White or light grey by the tile's parity, with a dark line
            whose place follows the address.
        """
        del variant
        pixels = np.full((TILE, TILE), (255, 170)[(col + row) % 2], np.uint8)
        pixels[(zoom + col * 7 + row * 13) % TILE, :] = 85
        pixels[:, (zoom * 3 + col * 11 + row) % TILE] = 85
        return pixels

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Give one street name across the middle of the box.

        Parameters
        ----------
        zoom : int
            Ignored.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in global pixels.
        kind : LabelKind
            Which labels; only street names are given.

        Returns
        -------
        list[StreetLabel]
            One label for street names; none for other kinds.
        """
        del zoom
        if kind is not LabelKind.STREET:
            return []
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return [StreetLabel(
            name=LABEL, x=x + 60.0, y=y, angle=0.0, width=80.0,
            start=(x, y), end=(x + 120.0, y),
        )]

    def version(self) -> str:
        """
        Say which build the tiles are.

        Returns
        -------
        str
            Always 'golden'.
        """
        return 'golden'


APPS: Final[dict[str, TransitApp]] = {
    city.name: TransitApp(city=city, parts=QUIET_PARTS)
    for city in (SYDNEY, CAMBRIDGE)
}
CAMBRIDGE_AT: Final[datetime] = datetime(
    2026, 11, 16, 7, 0, tzinfo=CAMBRIDGE_ZONE,
)
CAMBRIDGE_TO_ELY: Final[Pin] = Pin(
    origin=StopPlace(stop_id='rail:CBG', name='Cambridge', point=None),
    destination=StopPlace(stop_id='rail:ELY', name='Ely', point=None),
    kind='train',
)
# Minutes after CAMBRIDGE_AT, minutes to Ely, badge, headsign, platform.
ELY_TRAINS: Final[tuple[tuple[int, int, str, str, str], ...]] = (
    (6, 16, 'XC', 'Birmingham New Street', '4'),
    (21, 17, 'GN', 'Kings Lynn', '4'),
    (33, 18, 'GA', 'Norwich', '5'),
    (51, 17, 'GN', 'Kings Lynn', '1'),
)


def view_of(*, state: State) -> ScreenView:
    """
    Build the view a state shows.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    ScreenView
        The power notice while a power action is under way, as the app
        leaves it on screen; else the kit's panel screen, the sleep
        screen or the live view under the banner line, held or not.
    """
    if state.power is not None and state.last_view is not None:
        return state.last_view
    app, device = APPS[state.city.name], device_of(state=state)
    overlay = panel_view(
        device=device, app=app, state=state, zone=state.city.zone,
    )
    if overlay is not None:
        return cast(ScreenView, overlay)
    banner = banner_line(
        panel=state.panel, app=app, state=state, device=device,
    )
    stop_view = banner_live_view(state=state, banner=banner)
    if state.panel.asleep:
        return sleep_view(state=state, stop_view=stop_view)
    return current_view(state=state, stop_view=stop_view)


def frame_of(*, state: State) -> NDArray[np.uint8]:
    """
    Draw the view a state shows, over the golden tiles.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 grey pixels.
    """
    app = TransitApp(city=state.city, parts=QUIET_PARTS)
    app.maps = MapRenderer(tiles=GoldenTiles())
    return drawing(app=app, view=view_of(state=state))()


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
        departures=tuple(
            departure(
                departs=START + timedelta(minutes=minutes),
                mode=SydneyFeed.BUS,
                kind=SydneyMode.BUS,
                platform='B',
            )
            for minutes in (3, 6, 9)
        ),
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


GAUGE_AHEAD: Final[tuple[tuple[str, str], ...]] = (
    ('c1', 'Parramatta Rd Bef Catherine St'),
    ('c2', 'Parramatta Rd Bef Johnston St'),
    ('c3', 'Parramatta Rd Aft Johnston St'),
    ('c4', 'Parramatta Rd Bef Lyons Rd'),
    ('c5', 'Parramatta Rd Opp Univ Of Sydney'),
    ('c6', 'Broadway Central Park'),
    ('c7', 'Harris St Ultimo'),
    ('c8', 'Quay St Haymarket'),
    ('c9', 'Elizabeth St Museum'),
    ('c10', 'Oxford St Taylor Square'),
    ('c11', 'Flinders St Moore Park'),
    ('c12', 'Cleveland St Redfern'),
    ('c13', 'Regent St Chippendale'),
    ('c14', 'George St Railway Square'),
    ('c15', 'Castlereagh St Market'),
    ('c16', 'Bathurst St Town Hall'),
    ('c17', 'Liverpool St Hyde Park'),
    ('c18', 'College St Hyde Park'),
    ('pitt', 'Pitt St At Barlow St'),
    ('c20', 'Hay St/Belmore Park'),
)


def gauge_run() -> TripPattern:
    """
    The long ride of the gauge scenes, two stops before the From.

    Returns
    -------
    TripPattern
        The hero's trip.
    """
    calls_ahead = tuple(
        call_at(
            at=180 + 120 * number,
            x=150.0 * number,
            station=station,
            name=name,
        )
        for number, (station, name) in enumerate(GAUGE_AHEAD, start=1)
    )
    return pattern(
        trip_id='trip-0803',
        route_id='r_606',
        stops=(
            call_at(at=-60, x=-300.0, station='A', name='Johnston St'),
            call_at(at=60, x=-150.0, station='B', name='Norton Plaza'),
            call_at(
                at=180, x=0.0, station=FROM, name='Leichhardt PS, Norton St',
            ),
            *calls_ahead,
        ),
    )


def gauge_pin() -> State:
    """
    Show the Stop view of a Destination pin with a long ride ahead.

    Returns
    -------
    State
        State at `START`, before the hero's trip loads.
    """
    state, _ = on_view(
        view=LiveView.STOP,
        pin=Pin(
            origin=place(STAND, name='Leichhardt PS, Norton St', point=CENTRE),
            destination=place('pitt', name='Pitt St At Barlow St'),
            kind=SydneyMode.BUS,
        ),
        departures=tuple(
            departure(
                departs=START + timedelta(minutes=minutes),
                mode=SydneyFeed.BUS,
                kind=SydneyMode.BUS,
                ride=38,
            )
            for minutes in (3, 6, 9)
        ),
        station=FROM,
    )
    return state


def stop_gauge() -> State:
    """
    Show the Stop view of a Destination pin with a long ride ahead.

    Returns
    -------
    State
        State at `START`.
    """
    return patterns_loaded(
        state=gauge_pin(), patterns={'trip-0803': gauge_run()},
    )


BUSY_RIGHT: Final[tuple[float, ...]] = (
    -250.0, -120.0, 40.0, 200.0, 380.0, -60.0, 420.0, 1660.0, 1720.0,
    1810.0, 2900.0, 2960.0,
)  # metres east of the From, buses running the hero's way
BUSY_LEFT: Final[tuple[float, ...]] = (
    -200.0, 100.0, 300.0, 440.0, -280.0, 260.0, 160.0, 2950.0,
)  # metres east of the From, buses running the other way


def stop_gauge_busy() -> State:
    """
    Show the long ride with buses both ways, three in its break.

    Returns
    -------
    State
        State at `START`.
    """
    run = gauge_run()
    back = replace(run, stops=tuple(reversed(run.stops)))
    trips = {
        **{
            f'right-{number}': replace(run, trip_id=f'right-{number}')
            for number in range(len(BUSY_RIGHT))
        },
        **{
            f'left-{number}': replace(back, trip_id=f'left-{number}')
            for number in range(len(BUSY_LEFT))
        },
    }
    reports = tuple(
        vehicle(
            timestamp=int(START.timestamp()),
            trip_id=f'{way}-{number}',
            point=(x, 0.0),
        )
        for way, spots in (('right', BUSY_RIGHT), ('left', BUSY_LEFT))
        for number, x in enumerate(spots)
    )
    state, _ = step(gauge_pin(), PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=START, vehicles=reports),
    ))
    return patterns_loaded(
        state=state, patterns={'trip-0803': run, **trips},
    )


def map_scene() -> State:
    """
    Show the Map with a route line and a live bus.

    Returns
    -------
    State
        State at `START`.
    """
    state, effects = step(
        loaded(state=fresh(centre=CENTRE), shaped=True), PRESS,
    )
    others = tuple(
        effect
        for effect in effects
        if isinstance(effect, FetchFeed) and effect.feed != BUS_POSITIONS
    )
    state, _ = answer_fetches(state=state, effects=others)
    line = make_shape(shape_id='b:0', points=ROUTE)
    state, _ = step(state, ShapesLoaded(shapes={'b:0': line}))
    bus = vehicle(timestamp=int(START.timestamp()), point=ROUTE[1])
    positions = snapshot(fetched_at=START, vehicles=(bus,))
    state, _ = step(
        state, PositionsFetched(feed=BUS_POSITIONS, snapshot=positions),
    )
    return state


def map_framed(*, framing: MapFraming) -> State:
    """
    Show the Map scene's pin with every trip's pattern, framed one way.

    Parameters
    ----------
    framing : MapFraming
        The map framing setting.

    Returns
    -------
    State
        State at `START`; each 606 calls every 150 seconds from 7.5
        minutes before the From to 5 minutes after, so its timetable
        marker sits between stops.
    """
    state = map_scene()
    board = replace(state.settings.board, map_framing=framing)
    state = replace(state, settings=replace(state.settings, board=board))
    patterns: dict[str, TripPattern | None] = {
        item.trip_id: pattern(
            trip_id=item.trip_id,
            stops=calls(
                stations=tuple(APPROACH),
                start=(
                    EIGHT
                    + int((item.departs - START).total_seconds())
                    - 450
                ),
                step=150,
                points=APPROACH,
            ),
        )
        for item in state.board.departures
    }
    return patterns_loaded(state=state, patterns=patterns)


def line_scene() -> State:
    """
    Show a bus pin with no Destination and the day's lines at its stop.

    Returns
    -------
    State
        State at `START`; the 606 comes from the west so its dashes run
        up to the From and its ride is solid after it, the 610 leaves
        north-east, and the 620 passes north to south, loaded as a day
        line and drawn dashed. Every pattern is measured along its shape.
    """
    plain = Pin(
        origin=place('C', name='Hollis St', point=CENTRE),
        kind=SydneyMode.BUS,
    )
    runs = {
        'a': ('606', 'a:0', 3, APPROACH),
        'b': ('610', 'b:0', 6, NORTH_EAST),
        'x': ('620', 'x:0', 9, DAY_STOPS),
    }
    shapes = {
        trip: make_shape(shape_id=shape_id, points=list(points.values()))
        for trip, (_, shape_id, _, points) in runs.items()
    }
    made = {
        trip: replace(
            departure(
                departs=START + timedelta(minutes=minutes),
                route=route,
                mode=SydneyFeed.BUS,
                kind=SydneyMode.BUS,
                trip_id=trip,
            ),
            shape_id=shape_id,
        )
        for trip, (route, shape_id, minutes, _) in runs.items()
    }
    state, _ = on_view(
        view=LiveView.MAP,
        pin=plain,
        departures=(made['a'], made['b']),
    )
    held = state.map.day_lines
    assert held is not None
    state, _ = step(
        state, DayLinesLoaded(request=held.request, departures=(made['x'],)),
    )
    patterns: dict[str, TripPattern | None] = {
        trip: measured(
            made=pattern(
                trip_id=trip,
                route=runs[trip][0],
                stops=calls(
                    stations=tuple(runs[trip][3]),
                    start=(
                        EIGHT
                        + int((made[trip].departs - START).total_seconds())
                        - 150 * list(runs[trip][3]).index('C')
                    ),
                    step=150,
                    points=runs[trip][3],
                ),
            ),
            line=line,
        )
        for trip, line in shapes.items()
    }
    return patterns_loaded(
        state=state, patterns=patterns, shapes=list(shapes.values()),
    )


def destination_scene() -> State:
    """
    Show a bus pin with a Destination on its route, at the default zoom.

    Returns
    -------
    State
        State at `START`; the 606 runs from the west through the From to
        the Destination just past `ROUTE[1]`, so its From and To are lettered
        A and B and its ride between them is solid.
    """
    beyond = (
        ROUTE[1][0] + (ROUTE[2][0] - ROUTE[1][0]) / 4,
        ROUTE[1][1] + (ROUTE[2][1] - ROUTE[1][1]) / 4,
    )
    stops = {
        name: point for name, point in APPROACH.items() if name != 'E2'
    }
    stops['D'] = beyond
    stops['E2'] = ROUTE[2]
    pin = Pin(
        origin=place('C', name='Hollis St', point=CENTRE),
        destination=place('D', name=LABEL, point=beyond),
        kind=SydneyMode.BUS,
    )
    line = make_shape(shape_id='a:0', points=list(stops.values()))
    made = replace(
        departure(
            departs=START + timedelta(minutes=3),
            route='606',
            mode=SydneyFeed.BUS,
            kind=SydneyMode.BUS,
            trip_id='a',
        ),
        shape_id='a:0',
    )
    state, _ = on_view(view=LiveView.MAP, pin=pin, departures=(made,))
    run = measured(
        made=pattern(
            trip_id='a',
            route='606',
            stops=calls(
                stations=tuple(stops),
                start=EIGHT + 180 - 150 * list(stops).index('C'),
                step=150,
                points=stops,
            ),
        ),
        line=line,
    )
    return patterns_loaded(state=state, patterns={'a': run}, shapes=[line])


def map_panes() -> State:
    """
    Show a two-change connection pin's Map, one pane per change.

    Returns
    -------
    State
        State at 7:30 on the connection's day.
    """
    state = started(view=LiveView.MAP, pins=(TWO_CHANGES,))
    lists = (LEG1, LEG2, TRAINS)
    legs = way_legs(pin=TWO_CHANGES, way=TWO_WAY)
    state, _ = step(state, DeparturesLoaded(
        pin=TWO_CHANGES,
        departures=(),
        until=at(10, 30),
        legs=Legs(lists=MappingProxyType({
            leg_key(leg=leg): items for leg, items in zip(legs, lists)
        })),
    ))
    return state


def strip_scene(*, view: LiveView) -> State:
    """
    Show trains on a branching line on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.

    Returns
    -------
    State
        State at `START`, with the reach and every train's pattern.
    """
    departures = tuple(
        train(minutes=minutes, trip_id=trip)
        for trip, (_, minutes) in STRIP_TRIPS.items()
    )
    pin = replace(
        PIN,
        destination=None,
        origin=place('C', name='Central', point=POINTS['C']),
    )
    state, _ = on_view(view=view, pin=pin, departures=departures)
    state, _ = step(state, StripLoaded(key=KEY, reach=example_reach()))
    line = make_shape(
        shape_id='strip',
        points=[POINTS[station] for station in ('J', *EAST)],
    )
    patterns: dict[str, TripPattern | None] = {
        trip: measured(
            made=line_pattern(
                trip_id=trip,
                stations=stations,
                minutes=minutes,
            ),
            line=line,
        )
        for trip, (stations, minutes) in STRIP_TRIPS.items()
    }
    return patterns_loaded(state=state, patterns=patterns, shapes=[line])


def train_strip_scene(*, destination: str) -> State:
    """
    Show a train Strip following one train to a Destination.

    Parameters
    ----------
    destination : str
        The Destination's station ID and name.

    Returns
    -------
    State
        State at `START` on the Strip, with the train example's reach
        and the followed train's pattern.
    """
    pin = Pin(
        origin=place('Central', name='Central'),
        destination=place(destination, name=destination),
        kind=train_mode(),
    )
    key = strip_key(pin=pin, end_points=end_point_modes(
        modes=city_under_test().modes,
    ))
    state, _ = on_view(
        view=LiveView.STRIP, pin=pin, departures=(train_departure(),),
    )
    state, _ = step(
        state, StripLoaded(key=key, reach=train_reach_example()),
    )
    return patterns_loaded(
        state=state, patterns={TRAIN_TRIP: train_pattern()},
    )


def measured(*, made: TripPattern, line: Shape) -> TripPattern:
    """
    Put a pattern on a shape, measuring each call along it.

    Parameters
    ----------
    made : TripPattern
        The pattern.
    line : Shape
        The shape it runs along.

    Returns
    -------
    TripPattern
        The pattern naming the shape, with metres along at each call;
        unmeasured when a call has no point.
    """
    return measure(pattern=replace(made, shape_id=line.shape_id), shape=line)


def patterns_loaded(
    *,
    state: State,
    patterns: dict[str, TripPattern | None],
    shapes: Sequence[Shape] = (),
) -> State:
    """
    Load some trips' patterns.

    Parameters
    ----------
    state : State
        State.
    patterns : dict[str, TripPattern | None]
        Patterns by trip ID.
    shapes : Sequence[Shape]
        Shapes loaded with them.

    Returns
    -------
    State
        State after the load.
    """
    state, _ = step(state, PatternsLoaded(
        trip_ids=frozenset(patterns), patterns=patterns,
        shapes={line.shape_id: line for line in shapes},
    ))
    return state


def connection_strip(*, view: LiveView) -> State:
    """
    Show the Work connection pin's legs on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.

    Returns
    -------
    State
        State at 7:30 on the connection's day, with both legs' patterns.
    """
    return patterns_loaded(
        state=connection(view=view), patterns=dict(LEG_PATTERNS),
    )


def connection(*, view: LiveView) -> State:
    """
    Show the Work connection pin on a Live view.

    Parameters
    ----------
    view : LiveView
        The Live view.

    Returns
    -------
    State
        State at 7:30 on the connection's day.
    """
    return with_legs(state=started(view=view))


EASY_LATES: Final[Mapping[str, int]] = MappingProxyType({
    'a0733': 4, 'b0746': 2, 'a0822': -2,
})
TIGHT_LATES: Final[Mapping[str, int]] = MappingProxyType({
    'a0733': 4, 'a0748': 4, 'a0822': -2,
})
MISSED_LATES: Final[Mapping[str, int]] = MappingProxyType({
    'a0733': 6, 'a0748': 6, 'a0842': 8,
})


def late_connection(*, view: LiveView, lates: Mapping[str, int]) -> State:
    """
    Show the Work connection pin once its feed has answered, then late.

    Parameters
    ----------
    view : LiveView
        The Stop or Strip view.
    lates : Mapping[str, int]
        Minutes late by trip ID in the second answer.

    Returns
    -------
    State
        State at 7:30 with the first offers remembered and the trips
        late.
    """
    state = (
        connection(view=view)
        if view is LiveView.STOP
        else connection_strip(view=view)
    )
    state = late_legs(state=state, lates={})
    return late_legs(state=state, lates=lates)


def chooser() -> State:
    """
    Show the chooser at 606's Towards column.

    Returns
    -------
    State
        State at `START`.
    """
    return replace(stop(), chooser=at_towards())


def chooser_routes() -> State:
    """
    Show the chooser on 609 in a ROUTE column, its preview overflowing.

    Returns
    -------
    State
        State at `START`.
    """
    return replace(
        stop(),
        chooser=turned(chooser=at_routes(), steps=1, reach=None)[0],
    )


def building() -> State:
    """
    Show the Building screen, with no timetable.

    Returns
    -------
    State
        State at `START`.
    """
    state = fresh()
    return replace(state, rebuild=replace(state.rebuild, missing=True))


def notice() -> State:
    """
    Show the notice while shutting down.

    Returns
    -------
    State
        State at `START`, shutting down.
    """
    state, _ = step(stop(), PowerRequested(action=PowerAction.OFF))
    return state


def panel(
    *,
    menu: Menu | None = None,
    wifi: WifiSetup | None = None,
    confirm: Confirm | None = None,
) -> State:
    """
    Show a panel screen over the Stop view.

    Parameters
    ----------
    menu : Menu | None
        The open menu.
    wifi : WifiSetup | None
        The open Wi-Fi setup.
    confirm : Confirm | None
        The question asked.

    Returns
    -------
    State
        State at `START`, with the probe's facts.
    """
    state = stop()
    facts = replace(FACTS, app=TilesFacts(built='2025-10-19T08:00:00+11:00'))
    shown = replace(
        state.panel, facts=facts, menu=menu, wifi=wifi, confirm=confirm,
    )
    return replace(state, panel=shown)


def asleep() -> State:
    """
    Show the sleep screen over the Map scene's pin.

    Returns
    -------
    State
        State at `START`, asleep.
    """
    state = map_scene()
    return replace(state, panel=replace(state.panel, asleep=True))


SCENES: Final[dict[str, Callable[[], State]]] = {
    'stop': stop,
    'stop_larger_inverted': stop_larger_inverted,
    'stop_no_key': stop_no_key,
    'stop_trainlink': stop_trainlink,
    'stop_cambridge_trains': stop_cambridge_trains,
    'stop_no_destination': stop_no_destination,
    'stop_both_sides': lambda: both_sides_scene(sides=StopSides.TIME_ORDER),
    'stop_each_way': lambda: both_sides_scene(sides=StopSides.EACH_WAY),
    'stop_gauge': stop_gauge,
    'stop_gauge_busy': stop_gauge_busy,
    'stop_estimates': lambda: estimated(
        state=stop(), lates={0: (-2, False), 1: (2, False)},
    ),
    'stop_uncertain': lambda: estimated(
        state=stop(), lates={0: (11, True), 1: (12, True), 2: (10, False)},
    ),
    'map': map_scene,
    'map_panes': map_panes,
    'map_lines': line_scene,
    'map_destination': destination_scene,
    'map_approach': lambda: map_framed(framing=MapFraming.APPROACH),
    'map_centred': lambda: map_framed(framing=MapFraming.CENTRED),
    'map_both_sides': lambda: both_sides_live(view=LiveView.MAP),
    'strip': lambda: strip_scene(view=LiveView.STRIP),
    'strip_map': lambda: strip_scene(view=LiveView.STRIP_MAP),
    'strip_both_sides': lambda: both_sides_live(view=LiveView.STRIP),
    'strip_map_both_sides': lambda: both_sides_live(
        view=LiveView.STRIP_MAP,
    ),
    'train_strip': lambda: train_strip_scene(destination='Petersham'),
    'train_strip_ends': lambda: train_strip_scene(destination='Lidcombe'),
    'alerts': on_alerts,
    'connection': lambda: connection(view=LiveView.STOP),
    'connection_strip': lambda: connection_strip(view=LiveView.STRIP),
    'connection_strip_map': lambda: connection_strip(
        view=LiveView.STRIP_MAP,
    ),
    'connection_easy': lambda: late_connection(
        view=LiveView.STOP, lates=EASY_LATES,
    ),
    'connection_tight': lambda: late_connection(
        view=LiveView.STOP, lates=TIGHT_LATES,
    ),
    'connection_missed': lambda: late_connection(
        view=LiveView.STOP, lates=MISSED_LATES,
    ),
    'connection_strip_tight': lambda: late_connection(
        view=LiveView.STRIP, lates=TIGHT_LATES,
    ),
    'chooser': chooser,
    'chooser_routes': chooser_routes,
    'chooser_kerbs': lambda: replace(stop(), chooser=at_kerbs()),
    'chooser_kerbs_menu': lambda: replace(
        stop(), chooser=at_kerbs(pairs=(BUSY_PAIR,), menu=0),
    ),
    'building': building,
    'notice': notice,
    'menu': lambda: panel(menu=Menu()),
    'device': lambda: panel(menu=Menu(screen=PanelScreen.DEVICE)),
    'phone': lambda: panel(menu=Menu(screen=PanelScreen.PHONE)),
    'wifi_setup': lambda: panel(wifi=WifiSetup(
        phase=WifiPhase.HOTSPOT,
        password='7Q4K9W2M',
        until=START + timedelta(minutes=10),
    )),
    'confirm': lambda: panel(confirm=Confirm(
        action=ConfirmAction.SHUT_DOWN, origin=Origin.PANEL, asked_at=START,
    )),
    'sleep': asleep,
}
