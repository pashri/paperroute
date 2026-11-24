"""Vehicle markers: pinned badges over other vehicles' arrows."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from paperroute_device.render.canvas import (
    INK,
    PAPER,
    Box,
    Canvas,
    Face,
    Point,
    text_width,
)
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.strip_extents import age_text
from paperroute_transit.render.keep_out import NO_KEEP_OUT, KeepOut
from paperroute_transit.render.others import draw_others, mark_box

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
    (0, 64),
    (0, -64),
    (80, 0),
    (-80, 0),
)
BADGE_HALF_HEIGHT: Final[float] = 10.0
GROW_X: Final[float] = 12.0  # pixels each side, room for a wedge
GROW_Y: Final[float] = 4.0  # pixels each side
STOP_HALF: Final[float] = 18.0
END_HALF: Final[float] = 15.0
CHANGE_HALF: Final[float] = 11.0
DASH: Final[tuple[float, float]] = (4.0, 3.0)  # px on, px off
DASH_WIDTH: Final[float] = 2.0  # px
TAB_DROP: Final[float] = 12.0  # pixels an age tab reaches past a badge


@dataclass(frozen=True)
class WedgeSize:
    """
    The size of a heading wedge.

    Attributes
    ----------
    tip : float
        Pixels the apex stands past the badge's edge.
    back : float
        Pixels the base sits inside the badge's edge.
    half : float
        Half the base's width.
    """

    tip: float
    back: float
    half: float


MAP_WEDGE: Final[WedgeSize] = WedgeSize(tip=10.0, back=3.0, half=7.0)
STRIP_WEDGE: Final[WedgeSize] = WedgeSize(tip=12.0, back=3.0, half=8.0)


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
    size: WedgeSize = MAP_WEDGE,
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
    size : WedgeSize
        How far the wedge stands out and how wide its base is.

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
        (bx + ux * size.tip, by + uy * size.tip),
        (
            bx + px * size.half - ux * size.back,
            by + py * size.half - uy * size.back,
        ),
        (
            bx - px * size.half - ux * size.back,
            by - py * size.half - uy * size.back,
        ),
    ]


def badge_wedge(
    *,
    marker: Marker,
    centre: Point,
    size: WedgeSize,
) -> list[Point] | None:
    """
    Find a badge's wedge triangle, if its heading is known.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre.
    size : WedgeSize
        Wedge size.

    Returns
    -------
    list[Point] | None
        The apex then the base corners, or None with no bearing.
    """
    if marker.bearing is None:
        return None
    return wedge_points(
        centre=centre,
        half=badge_half(route=marker.route),
        bearing=marker.bearing,
        size=size,
    )


def wedges(
    *,
    marker: Marker,
    centre: Point,
    size: WedgeSize,
) -> list[list[Point]]:
    """
    Find the wedge triangles a badge draws.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre.
    size : WedgeSize
        Wedge size.

    Returns
    -------
    list[list[Point]]
        None with no bearing; one; or, for a count of both ways, one at
        the bearing and one opposite it.
    """
    if marker.bearing is None:
        return []
    bearings = [marker.bearing]
    if marker.ends:
        bearings.append(marker.bearing + 180.0)
    return [
        wedge_points(
            centre=centre,
            half=badge_half(route=marker.route),
            bearing=each,
            size=size,
        )
        for each in bearings
    ]


def tab_above(*, marker: Marker) -> bool:
    """
    Say whether a badge's age tab hangs above it.

    Parameters
    ----------
    marker : Marker
        The vehicle.

    Returns
    -------
    bool
        True for a hollow badge with an age whose bearing, snapped to 8
        directions, points below it.
    """
    if marker.live or marker.age is None or marker.bearing is None:
        return False
    return round(marker.bearing / 45) % 8 in (3, 4, 5)


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


def tab_box(*, centre: Point, age: float, above: bool = False) -> Box:
    """
    Give the age tab's outer paper rectangle under or above a badge.

    Parameters
    ----------
    centre : Point
        Badge centre.
    age : float
        Seconds since last heard.
    above : bool
        Mirror the tab about the badge's centre line.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    half_width = text_width(text=age_text(seconds=age), face=AGE_FACE) / 2 + 3
    x, y = centre
    if above:
        top = y - BADGE_HALF_HEIGHT - TAB_DROP
        return x - half_width - 1, top, x + half_width + 1, y - 9
    y += BADGE_HALF_HEIGHT
    return x - half_width - 1, y - 1, x + half_width + 1, y + TAB_DROP


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


def nearest_first(
    *, markers: Sequence[Marker], near: Point | None,
) -> list[Marker]:
    """
    Order markers by distance from a point.

    Parameters
    ----------
    markers : Sequence[Marker]
        Vehicles.
    near : Point | None
        The point, or None to keep the order given.

    Returns
    -------
    list[Marker]
        The markers, nearest first; ties keep their order.
    """
    if near is None:
        return list(markers)
    return sorted(
        markers,
        key=lambda marker: math.dist(near, (marker.x, marker.y)),
    )


def place(
    *,
    markers: Sequence[Marker],
    reserved: Sequence[Box],
) -> list[Placed]:
    """
    Place badges in order, each slid clear of reserved boxes.

    A badge whose own spot overlaps a badge already placed is dropped,
    as is one with no spot clear of the reserved boxes and the placed
    badges.

    Parameters
    ----------
    markers : Sequence[Marker]
        Vehicles in placing order.
    reserved : Sequence[Box]
        Boxes no badge may cover.

    Returns
    -------
    list[Placed]
        The badges kept, in placing order.
    """
    placed: list[Placed] = []
    claimed: list[Box] = []
    for marker in markers:
        own = grown_box(marker=marker, centre=(marker.x, marker.y))
        if any(overlaps(first=own, second=box) for box in claimed):
            continue
        spot = free_spot(marker=marker, taken=[*reserved, *claimed])
        if spot is None:
            continue
        claimed.append(spot[1])
        placed.append(Placed(marker=marker, centre=spot[0]))
    return placed


def clear_of(*, marker: Marker, boxes: Sequence[Box]) -> bool:
    """
    Check whether a vehicle's spot keeps clear of some boxes.

    Parameters
    ----------
    marker : Marker
        The vehicle.
    boxes : Sequence[Box]
        Boxes in map pixels.

    Returns
    -------
    bool
        True when its mark's box overlaps none of them.
    """
    spot = mark_box(centre=(marker.x, marker.y))
    return not any(overlaps(first=spot, second=box) for box in boxes)


def draw_tab(
    canvas: Canvas,
    *,
    centre: Point,
    age: float,
    above: bool = False,
) -> None:
    """
    Hang an inverted age tab under a badge, or above it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    centre : Point
        Badge centre.
    age : float
        Seconds since last heard.
    above : bool
        Hang the tab above the badge.
    """
    left, top, right, bottom = tab_box(centre=centre, age=age, above=above)
    canvas.rect(box=(left, top, right, bottom), grey=PAPER)
    ink = top + 1 if above else top
    canvas.rect(
        box=(left + 1, ink, right - 1, ink + bottom - top - 1),
        grey=INK,
    )
    canvas.text(
        at=(centre[0], ink + 6),
        text=age_text(seconds=age),
        face=AGE_FACE,
        grey=PAPER,
        anchor='mm',
    )


def draw_live(
    canvas: Canvas,
    *,
    marker: Marker,
    centre: Point,
    wedge: WedgeSize = MAP_WEDGE,
) -> None:
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
    wedge : WedgeSize
        Size of the heading wedge.
    """
    left, top, right, bottom = badge_box(route=marker.route, centre=centre)
    canvas.rect(box=(left - 1, top - 1, right + 1, bottom + 1), grey=PAPER)
    canvas.rect(box=(left, top, right, bottom), grey=INK)
    points = badge_wedge(marker=marker, centre=centre, size=wedge)
    if points is not None:
        canvas.polygon(points=points, grey=INK)
    canvas.text(
        at=(centre[0], centre[1] + 1),
        text=marker.route,
        face=BADGE_FACE,
        grey=PAPER,
        anchor='mm',
    )


def draw_dashed(
    canvas: Canvas,
    *,
    marker: Marker,
    centre: Point,
    wedge: WedgeSize,
) -> None:
    """
    Draw a timetable badge with a dashed outline and a solid wedge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre on the canvas.
    wedge : WedgeSize
        Size of the heading wedge.
    """
    left, top, right, bottom = badge_box(route=marker.route, centre=centre)
    canvas.rect(box=(left - 1, top - 1, right + 1, bottom + 1), grey=PAPER)
    inset = DASH_WIDTH / 2
    canvas.line(
        points=[
            (left + inset, top + inset),
            (right - inset, top + inset),
            (right - inset, bottom - inset),
            (left + inset, bottom - inset),
            (left + inset, top + inset),
        ],
        width=DASH_WIDTH,
        grey=INK,
        dash=DASH,
    )
    canvas.text(
        at=(centre[0], centre[1] + 1),
        text=marker.route,
        face=BADGE_FACE,
        grey=INK,
        anchor='mm',
    )
    points = badge_wedge(marker=marker, centre=centre, size=wedge)
    if points is not None:
        canvas.polygon(points=points, grey=INK)


def draw_hollow(
    canvas: Canvas,
    *,
    marker: Marker,
    centre: Point,
    wedge: WedgeSize | None = None,
) -> None:
    """
    Draw an outlined timetable badge, with its age tab when stale.

    A wedge is drawn only when a size is given, and then a tab goes above
    a badge that points down.

    Parameters
    ----------
    canvas : Canvas
        Target.
    marker : Marker
        The vehicle.
    centre : Point
        Badge centre on the canvas.
    wedge : WedgeSize | None
        Size of the heading wedge, or None to draw none.
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
    if wedge is not None:
        for points in wedges(marker=marker, centre=centre, size=wedge):
            canvas.polygon(points=points, grey=INK)
    if marker.age is not None:
        draw_tab(
            canvas,
            centre=centre,
            age=marker.age,
            above=wedge is not None and tab_above(marker=marker),
        )


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
    zoomed in. A vehicle under a corner box is not drawn.

    Parameters
    ----------
    canvas : Canvas
        Target.
    markers : Sequence[Marker]
        Vehicles in map pixels in placing order; of badges that would
        overlap, the first is kept.
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
    shown = [
        marker
        for marker in markers
        if clear_of(marker=marker, boxes=keep_out.corners)
    ]
    placed = place(
        markers=[marker for marker in shown if marker.pinned],
        reserved=[*reserved, *edges, *keep_out.corners],
    )
    draw_others(
        canvas,
        markers=[marker for marker in shown if not marker.pinned],
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
