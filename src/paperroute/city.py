"""Sydney's values for the engine."""

import re
import sqlite3
from datetime import time
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from paperroute.build.dedupe import merge_rail, relabel_trainlink
from paperroute.build.trainlink import trim_trainlink
from paperroute.sydney_feeds import SydneyFeeds
from paperroute_device.core.device_settings import Window
from paperroute_transit.core.city import (
    AgencyAlerts,
    AlertRules,
    CatalogueRules,
    City,
    MergeReport,
    Nouns,
    PlaceNames,
    TimetableRules,
)
from paperroute_transit.core.geo import DegreeBox, Projection, to_metres
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.modes import ModeSpec, RouteClass


class SydneyFeed(StrEnum):
    """Sydney's feed sources: which realtime feed serves a route."""

    BUS = 'bus'
    TRAIN = 'train'
    METRO = 'metro'
    FERRY = 'ferry'
    LIGHT_RAIL = 'light_rail'
    COACH = 'coach'
    REGIONAL = 'regional'
    OTHER = 'other'


class SydneyMode(StrEnum):
    """Sydney's modes: the kinds of route offered when choosing lines."""

    BUS = 'bus'
    TRAIN = 'train'
    METRO = 'metro'
    LIGHT_RAIL = 'light_rail'
    FERRY = 'ferry'
    SCHOOL = 'school'
    COACH = 'coach'
    REPLACEMENT = 'replacement'


REGIONAL_AGENCIES: Final[frozenset[str]] = frozenset({'711'})
EXACT_FEEDS: Final[dict[int, SydneyFeed]] = {
    2: SydneyFeed.TRAIN,
    106: SydneyFeed.TRAIN,
    401: SydneyFeed.METRO,
    4: SydneyFeed.FERRY,
    900: SydneyFeed.LIGHT_RAIL,
}
INTERCITY: Final[tuple[str, ...]] = ('BMT', 'CCN', 'SCO', 'SHL', 'HUN')
TRAINLINK: Final[tuple[str, ...]] = ('NRC', 'NRW', 'STH', 'WST')
SYDNEY_LINES: Final[re.Pattern[str]] = re.compile(r'^T[1-9]$')
SYDNEY_FERRIES: Final[re.Pattern[str]] = re.compile(r'^F(?:[1-9]|10)$')
TRAINLINK_KINDS: Final[dict[int, SydneyMode]] = {
    106: SydneyMode.TRAIN,
    204: SydneyMode.COACH,
    205: SydneyMode.COACH,
}
EXACT_KINDS: Final[dict[int, SydneyMode]] = {
    **TRAINLINK_KINDS,
    401: SydneyMode.METRO,
    900: SydneyMode.LIGHT_RAIL,
    4: SydneyMode.FERRY,
    700: SydneyMode.BUS,
    712: SydneyMode.SCHOOL,
    714: SydneyMode.REPLACEMENT,
}
BUS_CHIP: Final[str] = '#00b5ef'
SYDNEY_MODES: Final[tuple[ModeSpec, ...]] = (
    ModeSpec(
        id=SydneyMode.BUS, label='Bus', letter='B', chip=BUS_CHIP,
        tagged=True,
        board_with=(SydneyMode.SCHOOL, SydneyMode.REPLACEMENT),
    ),
    ModeSpec(
        id=SydneyMode.TRAIN, label='Train', letter='T', chip='#f99d1c',
        by_line=True, named_alerts=True,
    ),
    ModeSpec(
        id=SydneyMode.METRO, label='Metro', letter='M', chip='#168388',
        by_line=True, named_alerts=True,
    ),
    ModeSpec(
        id=SydneyMode.LIGHT_RAIL, label='Light rail', letter='L',
        chip='#ee343f', by_line=True, named_alerts=True, loose_alerts=True,
    ),
    ModeSpec(
        id=SydneyMode.FERRY, label='Ferry', letter='F', chip='#5ab031',
        by_line=True, named_alerts=True, wharf_names=True,
    ),
    ModeSpec(
        id=SydneyMode.SCHOOL, label='School', letter='S', chip=BUS_CHIP,
        tagged=True, skippable=True,
    ),
    ModeSpec(
        id=SydneyMode.COACH, label='Coach', letter='C', chip='#732a82',
        loose_alerts=True,
    ),
    ModeSpec(
        id=SydneyMode.REPLACEMENT, label='Replacement buses', letter='R',
        chip=BUS_CHIP, under=SydneyMode.BUS, replaces=SydneyMode.TRAIN,
    ),
)


def sydney_feed(*, route_type: int, agency_id: str | None) -> SydneyFeed:
    """
    Find the feed source of a route from its GTFS type and agency.

    NSW TrainLink (bundle agency 711) trains and coaches are Regional. TfNSW
    uses extended route types: 700-799 buses, 200-299 coaches.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id, when the feed gives one.

    Returns
    -------
    SydneyFeed
        Feed source.
    """
    if agency_id in REGIONAL_AGENCIES:
        return SydneyFeed.REGIONAL
    if route_type in EXACT_FEEDS:
        return EXACT_FEEDS[route_type]
    if 700 <= route_type < 800:
        return SydneyFeed.BUS
    return SydneyFeed.COACH if 200 <= route_type < 300 else SydneyFeed.OTHER


def sydney_mode(
    *, route_type: int, agency_id: str | None, short_name: str,
) -> SydneyMode | None:
    """
    Sort a route into a mode from its type, agency and short name.

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
    SydneyMode | None
        Mode; None for a route with no short name, a rail line that is
        not a Sydney, intercity or NSW TrainLink line, or an on-demand or
        unknown type. A rail trip keeps its line's mode when merging gave
        it the bundle's NSW TrainLink agency.
    """
    if not short_name:
        return None
    if agency_id in REGIONAL_AGENCIES:
        if route_type == 2:
            return SydneyMode.TRAIN if short_name in TRAINLINK else None
        return TRAINLINK_KINDS.get(route_type)
    if route_type == 2:
        known = (
            SYDNEY_LINES.match(short_name)
            or short_name in INTERCITY
            or short_name in TRAINLINK
        )
        return SydneyMode.TRAIN if known else None
    return EXACT_KINDS.get(route_type)


def classify_sydney(
    *, route_type: int, agency_id: str | None, short_name: str,
) -> RouteClass:
    """
    Sort a Sydney route into its mode and feed source.

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
        Mode (see `sydney_mode`) and feed source (see `sydney_feed`).
    """
    return RouteClass(
        kind=sydney_mode(
            route_type=route_type,
            agency_id=agency_id,
            short_name=short_name,
        ),
        source=sydney_feed(route_type=route_type, agency_id=agency_id),
    )


def sydney_line_group(*, number: str) -> int:
    """
    Sort a line number into the group it is listed in.

    Parameters
    ----------
    number : str
        Line number, such as 'T1', 'BMT' or 'F1'.

    Returns
    -------
    int
        0 for Sydney Trains and Sydney Ferries lines, 1 for intercity
        lines, 2 for NSW TrainLink lines and 3 for anything else.
    """
    if number in INTERCITY:
        return 1
    if number in TRAINLINK:
        return 2
    if SYDNEY_LINES.match(number) or SYDNEY_FERRIES.match(number):
        return 0
    return 3


def merge_sydney(*, conn: sqlite3.Connection) -> MergeReport:
    """
    Merge the rail feeds and trim and rename NSW TrainLink's bundle trips.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable being built, every source loaded.

    Returns
    -------
    MergeReport
        What the rail merge did.
    """
    report = merge_rail(conn=conn)
    trim_trainlink(conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK)
    relabel_trainlink(conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK)
    return report


CENTRES: Final[tuple[tuple[str, float, float, float], ...]] = (
    ('Sydney', 151.207, -33.870, 55),
    ('Newcastle', 151.750, -32.930, 40),
    ('C Coast', 151.340, -33.430, 30),
    ('Wollongong', 150.890, -34.420, 30),
    ('Blue Mtns', 150.400, -33.700, 25),
)  # label, lon, lat, radius in km
PLACE_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r'\s+(City Centre|Town Centre|CBD|Station|Interchange|Mall|City|Park Ave'
    r'|Shopping Centre|Shops|Market Square|Manning Mall|East|West|North'
    r'|South|CBD)$',
)
PLACE_SHORT: Final[dict[str, str]] = {
    'Port Macquarie': 'Port Mac',
    'Coffs Harbour': 'Coffs',
    'Wagga Wagga': 'Wagga',
    'Batemans Bay': 'Batemans',
    'Raymond Terrace': 'Raymond T',
    'Soldiers Point': 'Soldiers P',
    'Muswellbrook': 'Muswellbk',
    'Murwillumbah': 'Murwillum',
    'Queanbeyan': 'Queanbeyan',
    'University of Wollongong': 'UOW',
    'City Martin Place': 'City',
    'City Wynyard': 'City',
    'City QVB': 'City',
    'Broken Hill': 'Broken Hl',
    'Campbelltown': 'Campbelltn',
    'Stockland Forster': 'Forster',
    'QEII Square': 'Albury',
    'Orana': 'Dubbo',
    'Green Hills': 'Maitland',
    'Park Beach Plaza': 'Coffs',
    'Toormina Gardens': 'Coffs',
    'Sunnyside': 'Murwillum',
}
REPLACEMENT: Final[re.Pattern[str]] = re.compile(
    r'^\d+[A-Z]?(T\d|BM|CN|HU|SC|SH)',
)
REPLACEMENT_LINES: Final[dict[str, str]] = {
    'BM': 'BMT', 'CN': 'CCN', 'HU': 'HUN', 'SC': 'SCO', 'SH': 'SHL',
}
LINE_SORT: Final[dict[str, int]] = {
    **{f'T{line}': line for line in range(1, 10)},
    **{code: 101 + index for index, code in enumerate(INTERCITY)},
    **{code: 111 + index for index, code in enumerate(TRAINLINK)},
}
RAIL_TYPES: Final[frozenset[int]] = frozenset({2, 401, 900})
NAME_SUFFIXES: Final[tuple[str, ...]] = (
    ' Light Rail Station',
    ' Metro Station',
    ' Light Rail',
    ' Station',
    ' Wharf',
)
NAME_SOURCES: Final[frozenset[SydneyFeed]] = frozenset({
    SydneyFeed.TRAIN,
    SydneyFeed.METRO,
    SydneyFeed.LIGHT_RAIL,
    SydneyFeed.REGIONAL,
    SydneyFeed.FERRY,
})
LOOSE_SOURCES: Final[frozenset[SydneyFeed]] = frozenset({
    SydneyFeed.REGIONAL,
    SydneyFeed.LIGHT_RAIL,
})
ROUTE_PREFIXES: Final[tuple[str, ...]] = (
    'sydneytrains:',
    'metro:',
    'bundle:',
)
SYDNEY_CATALOGUE: Final[CatalogueRules] = CatalogueRules(
    line_sort=LINE_SORT,
    replacement=REPLACEMENT,
    replacement_lines=REPLACEMENT_LINES,
    numbered_lines=SYDNEY_FERRIES,
    rail_types=RAIL_TYPES,
    line_group=sydney_line_group,
    places=PlaceNames(
        centres=CENTRES,
        suffix=PLACE_SUFFIX,
        short=PLACE_SHORT,
    ),
)
SYDNEY_ALERTS: Final[AlertRules] = AlertRules(
    name_suffixes=NAME_SUFFIXES,
    name_sources=NAME_SOURCES,
    loose_sources=LOOSE_SOURCES,
    wharf_sources=frozenset({SydneyFeed.FERRY}),
    route_prefixes=ROUTE_PREFIXES,
    agency_alerts=AgencyAlerts(
        agencies=REGIONAL_AGENCIES,
        modes=frozenset({SydneyMode.TRAIN}),
        source=SydneyFeed.REGIONAL,
    ),
)
SYDNEY_ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
SYDNEY_PROJECTION: Final[Projection] = Projection(
    origin_lon=151.2093,
    origin_lat=-33.8688,
    grid_lon=149.5,
    grid_lat=-32.0,
)
SYDNEY_BBOX: Final[DegreeBox] = (140.99, -37.51, 153.64, -28.15)
TOWN_HALL: Final[StopPlace] = StopPlace(
    stop_id='200070',
    name='Town Hall Station',
    point=to_metres(
        lon=151.206899293,
        lat=-33.8735963257,
        projection=SYDNEY_PROJECTION,
    ),
)
DEFAULT_PIN: Final[Pin] = Pin(origin=TOWN_HALL, kind=SydneyMode.TRAIN)
PEAK_WINDOWS: Final[tuple[Window, ...]] = (
    Window(start=time(6, 30), end=time(10)),
    Window(start=time(15), end=time(19)),
)
SOURCE_NOUNS: Final[dict[str, Nouns]] = {
    SydneyFeed.BUS: Nouns(vehicle='bus', bay='stand', connection='bus'),
    SydneyFeed.TRAIN: Nouns(
        vehicle='train', bay='platform', connection='train',
    ),
    SydneyFeed.METRO: Nouns(
        vehicle='metro', bay='platform', connection='metro',
    ),
    SydneyFeed.FERRY: Nouns(vehicle='ferry', bay='wharf', connection='ferry'),
    SydneyFeed.LIGHT_RAIL: Nouns(
        vehicle='light rail', bay='platform', connection='light rail',
    ),
    SydneyFeed.COACH: Nouns(vehicle='coach', bay='stand', connection='coach'),
    SydneyFeed.REGIONAL: Nouns(
        vehicle='service', bay='platform', connection='train',
    ),
    SydneyFeed.OTHER: Nouns(
        vehicle='service', bay='platform', connection='service',
    ),
}
NOUN_OVERRIDES: Final[dict[tuple[str, str], Nouns]] = {
    (SydneyFeed.REGIONAL, SydneyMode.TRAIN): Nouns(
        vehicle='train', bay='platform', connection='train',
    ),
}
SYDNEY_REF: Final[str] = 'paperroute.city:SYDNEY'
SYDNEY: Final[City] = City(
    name='Sydney',
    zone=SYDNEY_ZONE,
    projection=SYDNEY_PROJECTION,
    bbox=SYDNEY_BBOX,
    modes=SYDNEY_MODES,
    classify=classify_sydney,
    catalogue=SYDNEY_CATALOGUE,
    timetable=TimetableRules(merge=merge_sydney, holiday_source='bundle'),
    alerts=SYDNEY_ALERTS,
    default_pin=DEFAULT_PIN,
    peak_windows=PEAK_WINDOWS,
    source_nouns=SOURCE_NOUNS,
    noun_overrides=NOUN_OVERRIDES,
    feeds=SydneyFeeds(projection=SYDNEY_PROJECTION),
)
