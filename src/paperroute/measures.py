"""Where trips' stops fall along their shapes, kept between loads."""

import threading
from collections import OrderedDict
from dataclasses import replace
from typing import Final

from paperroute.core.geo import Point, Shape
from paperroute.core.patterns import TripPattern, measure

MAX_SHAPES: Final[int] = 64
MAX_STOP_LISTS: Final[int] = 16  # per shape

type ShapeKey = tuple[str, int, float]
type StopList = tuple[Point | None, ...]
type Along = tuple[float, ...] | None


def shape_key(*, shape: Shape) -> ShapeKey:
    """
    Tell a shape apart from a rebuilt one of the same ID.

    Parameters
    ----------
    shape : Shape
        The shape.

    Returns
    -------
    ShapeKey
        Its ID, vertex count and length.
    """
    length = shape.along[-1] if shape.along else 0.0
    return shape.shape_id, len(shape.points), length


class StopMeasures:
    """
    Remember where trips' stops fall along their shapes.

    Trips on one shape calling at the same stops share one measurement.
    Measurements are kept for the shapes used most recently, and on each
    shape for the stop lists used most recently; dropping a shape drops
    every measurement on it. Safe to use from several threads.

    Parameters
    ----------
    max_shapes : int
        Shapes kept.
    max_stop_lists : int
        Stop lists kept per shape.
    """

    def __init__(
        self,
        *,
        max_shapes: int = MAX_SHAPES,
        max_stop_lists: int = MAX_STOP_LISTS,
    ) -> None:
        self.max_shapes = max_shapes
        self.max_stop_lists = max_stop_lists
        self.lock = threading.Lock()
        self.kept: OrderedDict[
            ShapeKey, OrderedDict[StopList, Along]
        ] = OrderedDict()

    def recall(self, *, key: ShapeKey, stops: StopList) -> Along:
        """
        Find where the same stops fell on a shape before.

        Parameters
        ----------
        key : ShapeKey
            The shape.
        stops : StopList
            The stops' points in order.

        Returns
        -------
        Along
            Metres along the shape at each stop, or None when not kept;
            a hit marks the shape and the stop list as just used.
        """
        with self.lock:
            lists = self.kept.get(key)
            if lists is None:
                return None
            self.kept.move_to_end(key)
            if stops in lists:
                lists.move_to_end(stops)
            return lists.get(stops)

    def keep(self, *, key: ShapeKey, stops: StopList, along: Along) -> None:
        """
        Store where stops fall on a shape, dropping the least used past limits.

        The stop list and the shape used least recently go first.

        Parameters
        ----------
        key : ShapeKey
            The shape.
        stops : StopList
            The stops' points in order.
        along : Along
            Metres along the shape at each stop.
        """
        with self.lock:
            lists = self.kept.setdefault(key, OrderedDict())
            self.kept.move_to_end(key)
            lists[stops] = along
            lists.move_to_end(stops)
            if len(lists) > self.max_stop_lists:
                lists.popitem(last=False)
            if len(self.kept) > self.max_shapes:
                self.kept.popitem(last=False)

    def measure(self, *, pattern: TripPattern, shape: Shape | None
                ) -> TripPattern:
        """
        Place a trip's stops along its shape, reusing a past measurement.

        Parameters
        ----------
        pattern : TripPattern
            The trip.
        shape : Shape | None
            Its shape, or None.

        Returns
        -------
        TripPattern
            The trip with `along` set, sharing the tuple of an earlier
            trip with the same shape and stops; unchanged when there is no
            shape or a stop has no point.
        """
        stops = tuple(stop.point for stop in pattern.stops)
        if shape is None or None in stops:
            return pattern
        key = shape_key(shape=shape)
        known = self.recall(key=key, stops=stops)
        if known is not None:
            return replace(pattern, along=known)
        measured = measure(pattern=pattern, shape=shape)
        self.keep(key=key, stops=stops, along=measured.along)
        return measured
