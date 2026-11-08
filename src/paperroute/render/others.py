"""Other vehicles: arrows along their bearing, with route numbers."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.render.keep_out import KeepOut
from paperroute_device.render.canvas import (
    INK,
    PAPER,
    Box,
    Canvas,
    Face,
    Point,
    font,
    text_width,
)
from paperroute_transit.core.map_view import Marker

ARROW: Final[tuple[Point, ...]] = (
    (0.0, 5.0),
    (4.0, -4.0),
    (0.0, -1.5),
    (-4.0, -4.0),
)  # across and along the bearing, in pixels
ARROW_SCALES: Final[tuple[tuple[float, int], ...]] = ((1.6, PAPER), (1.0, INK))
DOTS: Final[tuple[tuple[float, int], ...]] = ((6.0, PAPER), (4.0, INK))
MARK_HALF: Final[float] = 8.0
NUMBER_MPP: Final[int] = 14  # numbers at this many m/px and closer
NUMBER_FACE: Final[Face] = Face(weight=800, px=12, squeeze=0.78)
NUMBER_HALO: Final[int] = 2
NUMBER_GAP: Final[float] = 6.0  # from the mark to the number's near end
NUMBER_DROP: Final[float] = 4.0  # the baseline below the mark
CLEAR: Final[Box] = (math.inf, math.inf, math.inf, math.inf)  # a signed row

Mark = tuple[Point, float | None]


@dataclass(frozen=True)
class Number:
    """A route number placed beside its vehicle's mark.

    Attributes
    ----------
    route : str
        Route label.
    at : Point
        The baseline's anchor in map pixels.
    anchor : str
        ``ls`` right of the mark, ``rs`` left of it.
    """

    route: str
    at: Point
    anchor: str


def arrow_points(
    *, centre: Point, bearing: float, scale: float,
) -> list[Point]:
    """
    Turn the arrow to a bearing and place it.

    Parameters
    ----------
    centre : Point
        The vehicle's position.
    bearing : float
        Degrees clockwise from north.
    scale : float
        Size, 1 for the ink arrow.

    Returns
    -------
    list[Point]
        The tip, a wing, the notch and the other wing.
    """
    angle = math.radians(bearing)
    ux, uy = math.sin(angle), -math.cos(angle)
    x, y = centre
    return [
        (
            x + scale * (along * ux - across * uy),
            y + scale * (across * ux + along * uy),
        )
        for across, along in ARROW
    ]


def draw_marks(canvas: Canvas, *, marks: Sequence[Mark]) -> None:
    """
    Draw marks in a few batched calls: all paper, then all ink.

    Parameters
    ----------
    canvas : Canvas
        Target.
    marks : Sequence[Mark]
        Canvas centres with bearings; no bearing draws a dot.
    """
    arrows = [
        (centre, bearing) for centre, bearing in marks if bearing is not None
    ]
    dots = [centre for centre, bearing in marks if bearing is None]
    for (scale, grey), (diameter, _) in zip(ARROW_SCALES, DOTS, strict=True):
        if arrows:
            canvas.polygons(
                polygons=[
                    arrow_points(centre=centre, bearing=bearing, scale=scale)
                    for centre, bearing in arrows
                ],
                grey=grey,
            )
        if dots:
            canvas.dots(points=dots, diameter=diameter, grey=grey)


def draw_mark(
    canvas: Canvas, *, centre: Point, bearing: float | None,
) -> None:
    """
    Draw one vehicle's arrow, or a dot when it has no bearing.

    Parameters
    ----------
    canvas : Canvas
        Target.
    centre : Point
        The vehicle's position on the canvas.
    bearing : float | None
        Degrees clockwise from north, or None.
    """
    draw_marks(canvas, marks=[(centre, bearing)])


def mark_box(*, centre: Point) -> Box:
    """
    Give the box a mark claims.

    Parameters
    ----------
    centre : Point
        The vehicle's position.

    Returns
    -------
    Box
        Left, top, right, bottom, `MARK_HALF` each side.
    """
    x, y = centre
    return x - MARK_HALF, y - MARK_HALF, x + MARK_HALF, y + MARK_HALF


@cache
def cap_height() -> float:
    """
    Measure the number face's capital and digit height.

    Returns
    -------
    float
        Pixels above the baseline.
    """
    return float(font(NUMBER_FACE).getMetrics().fCapHeight)


def signed(*, box: Box) -> Box:
    """
    Write a box as a signed row for `overlaps_any`.

    Parameters
    ----------
    box : Box
        Left, top, right, bottom.

    Returns
    -------
    Box
        Left, top, then right and bottom negated.
    """
    return box[0], box[1], -box[2], -box[3]


def number_spots(*, route: str, mark: Point) -> tuple[Box, Box]:
    """
    Give the boxes a number covers right and left of its mark.

    Parameters
    ----------
    route : str
        Route label.
    mark : Point
        The vehicle's position.

    Returns
    -------
    tuple[Box, Box]
        The right spot, then the left; each is the text from its
        baseline to its cap height, grown by the halo.
    """
    width = text_width(text=route, face=NUMBER_FACE)
    cap = cap_height()
    x, baseline = mark[0], mark[1] + NUMBER_DROP
    top, bottom = baseline - cap - NUMBER_HALO, baseline + NUMBER_HALO
    near, far = NUMBER_GAP - NUMBER_HALO, NUMBER_GAP + width + NUMBER_HALO
    return (x + near, top, x + far, bottom), (x - far, top, x - near, bottom)


def overlaps_any(*, box: Box, boxes: NDArray[np.float64]) -> bool:
    """
    Check a box against many in one vectorised test.

    The rows are signed, as `signed` writes them, so that one comparison
    tests all four edges.

    Parameters
    ----------
    box : Box
        Left, top, right, bottom.
    boxes : NDArray[np.float64]
        An ``(n, 4)`` array of left, top, negated right and negated
        bottom rows.

    Returns
    -------
    bool
        True when the box shares area with any row; touching edges do
        not.
    """
    edges = (box[2], box[3], -box[0], -box[1])
    return bool((boxes < edges).all(axis=1).any())


def free_number(
    *, marker: Marker, boxes: NDArray[np.float64], streets: int,
) -> tuple[Number, Box] | None:
    """
    Find the first free spot for a number.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    boxes : NDArray[np.float64]
        Signed taken rows, the street names first.
    streets : int
        How many rows are street names.

    Returns
    -------
    tuple[Number, Box] | None
        The number and its box: right then left clear of everything, else
        right then left over a street name; None when all four are taken.
    """
    x, baseline = marker.x, marker.y + NUMBER_DROP
    route = marker.route
    right, left = number_spots(route=route, mark=(marker.x, marker.y))
    spots = (
        (
            right,
            Number(route=route, at=(x + NUMBER_GAP, baseline), anchor='ls'),
        ),
        (
            left,
            Number(route=route, at=(x - NUMBER_GAP, baseline), anchor='rs'),
        ),
    )
    return next(
        (
            (number, box)
            for start in (0, streets)
            for box, number in spots
            if not overlaps_any(box=box, boxes=boxes[start:])
        ),
        None,
    )


def taken_rows(
    *, markers: Sequence[Marker], taken: Sequence[Box], keep_out: KeepOut,
) -> NDArray[np.float64]:
    """
    Allocate every box a number must avoid, with a row per number.

    Parameters
    ----------
    markers : Sequence[Marker]
        Vehicles, whose marks fill the last taken rows.
    taken : Sequence[Box]
        Boxes already claimed.
    keep_out : KeepOut
        Baked names and corner boxes.

    Returns
    -------
    NDArray[np.float64]
        Signed rows: street names, then the taken boxes, places and
        marks, then one unfilled row per marker.
    """
    fixed = [
        *keep_out.streets,
        *taken,
        *keep_out.places,
        *(mark_box(centre=(marker.x, marker.y)) for marker in markers),
    ]
    rows = np.empty((len(fixed) + len(markers), 4), dtype=np.float64)
    rows[: len(fixed)] = np.reshape(
        [signed(box=box) for box in fixed], (-1, 4),
    )
    return rows


def place_numbers(
    *,
    markers: Sequence[Marker],
    taken: Sequence[Box],
    keep_out: KeepOut,
) -> list[Number]:
    """
    Place route numbers in order, each clear of everything before it.

    Parameters
    ----------
    markers : Sequence[Marker]
        Other vehicles in placing order, at their map pixels.
    taken : Sequence[Box]
        Map-pixel boxes already claimed: the From, To, changes, ridden
        stops, pinned badges, the banner and the area off the map.
    keep_out : KeepOut
        Baked names and corner boxes; a street name may be covered when
        neither side is free.

    Returns
    -------
    list[Number]
        The numbers placed; a number with no free spot is left out.
    """
    rows = taken_rows(markers=markers, taken=taken, keep_out=keep_out)
    filled = len(rows) - len(markers)
    first_mark = filled - len(markers)
    numbers = []
    for index, marker in enumerate(markers):
        own = rows[first_mark + index].copy()
        rows[first_mark + index] = CLEAR
        found = free_number(
            marker=marker, boxes=rows[:filled], streets=len(keep_out.streets),
        )
        rows[first_mark + index] = own
        if found is not None:
            numbers.append(found[0])
            rows[filled] = signed(box=found[1])
            filled += 1
    return numbers


def draw_others(
    canvas: Canvas,
    *,
    markers: Sequence[Marker],
    mpp: int,
    origin: Point,
    taken: Sequence[Box],
    keep_out: KeepOut,
) -> None:
    """
    Draw other vehicles' marks, and their numbers when zoomed in.

    Parameters
    ----------
    canvas : Canvas
        Target.
    markers : Sequence[Marker]
        Other vehicles in placing order, in map pixels.
    mpp : int
        Metres per pixel; numbers show at `NUMBER_MPP` and closer.
    origin : Point
        The map's top left on the canvas.
    taken : Sequence[Box]
        Map-pixel boxes numbers keep clear of.
    keep_out : KeepOut
        Baked names and corner boxes.
    """
    left, top = origin
    draw_marks(
        canvas,
        marks=[
            ((marker.x + left, marker.y + top), marker.bearing)
            for marker in markers
        ],
    )
    if mpp > NUMBER_MPP:
        return
    for number in place_numbers(
        markers=markers, taken=taken, keep_out=keep_out,
    ):
        canvas.text(
            at=(number.at[0] + left, number.at[1] + top),
            text=number.route,
            face=NUMBER_FACE,
            grey=INK,
            anchor=number.anchor,
            halo=NUMBER_HALO,
        )
