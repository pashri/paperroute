"""Placing a trip's calls along its line."""

from collections.abc import Sequence

from paperroute_transit.core.geo import Point, Shape, nearest_from


def forward_distances(
    *,
    shape: Shape,
    stops: Sequence[Point],
) -> tuple[float, ...]:
    """Place stops along a shape, in order.

    Each stop is matched only to segments from the previous stop's segment
    onwards, so a loop that passes the same corner twice keeps its order.

    Parameters
    ----------
    shape : Shape
        The path the stops lie on.
    stops : Sequence[Point]
        Stop positions in metres, in running order.

    Returns
    -------
    tuple[float, ...]
        Metres along the shape for each stop; never decreasing.
    """
    result: list[float] = []
    segment = 0
    for stop in stops:
        if len(shape.points) < 2:
            result.append(0.0)
            continue
        segment, distance = nearest_from(
            shape=shape,
            stop=stop,
            first=min(segment, len(shape.points) - 2),
        )
        result.append(max(distance, result[-1] if result else 0.0))
    return tuple(result)


def stop_distances(
    *,
    shape: Shape,
    stops: Sequence[Point],
) -> tuple[float, ...]:
    """Place stops along a shape, in order.

    Parameters
    ----------
    shape : Shape
        The path the stops lie on.
    stops : Sequence[Point]
        Stop positions in metres, in running order.

    Returns
    -------
    tuple[float, ...]
        Metres along the shape for each stop; never decreasing.
    """
    return forward_distances(shape=shape, stops=stops)
