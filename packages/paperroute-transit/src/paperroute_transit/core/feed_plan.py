"""Which realtime feeds a city has, and how each one is read."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from paperroute_transit.core.alert_model import AlertsSnapshot
from paperroute_transit.core.geo import MetreBox
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.realtime import RealtimeSnapshot


class FeedKind(StrEnum):
    """What a feed path carries."""

    TRIPS = 'trips'
    POSITIONS = 'positions'
    ALERTS = 'alerts'


class TripsParser(Protocol):
    """Reads a trip-updates feed body."""

    def __call__(
        self, *, data: bytes, fetched_at: datetime,
    ) -> RealtimeSnapshot:
        """
        Read a trip-updates feed body.

        Parameters
        ----------
        data : bytes
            Feed body.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        RealtimeSnapshot
            The feed's trips.
        """


class PositionsParser(Protocol):
    """Reads a vehicle-positions feed body."""

    def __call__(
        self, *, data: bytes, fetched_at: datetime,
    ) -> PositionsSnapshot:
        """
        Read a vehicle-positions feed body.

        Parameters
        ----------
        data : bytes
            Feed body.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        PositionsSnapshot
            The feed's vehicles, placed in the city's projection.
        """


class AlertsParser(Protocol):
    """Reads an alerts feed body."""

    def __call__(
        self, *, data: bytes, feed: str, fetched_at: datetime,
    ) -> AlertsSnapshot:
        """
        Read an alerts feed body.

        Parameters
        ----------
        data : bytes
            Feed body.
        feed : str
            Feed path.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        AlertsSnapshot
            The feed's alerts.
        """


@dataclass(frozen=True)
class FeedParsers:
    """
    How one feed's bodies are read, by what the feed carries.

    Attributes
    ----------
    trips : TripsParser
        Reads trip updates.
    positions : PositionsParser
        Reads vehicle positions.
    alerts : AlertsParser
        Reads alerts.
    """

    trips: TripsParser
    positions: PositionsParser
    alerts: AlertsParser


class FeedPlan(Protocol):
    """A city's realtime feeds: which to fetch and how to read them."""

    @property
    def daily_limit(self) -> int:
        """
        The API calls allowed each day.

        Returns
        -------
        int
            Calls a day.
        """

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

    def position_sources(self) -> frozenset[str]:
        """
        List the feed sources that have vehicle positions.

        Returns
        -------
        frozenset[str]
            Feed sources.
        """

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
            Feed paths, sorted and unique, without the single-line
            feeds whose line misses the box.
        """

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
            What the feed carries.
        """

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
            The feed's name; an unknown path as it is.
        """

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
            The feed's readers.
        """

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
            The route type whose only route the feed's unknown routes
            are, or None for a feed serving many lines.
        """
