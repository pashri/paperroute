"""Line and polygon operations on map features, as vertex arrays."""

from collections.abc import Sequence
from typing import Final

import numpy as np
import shapely
from numpy.typing import NDArray

from paperroute.core.geo import MetreBox

Line = NDArray[np.float64]
Ring = NDArray[np.float64]

NUDGE: Final[float] = 0.001  # metres either side of the nearest point


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
    merged = shapely.line_merge(shapely.MultiLineString(list(coast)))
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
