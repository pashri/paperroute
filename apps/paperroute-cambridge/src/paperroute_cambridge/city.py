"""Cambridge's values for the engine."""

import logging
import re
import sqlite3
from datetime import time
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_cambridge.area import (
    CAMBRIDGE_BBOX,
    CAMBRIDGE_PROJECTION,
    COACH_BOX,
    TRIM_RADIUS,
)
from paperroute_cambridge.build.stations import StationSpec, group_stations
from paperroute_cambridge.build.trim import trim_timetable
from paperroute_cambridge.cambridge_feeds import CambridgeFeeds
from paperroute_device.core.device_settings import Window
from paperroute_transit.core.city import (
    AlertRules,
    CatalogueRules,
    City,
    MergeReport,
    Nouns,
    PlaceNames,
    TimetableRules,
)
from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.map_places import MapPlaces, StationSource
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.modes import ModeSpec, RouteClass
from paperroute_transit.core.positions import ParkedAge

logger = logging.getLogger(__name__)

RAIL_SOURCE: Final[str] = 'rail'


class CambridgeFeed(StrEnum):
    """Cambridge's feed sources: which realtime feed serves a route."""

    BUS = 'bus'
    COACH = 'coach'
    TRAIN = 'train'
    OTHER = 'other'


class CambridgeMode(StrEnum):
    """Cambridge's modes: the kinds of route offered when choosing lines."""

    BUS = 'bus'
    COACH = 'coach'
    TRAIN = 'train'


COACH_AGENCIES: Final[frozenset[str]] = frozenset({'OP5050', 'OP5051'})
COACH_TYPE: Final[int] = 200
BUS_TYPE: Final[int] = 3
# GTFS route types: rail, rail replacement bus
RAIL_TYPES: Final[frozenset[int]] = frozenset({2, 714})
RAIL_BLUE: Final[str] = '#01426a'
DARK_TEXT: Final[str] = '#111111'
CAMBRIDGE_MODES: Final[tuple[ModeSpec, ...]] = (
    ModeSpec(
        id=CambridgeMode.BUS,
        label='Bus',
        letter='B',
        chip='#d9d8d0',
        tagged=True,
        chip_text=DARK_TEXT,
    ),
    ModeSpec(
        id=CambridgeMode.COACH,
        label='Coach',
        letter='C',
        chip='#e03c31',
        chip_text=DARK_TEXT,
    ),
    ModeSpec(
        id=CambridgeMode.TRAIN,
        label='Train',
        letter='T',
        chip=RAIL_BLUE,
        by_line=True,
    ),
)


def is_coach(*, route_type: int, agency_id: str | None) -> bool:
    """
    Tell whether a route is a coach service.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id, when the feed gives one.

    Returns
    -------
    bool
        True when the agency is a coach operator or the type is coach.
    """
    return route_type == COACH_TYPE or agency_id in COACH_AGENCIES


def classify_cambridge(
    *, route_type: int, agency_id: str | None, short_name: str,
) -> RouteClass:
    """
    Sort a Cambridge route into its mode and feed source.

    A rail or rail replacement route is a train.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id, when the feed gives one.
    short_name : str
        Route short name.

    Returns
    -------
    RouteClass
        Train for a rail or rail replacement route, coach for a coach route
        and bus for any other bus type, busway and park-and-ride included.
        The mode is None for a route with no short name; any other type is
        not a train, a bus or a coach.
    """
    if route_type in RAIL_TYPES:
        source = CambridgeFeed.TRAIN
    elif is_coach(route_type=route_type, agency_id=agency_id):
        source = CambridgeFeed.COACH
    elif route_type == BUS_TYPE:
        source = CambridgeFeed.BUS
    else:
        return RouteClass(kind=None, source=CambridgeFeed.OTHER)
    kind = CambridgeMode(source) if short_name else None
    return RouteClass(kind=kind, source=source)


STATIONS: Final[tuple[StationSpec, ...]] = (
    StationSpec(
        station_id='cambridge-drummer-street',
        name='Drummer Street',
        member_names=('Drummer St Bus Station', 'Drummer Street'),
        id_prefix='0500CCITY',
    ),
)
DRUMMER_STREET: Final[StopPlace] = StopPlace(
    stop_id='cambridge-drummer-street',
    name='Drummer Street',
    point=to_metres(
        lon=0.1242,
        lat=52.2052,
        projection=CAMBRIDGE_PROJECTION,
    ),
)


def merge_cambridge(*, conn: sqlite3.Connection) -> MergeReport:
    """
    Trim the timetable to Cambridge and group its stations.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable being built, every source loaded.

    Returns
    -------
    MergeReport
        The trips dropped; the other counts are zero.
    """
    report = trim_timetable(
        conn=conn,
        projection=CAMBRIDGE_PROJECTION,
        radius=TRIM_RADIUS,
        box=COACH_BOX,
        coach=is_coach,
        kept_sources=frozenset({RAIL_SOURCE}),
    )
    group_stations(conn=conn, stations=STATIONS)
    logger.info(
        'kept %d routes, removed %d trips, shortened %d coach trips',
        report.routes,
        report.dropped,
        report.cut,
    )
    return MergeReport(dropped=report.dropped, partial=0, overnight=0)


CENTRES: Final[tuple[tuple[str, float, float, float], ...]] = (
    ('Cambridge', 0.1218, 52.2053, 6),
    ('Ely', 0.2622, 52.3995, 4),
    ('Huntingdon', -0.1826, 52.3315, 4),
    ('St Ives', -0.0747, 52.3237, 3),
    ('Newmarket', 0.4083, 52.2440, 4),
    ('Haverhill', 0.4389, 52.0833, 4),
    ('Bedford', -0.4666, 52.1364, 5),
    ('Royston', -0.0244, 52.0481, 3),
    ('S Walden', 0.2416, 52.0226, 3),
)  # label, lon, lat, radius in km
PLACE_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r'\s+(Bus Station|Park-and-Ride|Park & Ride|Railway Station|Station'
    r'|Town Centre|City Centre)$',
)
NEVER: Final[re.Pattern[str]] = re.compile(r'(?!)')


def cambridge_line_group(*, number: str) -> int:
    """
    Sort a line number into the group it is listed in.

    Parameters
    ----------
    number : str
        Line number.

    Returns
    -------
    int
        Always 0: Cambridge lists no lines in groups.
    """
    del number
    return 0


MAP_PLACES: Final[MapPlaces] = MapPlaces(
    kinds=frozenset({'suburb', 'town', 'village'}),
    stations=frozenset({StationSource.RAIL, StationSource.MINOR}),
    # The first nine are council wards, placed at the middle of their
    # buildings; the last two are OSM localities worth keeping.
    extras=(
        ('Abbey', 52.2127, 0.1586),
        ('Arbury', 52.2228, 0.1167),
        ('Castle', 52.2189, 0.1044),
        ('Coleridge', 52.1904, 0.1505),
        ('East Chesterton', 52.2215, 0.1459),
        ("King's Hedges", 52.2294, 0.1340),
        ('Market', 52.2055, 0.1239),
        ("Queen Edith's", 52.1804, 0.1491),
        ('West Chesterton', 52.2175, 0.1290),
        ('Darwin Green', 52.2251, 0.1006),
        ('Franklin Gardens', 52.2293, 0.1082),
    ),
)
CAMBRIDGE_CATALOGUE: Final[CatalogueRules] = CatalogueRules(
    line_sort={},
    replacement=NEVER,
    replacement_lines={},
    numbered_lines=NEVER,
    rail_types=frozenset(),
    line_group=cambridge_line_group,
    places=PlaceNames(centres=CENTRES, suffix=PLACE_SUFFIX, short={}),
    map_places=MAP_PLACES,
)
CAMBRIDGE_ALERTS: Final[AlertRules] = AlertRules(
    name_suffixes=(),
    name_sources=frozenset(),
    loose_sources=frozenset(),
    wharf_sources=frozenset(),
    route_prefixes=('bods:',),
    agency_alerts=None,
)
CAMBRIDGE_ZONE: Final[ZoneInfo] = ZoneInfo('Europe/London')
PEAK_WINDOWS: Final[tuple[Window, ...]] = (
    Window(start=time(7), end=time(9, 30)),
    Window(start=time(16), end=time(18, 30)),
)
SOURCE_NOUNS: Final[dict[str, Nouns]] = {
    CambridgeFeed.BUS: Nouns(vehicle='bus', bay='stop', connection='bus'),
    CambridgeFeed.COACH: Nouns(
        vehicle='coach', bay='stop', connection='coach',
    ),
    CambridgeFeed.TRAIN: Nouns(
        vehicle='train', bay='platform', connection='train',
    ),
    CambridgeFeed.OTHER: Nouns(
        vehicle='service', bay='stop', connection='service',
    ),
}
CAMBRIDGE_REF: Final[str] = 'paperroute_cambridge.city:CAMBRIDGE'
CAMBRIDGE: Final[City] = City(
    name='Cambridge',
    zone=CAMBRIDGE_ZONE,
    projection=CAMBRIDGE_PROJECTION,
    bbox=CAMBRIDGE_BBOX,
    modes=CAMBRIDGE_MODES,
    classify=classify_cambridge,
    catalogue=CAMBRIDGE_CATALOGUE,
    timetable=TimetableRules(
        merge=merge_cambridge, holiday_source='bods', compact=True,
    ),
    alerts=CAMBRIDGE_ALERTS,
    default_pin=Pin(origin=DRUMMER_STREET, kind=CambridgeMode.BUS),
    peak_windows=PEAK_WINDOWS,
    source_nouns=SOURCE_NOUNS,
    noun_overrides={},
    feeds=CambridgeFeeds(projection=CAMBRIDGE_PROJECTION),
    parked_age=ParkedAge.TEN,
    route_fallback=True,
)
