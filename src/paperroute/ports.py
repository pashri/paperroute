"""Interfaces the core and app depend on; adapters implement them."""

from datetime import datetime
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from paperroute.core.model import Departure, Pin


class TimetableRepository(Protocol):
    """Read access to the built timetable."""

    def stop_name(self, *, stop_id: str) -> str:
        """
        Name of a stop or station.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        str
            Name.
        """

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Direct services leaving the pin's From between two moments.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            In departure order.
        """


class FeedsClient(Protocol):
    """Fetches GTFS-realtime feeds."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Download one feed.

        Parameters
        ----------
        feed : str
            Feed path, for example 'v1/gtfs/realtime/buses'.

        Returns
        -------
        bytes
            Protobuf body.
        """

    async def close(self) -> None:
        """Release any connections."""


class Display(Protocol):
    """Somewhere frames are shown."""

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Show a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels.
        """


class Clock(Protocol):
    """The current time."""

    def now(self) -> datetime:
        """
        The current Sydney time.

        Returns
        -------
        datetime
            Aware datetime.
        """


class Observer(Protocol):
    """Receives timing events from the running app."""

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Record that a frame was rendered.

        Parameters
        ----------
        render_ms : float
            Time taken to render, in milliseconds.
        """

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Record that a realtime feed was fetched and parsed.

        Parameters
        ----------
        feed : str
            Feed path.
        fetch_ms : float
            Download time in milliseconds.
        parse_ms : float
            Parse time in milliseconds.
        trips : int
            Number of trip updates found.
        """

    def ticked(self) -> None:
        """Record that the app loop ran once."""
