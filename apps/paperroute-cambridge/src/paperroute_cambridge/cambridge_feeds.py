"""Cambridge's realtime feed: one positions feed for buses and coaches."""

import functools
from collections.abc import Iterable
from typing import Final

from paperroute_transit.core.feed_plan import FeedKind, FeedParsers
from paperroute_transit.core.geo import MetreBox, Projection

POSITIONS_FEED: Final[str] = 'bods/positions'
ESTIMATES_FEED: Final[str] = 'bods/estimates'
ESTIMATED_FROM: Final[dict[str, str]] = {ESTIMATES_FEED: POSITIONS_FEED}
DAILY_LIMIT: Final[int] = 4_000  # counted fetches a day
SOURCES: Final[frozenset[str]] = frozenset({'bus', 'coach'})
TRIP_FEEDS: Final[dict[str, str]] = {'bus': ESTIMATES_FEED}
SINGLE_LINE_TYPES: Final[dict[str, int]] = {}


def bods_readers(*, projection: Projection) -> FeedParsers:
    """
    Give the readers, placing positions in a projection.

    The readers load protobuf, so they are imported here, when a feed is
    first read.

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
    from paperroute_cambridge.adapters.bods_positions import parse_positions
    # pylint: disable-next=import-outside-toplevel
    from paperroute_transit.adapters.gtfs_alerts import parse_alerts
    # pylint: disable-next=import-outside-toplevel
    from paperroute_transit.adapters.gtfs_rt import parse_trip_updates
    return FeedParsers(
        trips=parse_trip_updates,
        positions=functools.partial(parse_positions, projection=projection),
        alerts=parse_alerts,
    )


class CambridgeFeeds:
    """
    Cambridge's BODS realtime feed.

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
        The counted fetches allowed each day.

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
            The estimates feed for buses; None for coaches.
        """
        return TRIP_FEEDS.get(source)

    def estimated_from(self, *, feed: str) -> str | None:
        """
        Find the positions feed a trips feed is worked out from.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        str | None
            Its `ESTIMATED_FROM` entry, or None for a fetched feed.
        """
        return ESTIMATED_FROM.get(feed)

    def position_sources(self) -> frozenset[str]:
        """
        List the feed sources that have vehicle positions.

        Returns
        -------
        frozenset[str]
            Bus and coach.
        """
        return SOURCES

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
            Unused; the one feed covers every area.

        Returns
        -------
        tuple[str, ...]
            The one positions feed when a source is bus or coach, else
            empty.
        """
        del box
        if SOURCES.intersection(sources):
            return (POSITIONS_FEED,)
        return ()

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
            Empty: there are none.
        """
        del sources
        return ()

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
            The mode's own source for bus and coach; empty otherwise.
        """
        if mode in SOURCES:
            return frozenset({mode})
        return frozenset()

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
            ESTIMATES for the estimates feed; POSITIONS otherwise.
        """
        if feed == ESTIMATES_FEED:
            return FeedKind.ESTIMATES
        return FeedKind.POSITIONS

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
            The name of the positions or estimates feed; any other path
            as it is.
        """
        if feed == POSITIONS_FEED:
            return 'Bus and coach positions'
        if feed == ESTIMATES_FEED:
            return 'Bus times from positions'
        return feed

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
            The same readers for every feed; positions are placed in the
            projection.
        """
        del feed
        if self.readers is None:
            self.readers = bods_readers(projection=self.projection)
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
            None: there is no alerts feed.
        """
        return SINGLE_LINE_TYPES.get(feed)
