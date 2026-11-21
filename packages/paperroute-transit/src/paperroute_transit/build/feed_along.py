"""Turn a feed's own distances into metres along the stored lines."""

import math
import sqlite3
from array import array
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_transit.core.geo import (
    Point,
    Projection,
    Shape,
    point_along,
    to_metres,
    unpack_shape,
)

FEED_REACH: Final[float] = 60.0  # metres a placed call may lie from its stop
FEED_LINES: Final[int] = 16  # stored lines held while converting
FEED_COLUMN: Final[str] = 'shape_dist_traveled'
FEED_TABLE: Final[str] = (
    'CREATE TEMP TABLE IF NOT EXISTS shape_feed '
    '(shape_id TEXT PRIMARY KEY, feed BLOB NOT NULL)'
)
FEED_INSERT: Final[str] = 'INSERT INTO temp.shape_feed VALUES (?, ?)'
FEED_LINE: Final[str] = """
SELECT s.points, f.feed FROM shapes s
JOIN temp.shape_feed f ON f.shape_id = s.shape_id
WHERE s.shape_id = ?
"""
CLEAR_FEED: Final[str] = 'DROP TABLE IF EXISTS temp.shape_feed'
TRIP_SHAPE: Final[str] = 'SELECT shape_id FROM trips WHERE trip_id = ?'
STOP_POINT: Final[str] = 'SELECT lon, lat FROM stops WHERE stop_id = ?'


@dataclass(frozen=True)
class FeedLine:
    """
    A stored line with the feed's distance at each of its vertices.

    Attributes
    ----------
    shape : Shape
        The stored line as unpacked.
    feed : tuple[float, ...]
        One feed distance per vertex, never falling.
    """

    shape: Shape
    feed: tuple[float, ...]


def feed_figure(*, text: str) -> float | None:
    """
    Read a feed distance.

    Parameters
    ----------
    text : str
        The field's text.

    Returns
    -------
    float | None
        The number, or None when the text is blank, not a number or not
        finite.
    """
    try:
        figure = float(text)
    except ValueError:
        return None
    return figure if math.isfinite(figure) else None


def kept_feed(
    *,
    distances: Sequence[float | None],
    keep: NDArray[np.bool_],
) -> tuple[float, ...] | None:
    """
    Pick the feed distances of the points a simplified line keeps.

    Every point is checked, kept or not.

    Parameters
    ----------
    distances : Sequence[float | None]
        The feed distance of each point of the line, None when blank.
    keep : NDArray[np.bool_]
        Which points the simplified line keeps.

    Returns
    -------
    tuple[float, ...] | None
        The kept points' distances, or None when any distance is blank,
        any falls from the one before, or the first equals the last.
    """
    figures = [figure for figure in distances if figure is not None]
    if len(figures) < len(distances) or figures[0] == figures[-1]:
        return None
    if any(later < earlier for earlier, later in pairwise(figures)):
        return None
    return tuple(
        figure
        for figure, kept in zip(figures, keep, strict=True)
        if kept
    )


def pack_feed(*, feed: Sequence[float]) -> bytes:
    """
    Pack feed distances, in native byte order.

    Parameters
    ----------
    feed : Sequence[float]
        The distances.

    Returns
    -------
    bytes
        The packed distances; they never leave the process.
    """
    return array('d', feed).tobytes()


def unpack_feed(*, blob: bytes) -> tuple[float, ...]:
    """
    Unpack the output of pack_feed.

    Parameters
    ----------
    blob : bytes
        The packed distances.

    Returns
    -------
    tuple[float, ...]
        The distances.
    """
    values = array('d')
    values.frombytes(blob)
    return tuple(values)


def feed_along(*, line: FeedLine, distance: float) -> float | None:
    """
    Convert a feed distance to metres along the stored line.

    Parameters
    ----------
    line : FeedLine
        The line and its feed distances.
    distance : float
        A feed distance.

    Returns
    -------
    float | None
        Metres from the line's start, interpolated between the vertices
        either side; None outside the line's first and last distance.
    """
    feed, along = line.feed, line.shape.along
    if not feed[0] <= distance <= feed[-1]:
        return None
    index = min(bisect_right(feed, distance), len(feed) - 1)
    start, end = feed[index - 1], feed[index]
    share = (distance - start) / (end - start) if end > start else 0.0
    return along[index - 1] + share * (along[index] - along[index - 1])


def stop_along(
    *,
    line: FeedLine,
    distance: float,
    stop: Point | None,
) -> int | None:
    """
    Place a call on its line from the feed's distance, in whole metres.

    Parameters
    ----------
    line : FeedLine
        The line and its feed distances.
    distance : float
        The call's feed distance.
    stop : Point | None
        The stop's position in metres, None when it has none.

    Returns
    -------
    int | None
        Metres along the line, or None when the distance is outside the
        line, the stop has no position, or the placed point lies further
        than FEED_REACH from the stop.
    """
    metres = feed_along(line=line, distance=distance)
    if metres is None or stop is None:
        return None
    placed = point_along(shape=line.shape, distance=metres)
    return round(metres) if math.dist(placed, stop) <= FEED_REACH else None


class FeedAlong:
    """
    Works out each stop time's metres along its trip's stored line.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with the trips, stops and shapes of the feed loaded and
        the temp.shape_feed table filled.
    projection : Projection
        The projection the stored lines and the stops' points use.
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        projection: Projection,
    ) -> None:
        self.conn = conn
        self.projection = projection
        self.lines: OrderedDict[str, FeedLine | None] = OrderedDict()
        self.stops: dict[str, Point | None] = {}
        self.trip: tuple[str, str | None] = ('', None)

    def line(self, *, shape_id: str) -> FeedLine | None:
        """
        Fetch a stored line with its feed distances.

        Parameters
        ----------
        shape_id : str
            Stored shape ID.

        Returns
        -------
        FeedLine | None
            The line; None when it is not stored or has no feed
            distances.
        """
        if shape_id in self.lines:
            self.lines.move_to_end(shape_id)
            return self.lines[shape_id]
        found = self.conn.execute(FEED_LINE, (shape_id,)).fetchone()
        line = None if found is None else FeedLine(
            shape=unpack_shape(shape_id=shape_id, blob=found[0]),
            feed=unpack_feed(blob=found[1]),
        )
        self.lines[shape_id] = line
        if len(self.lines) > FEED_LINES:
            self.lines.popitem(last=False)
        return line

    def shape_of(self, *, trip_id: str) -> str | None:
        """
        Find a trip's stored shape ID.

        Parameters
        ----------
        trip_id : str
            The trip.

        Returns
        -------
        str | None
            The shape ID, None when the trip has none.
        """
        if self.trip[0] != trip_id:
            found = self.conn.execute(TRIP_SHAPE, (trip_id,)).fetchone()
            self.trip = (trip_id, None if found is None else found[0])
        return self.trip[1]

    def stop_point(self, *, stop_id: str) -> Point | None:
        """
        Find a stop's position in metres.

        Parameters
        ----------
        stop_id : str
            The stop.

        Returns
        -------
        Point | None
            Its position, None when the stop is unknown or has no
            coordinates.
        """
        if stop_id not in self.stops:
            found = self.conn.execute(STOP_POINT, (stop_id,)).fetchone()
            self.stops[stop_id] = (
                None if found is None or None in found
                else to_metres(
                    lon=found[0], lat=found[1], projection=self.projection,
                )
            )
        return self.stops[stop_id]

    def along(self, *, row: Mapping[str, str]) -> int | None:
        """
        Work out one stop_times.txt record's metres along its line.

        Parameters
        ----------
        row : Mapping[str, str]
            The record.

        Returns
        -------
        int | None
            Whole metres along the trip's stored line; None when the
            record has no usable distance, the trip has no shape, the
            shape has no stored line or feed distances, or the call is
            not near its stop.
        """
        distance = feed_figure(text=row.get(FEED_COLUMN) or '')
        shape_id = self.shape_of(trip_id=row['trip_id'])
        if distance is None or shape_id is None:
            return None
        line = self.line(shape_id=shape_id)
        if line is None:
            return None
        return stop_along(
            line=line,
            distance=distance,
            stop=self.stop_point(stop_id=row['stop_id']),
        )
