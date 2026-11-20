"""A train pattern's shape redrawn along the stored rail track."""

from collections.abc import Iterator, Mapping
from hashlib import blake2b
from itertools import pairwise
from math import dist
from typing import Final

from paperroute_transit.core.geo import Point, Shape, cut, make_shape
from paperroute_transit.core.patterns import TripPattern

TRACK_JOIN: Final[float] = 150.0  # metres
ID_BYTES: Final[int] = 8
MIN_TRACK: Final[int] = 2  # points

type Hops = Mapping[tuple[str, str], tuple[Point, ...]]


def call_pairs(*, pattern: TripPattern) -> Iterator[tuple[int, int]]:
    """
    Pair each stopping call that has a point with the next one.

    Parameters
    ----------
    pattern : TripPattern
        The trip.

    Yields
    ------
    tuple[int, int]
        The indexes of two consecutive stopping calls.
    """
    ends = [
        index
        for index, stop in enumerate(pattern.stops)
        if stop.point is not None and not stop.passes
    ]
    yield from pairwise(ends)


def hop_pieces(
    *, pattern: TripPattern, shape: Shape | None, hops: Hops,
) -> tuple[list[tuple[Point, ...]], list[tuple[str, str]]]:
    """
    Draw each hop between two calls, from the track where it is held.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's feed shape.
    hops : Hops
        Track paths by pair of stations.

    Returns
    -------
    tuple[list[tuple[Point, ...]], list[tuple[str, str]]]
        One piece per hop, and the pairs whose track was used.
    """
    along = pattern.along
    pieces: list[tuple[Point, ...]] = []
    used: list[tuple[str, str]] = []
    for first, second in call_pairs(pattern=pattern):
        start, end = pattern.stops[first], pattern.stops[second]
        key = (start.station, end.station)
        piece = hops.get(key, ())
        if len(piece) >= MIN_TRACK:
            used.append(key)
        elif shape is not None and along is not None:
            piece = cut(shape=shape, start=along[first], end=along[second])
        if not piece and start.point is not None and end.point is not None:
            piece = (start.point, end.point)
        pieces.append(piece)
    return pieces, used


def joined(*, pieces: list[tuple[Point, ...]]) -> list[Point]:
    """
    Join pieces end to end, dropping a start that sits on the last end.

    Parameters
    ----------
    pieces : list[tuple[Point, ...]]
        Pieces in running order.

    Returns
    -------
    list[Point]
        The points of the whole path.
    """
    points: list[Point] = []
    for piece in pieces:
        start = 1 if points and piece and (
            dist(points[-1], piece[0]) <= TRACK_JOIN
        ) else 0
        points.extend(piece[start:])
    return points


def tracked_shape(
    *,
    pattern: TripPattern,
    shape: Shape | None,
    hops: Hops,
    version: str | None,
) -> Shape | None:
    """
    Redraw a train's shape along the track between its calls.

    Parameters
    ----------
    pattern : TripPattern
        The trip, measured on its feed shape.
    shape : Shape | None
        The trip's feed shape, or None when it has none.
    hops : Hops
        Track paths by pair of station IDs.
    version : str | None
        The timetable's build, which the new shape's ID follows.

    Returns
    -------
    Shape | None
        The shape through each call, along the track where a hop is held,
        else along the feed shape, else straight; None when no hop was
        held.
    """
    pieces, used = hop_pieces(pattern=pattern, shape=shape, hops=hops)
    if not used:
        return None
    digest = blake2b(digest_size=ID_BYTES)
    parts = (version or '', pattern.shape_id or '', *used, *pieces)
    for part in parts:
        digest.update(repr(part).encode())
    return make_shape(
        shape_id=f'track:{digest.hexdigest()}',
        points=joined(pieces=pieces),
    )
