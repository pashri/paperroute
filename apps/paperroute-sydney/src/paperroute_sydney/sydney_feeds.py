"""Sydney's realtime feeds: which feed carries each source and mode."""

import functools
from collections.abc import Iterable
from typing import Final

from paperroute_transit.core.feed_plan import FeedKind, FeedParsers
from paperroute_transit.core.geo import MetreBox, Projection, to_metres

DAILY_LIMIT: Final[int] = 60_000  # API calls a day
FEEDS: Final[dict[str, str]] = {
    'bus': 'v1/gtfs/realtime/buses',
    'train': 'v2/gtfs/realtime/sydneytrains',
    'metro': 'v2/gtfs/realtime/metro',
    'regional': 'v1/gtfs/realtime/nswtrains',
}
ALERT_FEEDS: Final[dict[str, str]] = {
    'bus': 'v2/gtfs/alerts/buses',
    'train': 'v2/gtfs/alerts/sydneytrains',
    'metro': 'v2/gtfs/alerts/metro',
    'light_rail': 'v2/gtfs/alerts/lightrail',
    'regional': 'v2/gtfs/alerts/nswtrains',
    'ferry': 'v2/gtfs/alerts/ferries',
}
POSITION_FEEDS: Final[dict[str, tuple[str, ...]]] = {
    'bus': ('v1/gtfs/vehiclepos/buses',),
    'train': ('v2/gtfs/vehiclepos/sydneytrains',),
    'metro': ('v2/gtfs/vehiclepos/metro',),
    'regional': ('v1/gtfs/vehiclepos/nswtrains',),
    'ferry': ('v1/gtfs/vehiclepos/ferries/sydneyferries',),
    'light_rail': (
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
        'v1/gtfs/vehiclepos/lightrail/parramatta',
        'v1/gtfs/vehiclepos/lightrail/newcastle',
    ),
}
LINE_AREAS: Final[dict[str, tuple[float, float, float, float]]] = {
    'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast': (
        151.19, -33.95, 151.26, -33.85,
    ),
    'v2/gtfs/vehiclepos/lightrail/innerwest': (
        151.12, -33.92, 151.22, -33.86,
    ),
    'v1/gtfs/vehiclepos/lightrail/parramatta': (
        150.96, -33.87, 151.10, -33.76,
    ),
    'v1/gtfs/vehiclepos/lightrail/newcastle': (
        151.73, -32.94, 151.80, -32.91,
    ),
}  # west, south, east and north in degrees, with a margin round each line
KIND_MODES: Final[dict[str, frozenset[str]]] = {
    'bus': frozenset({'bus'}),
    'school': frozenset({'bus'}),
    'replacement': frozenset({'bus'}),
    'train': frozenset({'train'}),
    'metro': frozenset({'metro'}),
    'light_rail': frozenset({'light_rail'}),
    'ferry': frozenset({'ferry'}),
    'coach': frozenset({'coach'}),
}
MODE_NAMES: Final[dict[str, str]] = {
    'bus': 'Buses',
    'train': 'Sydney Trains',
    'metro': 'Metro',
    'regional': 'NSW TrainLink',
    'ferry': 'Ferries',
    'light_rail': 'Light rail',
}
SINGLE_LINE_TYPES: Final[dict[str, int]] = {
    'v2/gtfs/alerts/metro': 401,
}  # GTFS route type of the one line an alerts feed serves


def line_near(*, feed: str, box: MetreBox, projection: Projection) -> bool:
    """
    Say whether a feed's line comes near an area.

    Parameters
    ----------
    feed : str
        Feed path.
    box : MetreBox
        The area: west, south, east and north in metres.
    projection : Projection
        The projection the box is in.

    Returns
    -------
    bool
        True when the feed's `LINE_AREAS` entry overlaps the box, and for
        a feed with no entry.
    """
    if feed not in LINE_AREAS:
        return True
    west_lon, south_lat, east_lon, north_lat = LINE_AREAS[feed]
    west, south = to_metres(
        lon=west_lon,
        lat=south_lat,
        projection=projection,
    )
    east, north = to_metres(
        lon=east_lon,
        lat=north_lat,
        projection=projection,
    )
    left, bottom, right, top = box
    return west <= right and east >= left and south <= top and north >= bottom


def feed_kind(*, feed: str) -> FeedKind:
    """
    Tell what a feed path carries.

    Parameters
    ----------
    feed : str
        Feed path.

    Returns
    -------
    FeedKind
        POSITIONS for any path containing '/vehiclepos/', ALERTS for any
        containing '/alerts/', else TRIPS.
    """
    if '/alerts/' in feed:
        return FeedKind.ALERTS
    if '/vehiclepos/' in feed:
        return FeedKind.POSITIONS
    return FeedKind.TRIPS


def feed_label(feed: str) -> str:
    """
    Name a feed path for people.

    Parameters
    ----------
    feed : str
        Feed path.

    Returns
    -------
    str
        The mode's name and what the feed carries, with the line for a
        light rail positions feed; an unknown path as it is.
    """
    for source, path in FEEDS.items():
        if path == feed:
            return f'{MODE_NAMES[source]} trip updates'
    for source, path in ALERT_FEEDS.items():
        if path == feed:
            return f'{MODE_NAMES[source]} alerts'
    for source, paths in POSITION_FEEDS.items():
        if feed not in paths:
            continue
        label = f'{MODE_NAMES[source]} positions'
        if source == 'light_rail':
            return f"{label} ({feed.rsplit('/', maxsplit=1)[-1]})"
        return label
    return feed


def gtfs_readers(*, projection: Projection) -> FeedParsers:
    """
    Give the GTFS-realtime readers, placing positions in a projection.

    The readers load protobuf, so they are imported here, when a feed is
    first read, and the city loads without it.

    Parameters
    ----------
    projection : Projection
        The projection vehicle positions are placed in.

    Returns
    -------
    FeedParsers
        The trip updates, positions and alerts readers.
    """
    # pylint: disable-next=import-outside-toplevel
    from paperroute_transit.adapters.gtfs_alerts import parse_alerts
    # pylint: disable-next=import-outside-toplevel
    from paperroute_transit.adapters.gtfs_rt import (
        parse_positions,
        parse_trip_updates,
    )
    return FeedParsers(
        trips=parse_trip_updates,
        positions=functools.partial(parse_positions, projection=projection),
        alerts=parse_alerts,
    )


class SydneyFeeds:
    """
    Sydney's TfNSW realtime feeds.

    Parameters
    ----------
    projection : Projection
        The projection vehicle positions are placed in.
    """

    def __init__(self, *, projection: Projection) -> None:
        self.projection = projection
        self.readers: FeedParsers | None = None

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
            Feed path, or None when the source has no live feed.
        """
        return FEEDS.get(source)

    def position_sources(self) -> frozenset[str]:
        """
        List the feed sources that have vehicle positions.

        Returns
        -------
        frozenset[str]
            Feed sources.
        """
        return frozenset(POSITION_FEEDS)

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
            An area: west, south, east and north in metres; None for
            anywhere.

        Returns
        -------
        tuple[str, ...]
            Feed paths, sorted and unique, without the light rail lines
            nowhere near the box.
        """
        return tuple(
            sorted(
                {
                    path
                    for source in sources
                    for path in POSITION_FEEDS.get(source, ())
                    if box is None
                    or line_near(
                        feed=path, box=box, projection=self.projection,
                    )
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
            Feed paths, sorted and unique; sources with no alert feed
            add nothing.
        """
        return tuple(
            sorted(
                {
                    ALERT_FEEDS[source]
                    for source in sources
                    if source in ALERT_FEEDS
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
            Feed sources; empty for an unknown mode.
        """
        return KIND_MODES.get(mode, frozenset())

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
            See `feed_kind`.
        """
        return feed_kind(feed=feed)

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
            See `feed_label`.
        """
        return feed_label(feed=feed)

    def parsers(self, *, feed: str) -> FeedParsers:
        """
        Give the readers of a feed's bodies.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        FeedParsers
            The GTFS-realtime readers, which every Sydney feed uses;
            positions are placed in the projection.
        """
        del feed
        if self.readers is None:
            self.readers = gtfs_readers(projection=self.projection)
        return self.readers

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
            401 for the metro alerts feed; None for any other.
        """
        return SINGLE_LINE_TYPES.get(feed)
