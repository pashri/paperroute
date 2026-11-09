"""Northby, a small made-up city that package tests run against."""

import functools
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

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
from paperroute_transit.core.feed_plan import FeedKind, FeedParsers
from paperroute_transit.core.geo import (
    DegreeBox,
    MetreBox,
    Projection,
    to_metres,
)
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.modes import ModeSpec, RouteClass


class NorthbyMode(StrEnum):
    """Northby's modes, which are also its feed sources."""

    BUS = 'bus'
    RAIL = 'rail'
    TRAM = 'tram'


DAILY_LIMIT: Final[int] = 1_000  # API calls a day
ROUTE_MODES: Final[dict[int, NorthbyMode]] = {
    3: NorthbyMode.BUS,
    0: NorthbyMode.TRAM,
    2: NorthbyMode.RAIL,
}  # GTFS route type to mode
SINGLE_LINE_TYPES: Final[dict[str, int]] = {}  # none: every feed is shared
RAIL_LINES: Final[re.Pattern[str]] = re.compile(r'^R\d+$')
TEST_MODES: Final[tuple[ModeSpec, ...]] = (
    ModeSpec(
        id=NorthbyMode.BUS, label='Bus', letter='B', chip='#1f6fb2',
        tagged=True,
    ),
    ModeSpec(
        id=NorthbyMode.RAIL, label='Rail', letter='R', chip='#ffcc00',
        by_line=True, named_alerts=True, chip_text='#000000',
    ),
    ModeSpec(
        id=NorthbyMode.TRAM, label='Tram', letter='T', chip='#c8102e',
        by_line=True, named_alerts=True, loose_alerts=True,
    ),
)
TEST_ZONE: Final[ZoneInfo] = ZoneInfo('Europe/Berlin')
TEST_PROJECTION: Final[Projection] = Projection(
    origin_lon=10.0,
    origin_lat=52.0,
    grid_lon=9.0,
    grid_lat=52.5,
)
TEST_BBOX: Final[DegreeBox] = (9.0, 51.5, 11.0, 52.5)
MARKET: Final[StopPlace] = StopPlace(
    stop_id='1001',
    name='Market Station',
    point=to_metres(lon=10.001, lat=52.002, projection=TEST_PROJECTION),
)
TEST_NOUNS: Final[dict[str, Nouns]] = {
    NorthbyMode.BUS: Nouns(vehicle='bus', bay='stand', connection='bus'),
    NorthbyMode.TRAM: Nouns(vehicle='tram', bay='stop', connection='tram'),
    NorthbyMode.RAIL: Nouns(
        vehicle='train', bay='platform', connection='train',
    ),
}


def classify_northby(
    *, route_type: int, agency_id: str | None, short_name: str,
) -> RouteClass:
    """
    Sort a route into its mode, which is also its feed source.

    Parameters
    ----------
    route_type : int
        GTFS route_type: 3 bus, 0 tram, 2 rail.
    agency_id : str | None
        Ignored.
    short_name : str
        Route short name; a route without one has no mode.

    Returns
    -------
    RouteClass
        The mode and source; no mode and source 'other' for any other
        route type.
    """
    del agency_id
    mode = ROUTE_MODES.get(route_type) if short_name else None
    return RouteClass(kind=mode, source=mode or 'other')


def line_group_of(*, number: str) -> int:
    """
    Sort a line number into the group it is listed in.

    Parameters
    ----------
    number : str
        Line number, such as 'R1'.

    Returns
    -------
    int
        0 for a rail line, else 1.
    """
    return 0 if RAIL_LINES.match(number) else 1


def merge_nothing(*, conn: sqlite3.Connection) -> MergeReport:
    """
    Leave a timetable as it is.

    Parameters
    ----------
    conn : sqlite3.Connection
        Ignored.

    Returns
    -------
    MergeReport
        Nothing dropped.
    """
    del conn
    return MergeReport(dropped=0, partial=0, overnight=0)


@dataclass(frozen=True)
class NorthbyFeeds:
    """
    Northby's realtime feeds: one of each kind per mode.

    Attributes
    ----------
    projection : Projection
        The projection vehicle positions are placed in.
    """

    projection: Projection

    @property
    def daily_limit(self) -> int:
        """
        The API calls allowed each day.

        Returns
        -------
        int
            `DAILY_LIMIT`.
        """
        return DAILY_LIMIT

    def trip_feed(self, *, source: str) -> str | None:
        """
        Find the trip-updates feed of a feed source.

        Parameters
        ----------
        source : str
            Feed source.

        Returns
        -------
        str | None
            'trips/<source>', or None for an unknown source.
        """
        return f'trips/{source}' if source in NorthbyMode else None

    def position_sources(self) -> frozenset[str]:
        """
        List the feed sources that have vehicle positions.

        Returns
        -------
        frozenset[str]
            Every mode.
        """
        return frozenset(NorthbyMode)

    def position_feeds(
        self, *, sources: Iterable[str], box: MetreBox | None,
    ) -> tuple[str, ...]:
        """
        List the vehicle-positions feeds of some feed sources.

        Parameters
        ----------
        sources : Iterable[str]
            Feed sources.
        box : MetreBox | None
            Ignored: every feed covers the whole city.

        Returns
        -------
        tuple[str, ...]
            'positions/<source>' per known source, sorted and unique.
        """
        del box
        return tuple(
            sorted(
                {
                    f'positions/{source}'
                    for source in sources
                    if source in NorthbyMode
                }
            )
        )

    def alert_feeds(self, *, sources: Iterable[str]) -> tuple[str, ...]:
        """
        List the alert feeds of some feed sources.

        Parameters
        ----------
        sources : Iterable[str]
            Feed sources.

        Returns
        -------
        tuple[str, ...]
            'alerts/<source>' per known source, sorted and unique.
        """
        return tuple(
            sorted(
                {
                    f'alerts/{source}'
                    for source in sources
                    if source in NorthbyMode
                }
            )
        )

    def sources_of(self, *, mode: str) -> frozenset[str]:
        """
        List the feed sources a mode's routes come from.

        Parameters
        ----------
        mode : str
            Mode id.

        Returns
        -------
        frozenset[str]
            The mode itself; empty for an unknown mode.
        """
        return frozenset({mode}) if mode in NorthbyMode else frozenset()

    def kind(self, *, feed: str) -> FeedKind:
        """
        Tell what a feed path carries.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        FeedKind
            The kind its first path segment names; TRIPS when it names
            none.
        """
        first = feed.split('/', maxsplit=1)[0]
        return FeedKind(first) if first in FeedKind else FeedKind.TRIPS

    def label(self, *, feed: str) -> str:
        """
        Name a feed path for people.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        str
            The path as it is.
        """
        return feed

    def parsers(self, *, feed: str) -> FeedParsers:
        """
        Give the GTFS-realtime readers of a feed's bodies.

        Parameters
        ----------
        feed : str
            Ignored: every feed is GTFS-realtime.

        Returns
        -------
        FeedParsers
            The trip updates, positions and alerts readers.
        """
        del feed
        # pylint: disable=import-outside-toplevel
        from paperroute_transit.adapters.gtfs_alerts import parse_alerts
        from paperroute_transit.adapters.gtfs_rt import (
            parse_positions,
            parse_trip_updates,
        )

        # pylint: enable=import-outside-toplevel
        return FeedParsers(
            trips=parse_trip_updates,
            positions=functools.partial(
                parse_positions, projection=self.projection,
            ),
            alerts=parse_alerts,
        )

    def single_line_type(self, *, feed: str) -> int | None:
        """
        Give the GTFS route type of an alerts feed that serves one line.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        int | None
            Its `SINGLE_LINE_TYPES` entry, or None for a feed serving
            many lines.
        """
        return SINGLE_LINE_TYPES.get(feed)


TEST_CITY: Final[City] = City(
    name='Northby',
    zone=TEST_ZONE,
    projection=TEST_PROJECTION,
    bbox=TEST_BBOX,
    modes=TEST_MODES,
    classify=classify_northby,
    catalogue=CatalogueRules(
        line_sort={'R1': 1, 'R2': 2},
        replacement=re.compile(r'^X(R\d+)$'),
        replacement_lines={},
        numbered_lines=RAIL_LINES,
        rail_types=frozenset({0, 2}),
        line_group=line_group_of,
        places=PlaceNames(
            centres=(('Northby', 10.0, 52.0, 20),),
            suffix=re.compile(r'\s+(Station|Stop)$'),
            short={},
        ),
    ),
    timetable=TimetableRules(merge=merge_nothing, holiday_source='main'),
    alerts=AlertRules(
        name_suffixes=(' Station', ' Stop'),
        name_sources=frozenset({NorthbyMode.TRAM, NorthbyMode.RAIL}),
        loose_sources=frozenset({NorthbyMode.TRAM}),
        wharf_sources=frozenset(),
        route_prefixes=('main:',),
        agency_alerts=None,
    ),
    default_pin=Pin(origin=MARKET, kind=NorthbyMode.RAIL),
    peak_windows=(
        Window(start=time(7), end=time(9)),
        Window(start=time(16), end=time(18)),
    ),
    source_nouns=TEST_NOUNS,
    noun_overrides={},
    feeds=NorthbyFeeds(projection=TEST_PROJECTION),
)
