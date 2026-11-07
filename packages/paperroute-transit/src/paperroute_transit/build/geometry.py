"""Line and polygon operations on map features, as vertex arrays."""

from collections.abc import Sequence
from typing import Final

import numpy as np
import shapely
from numpy.typing import NDArray

from paperroute_transit.core.geo import MetreBox

Line = NDArray[np.float64]
Ring = NDArray[np.float64]

NUDGE: Final[float] = 0.001  # metres either side of the nearest point
HEADING_BACK: Final[float] = 5_000.0  # metres back that set an end's heading


def coordinates(*, geometry: shapely.Geometry) -> Line:
    """
    Read a geometry's vertices.

    Parameters
    ----------
    geometry : shapely.Geometry
        A line or ring.

    Returns
    -------
    Line
        The n by 2 vertex array.
    """
    return np.asarray(shapely.get_coordinates(geometry), dtype=np.float64)


def line_parts(*, geometry: shapely.Geometry) -> list[Line]:
    """
    Split a geometry into its lines.

    Parameters
    ----------
    geometry : shapely.Geometry
        A line, multi-line or collection.

    Returns
    -------
    list[Line]
        Each line with two or more vertices; other parts are dropped.
    """
    return [
        coordinates(geometry=part)
        for part in shapely.get_parts(geometry)
        if isinstance(part, shapely.LineString)
        and shapely.get_num_coordinates(part) >= 2
    ]


def polygon_rings(*, geometry: shapely.Geometry) -> list[Ring]:
    """
    List the exterior and interior rings of every polygon in a geometry.

    Parameters
    ----------
    geometry : shapely.Geometry
        A polygon, multi-polygon or collection.

    Returns
    -------
    list[Ring]
        Closed rings; filled together even-odd they give the polygons.
    """
    return [
        coordinates(geometry=ring)
        for part in shapely.get_parts(geometry)
        if isinstance(part, shapely.Polygon) and not part.is_empty
        for ring in shapely.get_rings(part)
    ]


def wkb_lines(*, wkb: bytes) -> list[Line]:
    """
    Read the lines in a WKB geometry.

    Parameters
    ----------
    wkb : bytes
        Well-known binary.

    Returns
    -------
    list[Line]
        The lines.
    """
    return line_parts(geometry=shapely.from_wkb(wkb))


def wkb_rings(*, wkb: bytes) -> list[Ring]:
    """
    Read the polygon rings in a WKB geometry.

    Parameters
    ----------
    wkb : bytes
        Well-known binary.

    Returns
    -------
    list[Ring]
        Exterior and interior rings.
    """
    return polygon_rings(geometry=shapely.from_wkb(wkb))


def merge_lines(*, lines: Sequence[Line]) -> list[Line]:
    """
    Join lines that meet end to end.

    Parameters
    ----------
    lines : Sequence[Line]
        Lines to join.

    Returns
    -------
    list[Line]
        The joined lines.
    """
    multi = shapely.MultiLineString([line for line in lines if len(line) > 1])
    return line_parts(geometry=shapely.line_merge(multi))


def clip_lines(*, lines: Sequence[Line], box: MetreBox) -> list[Line]:
    """
    Cut lines to a box.

    Parameters
    ----------
    lines : Sequence[Line]
        Lines in metres.
    box : MetreBox
        West, south, east and north.

    Returns
    -------
    list[Line]
        The pieces inside the box.
    """
    shapes = [shapely.LineString(line) for line in lines if len(line) > 1]
    return [
        piece
        for clipped in shapely.clip_by_rect(shapes, *box)
        for piece in line_parts(geometry=clipped)
    ]


def clip_rings(*, rings: Sequence[Ring], box: MetreBox) -> list[Ring]:
    """
    Cut rings to a box, one ring at a time.

    Parameters
    ----------
    rings : Sequence[Ring]
        Rings in metres, as exteriors and holes.
    box : MetreBox
        West, south, east and north.

    Returns
    -------
    list[Ring]
        Rings inside the box. Filled together even-odd they cover what
        the input rings covered inside the box.
    """
    shapes = [shapely.Polygon(ring) for ring in rings if len(ring) > 2]
    return [
        ring
        for clipped in shapely.clip_by_rect(shapes, *box)
        for ring in polygon_rings(geometry=clipped)
    ]


def merge_coast(*, lines: Sequence[Line]) -> shapely.Geometry:
    """
    Join coastline ways that run on from one another.

    A way is only joined to one running the same way, so every part
    keeps OSM's land-on-the-left direction.

    Parameters
    ----------
    lines : Sequence[Line]
        Coastline ways in metres.

    Returns
    -------
    shapely.Geometry
        The joined lines.
    """
    return shapely.line_merge(
        shapely.MultiLineString(list(lines)),
        directed=True,
    )


def sea_rings(*, coast: Sequence[Line], frame: MetreBox) -> list[Ring]:
    """
    Fill the sea side of coastlines inside a frame.

    Parameters
    ----------
    coast : Sequence[Line]
        Coastline ways in metres. OSM draws them with land on the left.
    frame : MetreBox
        West, south, east and north of the area to fill.

    Returns
    -------
    list[Ring]
        Rings of the sea faces, to fill even-odd; empty without coast.
    """
    if not coast:
        return []
    merged = merge_coast(lines=coast)
    ends = cut_ends(merged=merged, frame=frame)
    merged = merge_coast(
        lines=[*line_parts(geometry=merged), *ends],
    )
    edges = shapely.union_all(
        [shapely.clip_by_rect(merged, *frame), shapely.box(*frame).exterior],
    )
    tree = shapely.STRtree(shapely.get_parts(merged))
    return [
        ring
        for face in shapely.get_parts(shapely.polygonize([edges]))
        if is_sea(face=face, tree=tree)
        for ring in polygon_rings(geometry=face)
    ]


def cut_ends(
    *,
    merged: shapely.Geometry,
    frame: MetreBox,
) -> list[Line]:
    """
    Run each open coastline end inside a frame on to the frame's edge.

    An extract cuts coastline at its boundary, such as a state border. A
    cut end inside the frame leaves sea and land joined, so the end is
    carried on in the direction it was heading.

    Parameters
    ----------
    merged : shapely.Geometry
        Merged coastlines in metres.
    frame : MetreBox
        West, south, east and north.

    Returns
    -------
    list[Line]
        A two-point line at each open end inside the frame, along the
        coast and reaching a point beyond the frame.
    """
    inner = shapely.box(*frame)
    reach = 2.0 * float(np.hypot(frame[2] - frame[0], frame[3] - frame[1]))
    extensions = (
        end_extension(part=part, forward=forward, reach=reach, inner=inner)
        for part in shapely.get_parts(merged)
        if not part.is_closed and part.length > 0.0
        for forward in (False, True)
    )
    return [extension for extension in extensions if extension is not None]


def end_extension(
    *,
    part: shapely.LineString,
    forward: bool,
    reach: float,
    inner: shapely.Polygon,
) -> Line | None:
    """
    Carry one end of a line on in the direction it was heading.

    The heading runs to the end from a point up to `HEADING_BACK` back
    along the line, so a short jog or a repeated vertex at the end
    doesn't turn it.

    Parameters
    ----------
    part : shapely.LineString
        The line, of non-zero length.
    forward : bool
        True for the last end, False for the first.
    reach : float
        How far to carry the end on, in metres.
    inner : shapely.Polygon
        The frame; ends outside it are left alone.

    Returns
    -------
    Line | None
        A two-point line in the line's direction, or None for an end
        outside the frame or one the line comes back to.
    """
    back = min(HEADING_BACK, part.length)
    ends = (part.length, part.length - back) if forward else (0.0, back)
    tip, before = (
        coordinates(geometry=part.interpolate(at))[0] for at in ends
    )
    heading = tip - before
    length = float(np.hypot(*heading))
    if length == 0.0 or not inner.contains(shapely.Point(tip)):
        return None
    far = tip + heading / length * reach
    return np.array([tip, far] if forward else [far, tip])


def is_sea(*, face: shapely.Polygon, tree: shapely.STRtree) -> bool:
    """
    Decide whether a face lies right of its nearest coastline.

    Parameters
    ----------
    face : shapely.Polygon
        A face cut out by coastlines and the frame.
    tree : shapely.STRtree
        The merged coastlines.

    Returns
    -------
    bool
        True when the face is sea.
    """
    point = face.representative_point()
    nearest = tree.geometries[tree.nearest(point)]
    along = nearest.project(point)
    a = nearest.interpolate(max(along - NUDGE, 0.0))
    b = nearest.interpolate(min(along + NUDGE, nearest.length))
    cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x)
    return bool(cross < 0.0)
