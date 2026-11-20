"""Draw the Map's route lines: cased dashes under cased solid strokes."""

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_transit.core.geo import Point, Projection, cut
from paperroute_transit.core.map_line import MapLine
from paperroute_transit.core.map_view import MAP_HEIGHT, MapBase, map_window
from paperroute_transit.render.map_marks import (
    LINE_CASING,
    SOLID_WIDTH,
    dash_pattern,
    dash_width,
)
from paperroute_transit.render.trip_marks import cased_line

type Pixels = NDArray[np.float64]

EDGE_MARGIN: Final[float] = 40.0  # pixels past the map edge
STEP: Final[float] = 2.0  # pixels between resampled points
MERGE: Final[float] = 2.5  # pixels within which a dashed sample is a repeat


def route_pixels(
    *,
    lines: Sequence[NDArray[np.float64]],
    centre: Point,
    mpp: int,
    width: int,
    projection: Projection,
) -> list[NDArray[np.float64]]:
    """
    Convert route lines to map pixels.

    Parameters
    ----------
    lines : Sequence[NDArray[np.float64]]
        Route lines in metres, each n by 2.
    centre : Point
        The map centre in metres.
    mpp : int
        Metres per pixel.
    width : int
        Map width in pixels.
    projection : Projection
        The projection whose pixel grid the map sits on.

    Returns
    -------
    list[NDArray[np.float64]]
        One n by 2 array per line; lines of fewer than two points are
        dropped.
    """
    left, top = map_window(
        centre=centre, mpp=mpp, projection=projection, width=width,
    )
    grid = projection.grid_origin
    return [
        np.column_stack(
            (
                (line[:, 0] - grid[0]) / mpp - left,
                (grid[1] - line[:, 1]) / mpp - top,
            ),
        )
        for line in lines
        if len(line) >= 2
    ]


def points_of(*, line: Pixels) -> list[Point]:
    """
    Turn an n by 2 array into canvas points.

    Parameters
    ----------
    line : Pixels
        Polyline.

    Returns
    -------
    list[Point]
        The vertices.
    """
    return [(float(x), float(y)) for x, y in line]


def on_screen(*, line: Pixels, size: tuple[int, int]) -> list[Pixels]:
    """
    Split a polyline where it runs off the map.

    Parameters
    ----------
    line : Pixels
        Polyline in map pixels.
    size : tuple[int, int]
        Map width and height.

    Returns
    -------
    list[Pixels]
        The runs of segments that do not have both ends beyond the same
        edge of the map plus `EDGE_MARGIN`.
    """
    low = -EDGE_MARGIN
    high = np.array(size, dtype=np.float64) + EDGE_MARGIN
    ends = (line[:-1], line[1:])
    gone = np.any(
        np.logical_and(ends[0] < low, ends[1] < low)
        | np.logical_and(ends[0] > high, ends[1] > high),
        axis=1,
    )
    edges = np.flatnonzero(np.diff(np.concatenate(([0], ~gone, [0]))))
    return [
        line[first : last + 1]
        for first, last in zip(edges[::2], edges[1::2], strict=True)
    ]


def spans(*, line: MapLine) -> list[tuple[bool, tuple[Point, ...]]]:
    """
    Cut a line into its solid and dashed stretches.

    Parameters
    ----------
    line : MapLine
        The line.

    Returns
    -------
    list[tuple[bool, tuple[Point, ...]]]
        Each stretch's points in metres and whether it is solid; a line
        with no solid span is one dashed stretch.
    """
    shape = line.shape
    if line.solid is None:
        return [(False, shape.points)]
    start, end = line.solid
    stretches = (
        (False, 0.0, start),
        (True, start, end),
        (False, end, shape.along[-1]),
    )
    return [
        (solid, cut(shape=shape, start=first, end=last))
        for solid, first, last in stretches
    ]


def in_pixels(
    *, lines: Sequence[Sequence[Point]], base: MapBase, centre: Point,
) -> list[Pixels]:
    """
    Convert lines to map pixels and drop what runs off the map.

    Parameters
    ----------
    lines : Sequence[Sequence[Point]]
        Lines in metres.
    base : MapBase
        The base map key.
    centre : Point
        The map centre in metres.

    Returns
    -------
    list[Pixels]
        The visible runs, in map pixels.
    """
    converted = route_pixels(
        lines=[np.asarray(line, dtype=np.float64) for line in lines],
        centre=centre,
        mpp=base.mpp,
        width=base.width,
        projection=base.projection,
    )
    return [
        run
        for pixels in converted
        for run in on_screen(line=pixels, size=(base.width, MAP_HEIGHT))
    ]


def line_parts(*, base: MapBase) -> tuple[list[Pixels], list[Pixels]]:
    """
    Cut the map's lines into solid and dashed pixel polylines.

    Parameters
    ----------
    base : MapBase
        The base map key.

    Returns
    -------
    tuple[list[Pixels], list[Pixels]]
        The solid polylines, then the dashed ones, in map pixels. Route
        lines are solid; nothing is returned without a centre.
    """
    centre = base.centre
    if centre is None:
        return [], []
    solid = list(base.route)
    dashed: list[Sequence[Point]] = []
    for line in base.lines:
        for is_solid, points in spans(line=line):
            (solid if is_solid else dashed).append(points)
    return (
        in_pixels(lines=solid, base=base, centre=centre),
        in_pixels(lines=dashed, base=base, centre=centre),
    )


def resample(*, line: Pixels) -> Pixels:
    """
    Space a polyline's points `STEP` pixels apart along it.

    Parameters
    ----------
    line : Pixels
        Polyline in map pixels.

    Returns
    -------
    Pixels
        Points every `STEP` along the line, and its end.
    """
    gaps = np.hypot(*np.diff(line, axis=0).T)
    along = np.concatenate(([0.0], np.cumsum(gaps)))
    at = np.arange(0.0, along[-1], STEP)
    if len(at) == 0 or along[-1] - at[-1] > 1e-9:
        at = np.append(at, along[-1])
    return np.column_stack(
        (np.interp(at, along, line[:, 0]), np.interp(at, along, line[:, 1])),
    )


def disc(*, radius: float) -> tuple[Pixels, Pixels]:
    """
    List the whole-pixel offsets within a radius.

    Parameters
    ----------
    radius : float
        Radius in pixels.

    Returns
    -------
    tuple[Pixels, Pixels]
        The row and column offsets.
    """
    reach = int(radius)
    rows, columns = np.mgrid[-reach : reach + 1, -reach : reach + 1]
    inside = rows**2 + columns**2 <= radius**2
    return rows[inside], columns[inside]


def stamp(
    *, mask: NDArray[np.bool_], samples: Pixels, radius: float,
) -> None:
    """
    Set a disc of the mask around each sample.

    Parameters
    ----------
    mask : NDArray[np.bool_]
        The rows by columns mask, changed in place.
    samples : Pixels
        Sample points in map pixels.
    radius : float
        Disc radius in pixels.
    """
    height, width = mask.shape
    columns = np.rint(samples[:, 0]).astype(np.int64)
    rows = np.rint(samples[:, 1]).astype(np.int64)
    for dy, dx in zip(*disc(radius=radius), strict=True):
        y, x = rows + dy, columns + dx
        inside = (y >= 0) & (y < height) & (x >= 0) & (x < width)
        mask[y[inside], x[inside]] = True


def covered(*, mask: NDArray[np.bool_], samples: Pixels) -> NDArray[np.bool_]:
    """
    Look the samples up in a mask.

    Parameters
    ----------
    mask : NDArray[np.bool_]
        The rows by columns mask.
    samples : Pixels
        Sample points in map pixels.

    Returns
    -------
    NDArray[np.bool_]
        True where a sample is on the map and set in the mask.
    """
    height, width = mask.shape
    columns = np.rint(samples[:, 0]).astype(np.int64)
    rows = np.rint(samples[:, 1]).astype(np.int64)
    inside = (rows >= 0) & (rows < height) & (columns >= 0) & (columns < width)
    found = np.zeros(len(samples), dtype=np.bool_)
    found[inside] = mask[rows[inside], columns[inside]]
    return found


def runs_of(*, samples: Pixels, keep: NDArray[np.bool_]) -> list[Pixels]:
    """
    Gather the runs of two or more kept samples in a row.

    Parameters
    ----------
    samples : Pixels
        Sample points.
    keep : NDArray[np.bool_]
        Which samples are kept.

    Returns
    -------
    list[Pixels]
        Each run's samples.
    """
    edges = np.flatnonzero(np.diff(np.concatenate(([0], keep, [0]))))
    return [
        samples[first:last]
        for first, last in zip(edges[::2], edges[1::2], strict=True)
        if last - first >= 2
    ]


def shared_runs(
    *,
    dashed: Sequence[Pixels],
    solid: Sequence[Pixels],
    reach: float,
    size: tuple[int, int],
) -> list[Pixels]:
    """
    Keep each stretch of road's dashes once, and none under a solid line.

    Every line is resampled every `STEP` pixels. A dashed sample is
    dropped within `reach` of a solid sample, or within `MERGE` of an
    earlier kept dashed sample.

    Parameters
    ----------
    dashed : Sequence[Pixels]
        Dashed polylines in map pixels.
    solid : Sequence[Pixels]
        Solid polylines in map pixels.
    reach : float
        How near a solid sample hides a dashed one.
    size : tuple[int, int]
        Map width and height.

    Returns
    -------
    list[Pixels]
        The dashed polylines to draw, each of two or more samples.
    """
    width, height = size
    under = np.zeros((height, width), dtype=np.bool_)
    for line in solid:
        stamp(mask=under, samples=resample(line=line), radius=reach)
    kept = np.zeros((height, width), dtype=np.bool_)
    runs: list[Pixels] = []
    for line in dashed:
        samples = resample(line=line)
        keep = ~(covered(mask=under, samples=samples)
                 | covered(mask=kept, samples=samples))
        stamp(mask=kept, samples=samples[keep], radius=MERGE)
        runs.extend(runs_of(samples=samples, keep=keep))
    return runs


def draw_lines(canvas: Canvas, *, base: MapBase) -> list[Pixels]:
    """
    Draw the dashed lines, then the solid ones over them.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key.

    Returns
    -------
    list[Pixels]
        Every polyline drawn, in map pixels.
    """
    solid, dashed = line_parts(base=base)
    mpp, width = base.mpp, SOLID_WIDTH[base.mpp]
    casing = LINE_CASING[mpp]
    runs = shared_runs(
        dashed=dashed,
        solid=solid,
        reach=width / 2 + casing,
        size=(base.width, MAP_HEIGHT),
    )
    thin = dash_width(mpp=mpp)
    for run in runs:
        cased_line(
            canvas,
            points=points_of(line=run),
            widths=(thin, thin + 2 * casing),
            dash=dash_pattern(mpp=mpp),
        )
    for line in solid:
        canvas.line(
            points=points_of(line=line), width=width + 2 * casing, grey=PAPER,
        )
    for line in solid:
        canvas.line(points=points_of(line=line), width=width, grey=INK)
    return [*solid, *runs]
