"""Vehicle markers: pinned badges over other vehicles' arrows."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from paperroute.core.map_view import Marker
from paperroute.core.strip_extents import age_text
from paperroute.render.canvas import (
    INK,
    PAPER,
    Box,
    Canvas,
    Face,
    Point,
    text_width,
)
from paperroute.render.keep_out import NO_KEEP_OUT, KeepOut
from paperroute.render.others import draw_others

BADGE_FACE: Final[Face] = Face(weight=800, px=14)
AGE_FACE: Final[Face] = Face(weight=800, px=10)
OFFSETS: Final[tuple[tuple[int, int], ...]] = (
    (0, 0),
    (0, -28),
    (0, 28),
    (0, -36),
    (0, 36),
    (40, 0),
    (-40, 0),
    (40, -28),
    (-40, -28),
    (40, 28),
    (-40, 28),
)
BADGE_HALF_HEIGHT: Final[float] = 10.0
GROW_X: Final[float] = 12.0  # pixels each side, room for a wedge
GROW_Y: Final[float] = 4.0  # pixels each side
STOP_HALF: Final[float] = 18.0
END_HALF: Final[float] = 15.0
CHANGE_HALF: Final[float] = 11.0


@dataclass(frozen=True)
class Placed:
    """A marker and the centre its badge is drawn at.

    Attributes
    ----------
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre in map pixels; differs from the marker's spot when
        the badge slid.
    """

    marker: Marker
    centre: Point


def side(*, value: float) -> float:
    """
    Give the sign of a unit component, treating near zero as zero.

    Parameters
    ----------
    value : float
        Component.

    Returns
    -------
    float
        -1, 0 or 1.
    """
    return 0.0 if abs(value) < 1e-9 else math.copysign(1, value)


def wedge_points(
    *,
    centre: Point,
    half: tuple[float, float],
    bearing: float,
) -> list[Point]:
    """
    Find the heading wedge's triangle for a badge.

    The bearing snaps to 8 directions. The wedge leaves an edge midpoint
    for N, E, S and W, and a corner for the diagonals.

    Parameters
    ----------
    centre : Point
        Badge centre.
    half : tuple[float, float]
        Badge half width and half height.
    bearing : float
        Degrees clockwise from north.

    Returns
    -------
    list[Point]
        The apex, then the two base corners.
    """
    angle = math.radians(round(bearing / 45) % 8 * 45)
    ux, uy = math.sin(angle), -math.cos(angle)
    bx = centre[0] + side(value=ux) * half[0]
    by = centre[1] + side(value=uy) * half[1]
    px, py = -uy, ux
    return [
        (bx + ux * 10, by + uy * 10),
        (bx + px * 7 - ux * 3, by + py * 7 - uy * 3),
        (bx - px * 7 - ux * 3, by - py * 7 - uy * 3),
    ]


def badge_half(*, route: str) -> tuple[float, float]:
    """
    Give a badge's half width and half height.

    Parameters
    ----------
    route : str
        Route label.

    Returns
    -------
    tuple[float, float]
        Half width and half height in pixels.
    """
    return text_width(text=route, face=BADGE_FACE) / 2 + 5, BADGE_HALF_HEIGHT


def badge_box(*, route: str, centre: Point) -> Box:
    """
    Give a badge's box.

    Parameters
    ----------
    route : str
        Route label.
    centre : Point
        Badge centre.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    half_width, half_height = badge_half(route=route)
    x, y = centre
    return x - half_width, y - half_height, x + half_width, y + half_height


def tab_box(*, centre: Point, age: float) -> Box:
    """
    Give the age tab's outer paper rectangle under a badge.

    Parameters
    ----------
    centre : Point
        Badge centre.
    age : float
        Seconds since last heard.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    half_width = text_width(text=age_text(seconds=age), face=AGE_FACE) / 2 + 3
    x, y = centre[0], centre[1] + BADGE_HALF_HEIGHT
    return x - half_width - 1, y - 1, x + half_width + 1, y + 12


def union(*, first: Box, second: Box) -> Box:
    """
    Give the smallest box holding two boxes.

    Parameters
    ----------
    first : Box
        One box.
    second : Box
        The other.

    Returns
    -------
    Box
        Their union.
    """
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def marker_box(*, marker: Marker, centre: Point) -> Box:
    """
    Give the box a marker's badge and any age tab cover.

    Only a hollow badge hangs a tab, so a live marker's age adds no room.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre.

    Returns
    -------
    Box
        Left, top, right, bottom, before any growing.
    """
    box = badge_box(route=marker.route, centre=centre)
    if marker.live or marker.age is None:
        return box
    return union(first=box, second=tab_box(centre=centre, age=marker.age))


def overlaps(*, first: Box, second: Box) -> bool:
    """
    Check whether two boxes overlap; touching edges do not.

    Parameters
    ----------
    first : Box
        One box.
    second : Box
        The other.

    Returns
    -------
    bool
        True when they share area.
    """
    return (
        first[0] < second[2]
        and first[2] > second[0]
        and first[1] < second[3]
        and first[3] > second[1]
    )


def grown_box(*, marker: Marker, centre: Point) -> Box:
    """
    Give the box a badge keeps clear, grown for its wedge.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    left, top, right, bottom = marker_box(marker=marker, centre=centre)
    return left - GROW_X, top - GROW_Y, right + GROW_X, bottom + GROW_Y


def free_spot(
    *,
    marker: Marker,
    taken: Sequence[Box],
) -> tuple[Point, Box] | None:
    """
    Find the first slide offset where a marker's grown box is free.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    taken : Sequence[Box]
        Boxes already claimed.

    Returns
    -------
    tuple[Point, Box] | None
        The badge centre and its grown box, or None when every spot is
        taken.
    """
    for dx, dy in OFFSETS:
        centre = (marker.x + dx, marker.y + dy)
        box = grown_box(marker=marker, centre=centre)
        if not any(overlaps(first=box, second=other) for other in taken):
            return centre, box
    return None


def place(
    *,
    markers: Sequence[Marker],
    reserved: Sequence[Box],
) -> list[Placed]:
    """
    Place badges in order, sliding each to the first free nearby spot.

    A badge with no free spot stays on its true spot and may overlap.

    Parameters
    ----------
    markers : Sequence[Marker]
        Vehicles in placing order.
    reserved : Sequence[Box]
        Boxes no badge may cover.

    Returns
    -------
    list[Placed]
        One placement per marker, in the same order.
    """
    taken = list(reserved)
    placed = []
    for marker in markers:
        spot = free_spot(marker=marker, taken=taken)
        if spot is None:
            placed.append(Placed(marker=marker, centre=(marker.x, marker.y)))
            continue
        taken.append(spot[1])
        placed.append(Placed(marker=marker, centre=spot[0]))
    return placed


def draw_tab(canvas: Canvas, *, centre: Point, age: float) -> None:
    """
    Hang an inverted age tab under a badge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    centre : Point
        Badge centre.
    age : float
        Seconds since last heard.
    """
    left, top, right, bottom = tab_box(centre=centre, age=age)
    canvas.rect(box=(left, top, right, bottom), grey=PAPER)
    canvas.rect(box=(left + 1, top, right - 1, bottom - 1), grey=INK)
    canvas.text(
        at=(centre[0], top + 6),
        text=age_text(seconds=age),
        face=AGE_FACE,
        grey=PAPER,
        anchor='mm',
    )


def draw_live(canvas: Canvas, *, marker: Marker, centre: Point) -> None:
    """
    Draw a solid badge with its heading wedge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre on the canvas.
    """
    left, top, right, bottom = badge_box(route=marker.route, centre=centre)
    canvas.rect(box=(left - 1, top - 1, right + 1, bottom + 1), grey=PAPER)
    canvas.rect(box=(left, top, right, bottom), grey=INK)
    if marker.bearing is not None:
        points = wedge_points(
            centre=centre,
            half=badge_half(route=marker.route),
            bearing=marker.bearing,
        )
        canvas.polygon(points=points, grey=INK)
    canvas.text(
        at=(centre[0], centre[1] + 1),
        text=marker.route,
        face=BADGE_FACE,
        grey=PAPER,
        anchor='mm',
    )


def draw_hollow(canvas: Canvas, *, marker: Marker, centre: Point) -> None:
    """
    Draw an outlined timetable badge, with its age tab when stale.

    Parameters
    ----------
    canvas : Canvas
        Target.
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre on the canvas.
    """
    left, top, right, bottom = badge_box(route=marker.route, centre=centre)
    canvas.rect(box=(left - 1, top - 1, right + 1, bottom + 1), grey=PAPER)
    canvas.rect(box=(left, top, right, bottom), grey=INK)
    canvas.rect(box=(left + 2, top + 2, right - 2, bottom - 2), grey=PAPER)
    canvas.text(
        at=(centre[0], centre[1] + 1),
        text=marker.route,
        face=BADGE_FACE,
        grey=INK,
        anchor='mm',
    )
    if marker.age is not None:
        draw_tab(canvas, centre=centre, age=marker.age)


def shifted(*, point: Point, origin: Point) -> Point:
    """
    Move a map pixel onto the canvas.

    Parameters
    ----------
    point : Point
        Map pixel.
    origin : Point
        The map's top left on the canvas.

    Returns
    -------
    Point
        Canvas pixel.
    """
    return point[0] + origin[0], point[1] + origin[1]


def draw_leaders(
    canvas: Canvas,
    *,
    placed: Sequence[Placed],
    origin: Point,
) -> None:
    """
    Join each slid badge to its true spot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    placed : Sequence[Placed]
        Placements.
    origin : Point
        The map's top left on the canvas.
    """
    spots = []
    for item in placed:
        spot = shifted(point=(item.marker.x, item.marker.y), origin=origin)
        centre = shifted(point=item.centre, origin=origin)
        if spot == centre:
            continue
        canvas.line(points=[spot, centre], width=3, grey=PAPER)
        canvas.line(points=[spot, centre], width=1.5, grey=INK)
        spots.append(spot)
    if spots:
        canvas.dots(points=spots, diameter=3.5, grey=INK)


def square(*, at: Point, half: float) -> Box:
    """
    Give the square box around a point.

    Parameters
    ----------
    at : Point
        The square's centre.
    half : float
        Half its side.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    return at[0] - half, at[1] - half, at[0] + half, at[1] + half


def draw_markers(
    canvas: Canvas,
    *,
    markers: Sequence[Marker],
    mpp: int,
    origin: Point,
    reserved: Sequence[Box],
    edges: Sequence[Box] = (),
    stops: Sequence[Box] = (),
    keep_out: KeepOut = NO_KEEP_OUT,
) -> None:
    """
    Draw vehicles: other vehicles' arrows under placed pinned badges.

    Pinned routes are badges at every zoom, placed first and drawn last,
    so they sit on top. Every other vehicle is an arrow, numbered when
    zoomed in.

    Parameters
    ----------
    canvas : Canvas
        Target.
    markers : Sequence[Marker]
        Vehicles in map pixels, nearest the map centre first.
    mpp : int
        Metres per pixel.
    origin : Point
        The map's top left on the canvas.
    reserved : Sequence[Box]
        Map-pixel boxes no badge or number may cover, such as the From
        and the To.
    edges : Sequence[Box]
        Map-pixel boxes off the map and under the banner, which no badge
        slides into and no number is placed in.
    stops : Sequence[Box]
        Map-pixel boxes of the ridden stops, which numbers keep clear of.
    keep_out : KeepOut
        Map-pixel boxes of the baked names and the corner boxes.
    """
    placed = place(
        markers=[marker for marker in markers if marker.pinned],
        reserved=[*reserved, *edges],
    )
    draw_others(
        canvas,
        markers=[marker for marker in markers if not marker.pinned],
        mpp=mpp,
        origin=origin,
        taken=[
            *reserved,
            *edges,
            *stops,
            *(
                grown_box(marker=item.marker, centre=item.centre)
                for item in placed
            ),
        ],
        keep_out=keep_out,
    )
    draw_leaders(canvas, placed=placed, origin=origin)
    for item in placed:
        draw = draw_live if item.marker.live else draw_hollow
        draw(
            canvas,
            marker=item.marker,
            centre=shifted(point=item.centre, origin=origin),
        )
