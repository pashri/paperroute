"""Sydney's values for the engine."""

import re
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_transit.core.city import City
from paperroute_transit.core.geo import DegreeBox, Projection
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


SYDNEY_ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
SYDNEY_PROJECTION: Final[Projection] = Projection(
    origin_lon=151.2093,
    origin_lat=-33.8688,
    grid_lon=149.5,
    grid_lat=-32.0,
)
SYDNEY_BBOX: Final[DegreeBox] = (140.99, -37.51, 153.64, -28.15)
SYDNEY: Final[City] = City(
    name='Sydney',
    zone=SYDNEY_ZONE,
    projection=SYDNEY_PROJECTION,
    bbox=SYDNEY_BBOX,
    modes=SYDNEY_MODES,
    classify=classify_sydney,
)
