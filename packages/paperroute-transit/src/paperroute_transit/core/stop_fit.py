"""Placing a trip's calls along its line."""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Final

from paperroute_transit.core.geo import (
    SKIP_SLACK,
    Point,
    Shape,
    nearest_from,
    point_along,
    project_onto_segment,
)

RADIUS: Final[float] = 100.0  # metres beyond a call's nearest pass
CANDIDATES: Final[int] = 32  # passes kept per call
ORDER_SLACK: Final[float] = 1.0  # metres a pass may lie behind the call before
TIE: Final[float] = 0.1  # metres of total miss a fit must save
MARGIN: Final[float] = 500.0  # metres above the cheapest fit a fit is kept


@dataclass(frozen=True)
class Pass:
    """One place a line runs by a stop.

    Attributes
    ----------
    along : float
        Metres along the line.
    gap : float
        Metres from the stop to the line there.
    """

    along: float
    gap: float


@dataclass(frozen=True)
class Fit:
    """A placement of the calls so far.

    Attributes
    ----------
    along : float
        Metres along the line of the latest call.
    cost : float
        Total miss, in metres, of the calls so far.
    before : Fit | None
        The previous call's fit.
    """

    along: float
    cost: float
    before: Fit | None


def forward_distances(
    *,
    shape: Shape,
    stops: Sequence[Point],
) -> tuple[float, ...]:
    """Place each stop at its nearest point from the previous stop onward.

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

    Each call takes its nearest pass when those are already in order.
    Otherwise the ordered fit over the calls' passes is used when it
    misses the stops by more than `TIE` less than the forward search.

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
    if not stops:
        return ()
    if len(shape.points) < 2:
        return (0.0,) * len(stops)
    calls = [stop for stop, _ in groupby(stops)]
    counts = [len(list(group)) for _, group in groupby(stops)]
    options = call_passes(shape=shape, calls=calls)
    nearest = [nearest_pass(found=found).along for found in options]
    if all(b >= a for a, b in zip(nearest, nearest[1:], strict=False)):
        return spread(distances=nearest, counts=counts)
    today = forward_distances(shape=shape, stops=stops)
    fitted = spread(
        distances=fitted_distances(shape=shape, calls=calls, options=options),
        counts=counts,
    )
    saved = total_miss(shape=shape, stops=stops, distances=today)
    if total_miss(shape=shape, stops=stops, distances=fitted) < saved - TIE:
        return fitted
    return today


def scan(
    *,
    shape: Shape,
    stop: Point,
    ceiling: float,
) -> tuple[float, list[tuple[int, float, float]]]:
    """Measure a stop against the segments that could hold its nearest pass.

    Parameters
    ----------
    shape : Shape
        The line to scan, from its first segment.
    stop : Point
        The stop in metres.
    ceiling : float
        Any known distance from the stop to a point of the line, or
        `math.inf`.

    Returns
    -------
    tuple[float, list[tuple[int, float, float]]]
        The smallest gap found, and each visited segment's index, gap and
        metres along the line; segments too far to matter are skipped.
    """
    points, along = shape.points, shape.along
    last = len(points) - 1
    best, seen, index = ceiling, [], 0
    while index < last:
        gap, offset = project_onto_segment(
            point=stop, start=points[index], end=points[index + 1],
        )
        seen.append((index, gap, along[index] + offset))
        best = min(best, gap)
        index += 1
        reach = math.dist(stop, points[index]) - best - RADIUS - SKIP_SLACK
        if reach > 0 and index < last:
            index = max(
                index, bisect_left(along, along[index] + reach, index) - 1,
            )
    return best, seen


def passes(*, shape: Shape, stop: Point, ceiling: float) -> list[Pass]:
    """Find where a line runs near a stop.

    A pass is a visited segment no farther than `RADIUS` beyond the nearest
    and no farther than either visited neighbour. Only the `CANDIDATES`
    nearest are kept.

    Parameters
    ----------
    shape : Shape
        The line to search.
    stop : Point
        The stop in metres.
    ceiling : float
        Any known distance from the stop to a point of the line, or
        `math.inf`.

    Returns
    -------
    list[Pass]
        The stop's passes, by metres along the line.
    """
    best, seen = scan(shape=shape, stop=stop, ceiling=ceiling)
    gaps = {index: gap for index, gap, _ in seen}
    found = [
        (gap, index, at)
        for index, gap, at in seen
        if gap <= best + RADIUS
        and gap <= gaps.get(index - 1, math.inf)
        and gap <= gaps.get(index + 1, math.inf)
    ]
    found.sort()
    return [
        Pass(along=at, gap=gap)
        for gap, _, at in sorted(found[:CANDIDATES], key=lambda one: one[1])
    ]


def nearest_pass(*, found: Sequence[Pass]) -> Pass:
    """Pick the pass with the smallest gap, the earliest on a tie.

    Parameters
    ----------
    found : Sequence[Pass]
        A stop's passes, by metres along the line.

    Returns
    -------
    Pass
        The nearest pass.
    """
    return min(found, key=lambda one: one.gap)


def call_passes(
    *,
    shape: Shape,
    calls: Sequence[Point],
) -> list[list[Pass]]:
    """Find the passes of each call, each scan bounded by the call before.

    Parameters
    ----------
    shape : Shape
        The line to search.
    calls : Sequence[Point]
        Stop positions in metres, in running order.

    Returns
    -------
    list[list[Pass]]
        Each call's passes.
    """
    options: list[list[Pass]] = []
    ceiling = math.dist(calls[0], shape.points[0])
    for index, call in enumerate(calls):
        if index:
            ceiling = math.dist(call, calls[index - 1]) + min(
                one.gap for one in options[-1]
            )
        options.append(passes(shape=shape, stop=call, ceiling=ceiling))
    return options


def total_miss(
    *,
    shape: Shape,
    stops: Sequence[Point],
    distances: Sequence[float],
) -> float:
    """Add up how far each stop lies from its place on the line.

    Parameters
    ----------
    shape : Shape
        The line the stops were placed on.
    stops : Sequence[Point]
        Stop positions in metres.
    distances : Sequence[float]
        Metres along the line for each stop.

    Returns
    -------
    float
        The sum of each stop's distance to the line point at its distance.
    """
    return sum(
        math.dist(stop, point_along(shape=shape, distance=distance))
        for stop, distance in zip(stops, distances, strict=True)
    )


def kept(*, fits: Sequence[Fit]) -> list[Fit]:
    """Keep the fits worth extending.

    A fit stays when it costs less than every fit that ends earlier, and
    no more than `MARGIN` above the cheapest.

    Parameters
    ----------
    fits : Sequence[Fit]
        Fits of the calls so far.

    Returns
    -------
    list[Fit]
        The kept fits by metres along the line; the last is the cheapest.
    """
    front: list[Fit] = []
    for fit in sorted(fits, key=lambda one: (one.along, one.cost)):
        if not front or fit.cost < front[-1].cost:
            front.append(fit)
    limit = front[-1].cost + MARGIN
    return [fit for fit in front if fit.cost <= limit]


def extend(
    *,
    shape: Shape,
    call: Point,
    found: Sequence[Pass],
    front: Sequence[Fit],
) -> list[Fit]:
    """Place the next call on one of its passes, or where the last one is.

    Parameters
    ----------
    shape : Shape
        The line the calls lie on.
    call : Point
        The call's stop in metres.
    found : Sequence[Pass]
        The call's passes.
    front : Sequence[Fit]
        The kept fits of the calls before, by metres along the line.

    Returns
    -------
    list[Fit]
        The kept fits including this call.
    """
    alongs = [fit.along for fit in front]
    fits = []
    for one in found:
        spot = bisect_right(alongs, one.along + ORDER_SLACK) - 1
        if spot >= 0:
            fits.append(
                Fit(
                    along=max(one.along, front[spot].along),
                    cost=front[spot].cost + one.gap,
                    before=front[spot],
                ),
            )
    for fit in front:
        miss = math.dist(call, point_along(shape=shape, distance=fit.along))
        fits.append(Fit(along=fit.along, cost=fit.cost + miss, before=fit))
    return kept(fits=fits)


def fitted_distances(
    *,
    shape: Shape,
    calls: Sequence[Point],
    options: Sequence[Sequence[Pass]],
) -> list[float]:
    """Place the calls in order on the cheapest run of passes.

    Parameters
    ----------
    shape : Shape
        The line the calls lie on.
    calls : Sequence[Point]
        Stop positions in metres, in running order.
    options : Sequence[Sequence[Pass]]
        Each call's passes.

    Returns
    -------
    list[float]
        Metres along the line for each call; never decreasing.
    """
    front = kept(
        fits=[
            Fit(along=one.along, cost=one.gap, before=None)
            for one in options[0]
        ],
    )
    for call, found in zip(calls[1:], options[1:], strict=True):
        front = extend(shape=shape, call=call, found=found, front=front)
    walk: Fit | None = front[-1]
    distances: list[float] = []
    while walk is not None:
        distances.append(walk.along)
        walk = walk.before
    return distances[::-1]


def spread(
    *,
    distances: Sequence[float],
    counts: Sequence[int],
) -> tuple[float, ...]:
    """Give every stop its call's distance, never below the stop before.

    Parameters
    ----------
    distances : Sequence[float]
        Metres along the line for each call.
    counts : Sequence[int]
        How many stops each call stands for.

    Returns
    -------
    tuple[float, ...]
        Metres along the line for each stop.
    """
    result: list[float] = []
    for distance, count in zip(distances, counts, strict=True):
        result.extend([max(distance, result[-1] if result else 0.0)] * count)
    return tuple(result)
