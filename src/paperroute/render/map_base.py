"""The map base: tiles, the route line, its street names and the stop."""

import math
from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.build.map_style import DARK, STREET_FACE
from paperroute.core.geo import Point
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    map_window,
    screen_point,
)
from paperroute.core.tiles import (
    LABEL_REACH,
    TILE,
    ZOOMS,
    StreetLabel,
    TileVariant,
    tiles_covering,
)
from paperroute.ports import TileRepository
from paperroute.render.canvas import INK, PAPER, Canvas, Face
from paperroute.render.tile_codec import THRESHOLD, diffuse, quantise4

Window = tuple[int, int]
Region = tuple[slice, slice]

ROUTE_STYLE: Final[dict[int, tuple[int, int | None]]] = {
    3: (7, 3),
    5: (7, 3),
    8: (7, 3),
    14: (5, 1),
    45: (3, None),
}
BEAD_DASH: Final[tuple[float, float]] = (2.0, 7.0)
HALO: Final[int] = 2
MATCH_POINTS: Final[int] = 9
MATCH_NEEDED: Final[int] = 6
MATCH_REACH: Final[float] = 5.0  # pixels
MATCH_TURN: Final[float] = 25.0  # degrees
STOP_DISC: Final[float] = 12.0
STOP_RING: Final[float] = 9.0
STOP_RING_WIDTH: Final[float] = 3.0
STOP_DOT: Final[float] = 4.0
SCALE_STEPS: Final[tuple[int, ...]] = (5000, 2000, 1000, 500, 200, 100)
SCALE_REACH: Final[float] = 143.0  # pixels
SCALE_FACE: Final[Face] = Face(weight=400, px=11)
SCALE_BOX_LEFT: Final[float] = 10.0
SCALE_BOX_TOP: Final[float] = 22.0  # pixels above the map's bottom
SCALE_BOX_BOTTOM: Final[float] = 6.0  # pixels above the map's bottom
SCALE_BOX_PAD: Final[float] = 4.0  # paper right of the text
SCALE_BAR_LEFT: Final[float] = 14.0
SCALE_BAR_TOP: Final[float] = 12.0  # pixels above the map's bottom
SCALE_BAR_BOTTOM: Final[float] = 9.0  # pixels above the map's bottom
SCALE_TEXT_GAP: Final[float] = 6.0  # pixels from the bar's end
SCALE_BASELINE: Final[float] = 10.0  # pixels above the map's bottom
CREDIT: Final[str] = '© OpenStreetMap'
CREDIT_FACE: Final[Face] = Face(weight=400, px=11)
EDGE: Final[float] = 6.0  # pixels from the map's edge to the credit
NOT_INSTALLED: Final[str] = 'Map not installed'
NOT_INSTALLED_FACE: Final[Face] = Face(weight=700, px=16)
NOT_INSTALLED_DROP: Final[float] = STOP_DISC + 16.0  # below the centre


def window_of(*, base: MapBase) -> Window:
    """
    Find the global pixel of the map's top-left corner.

    Parameters
    ----------
    base : MapBase
        The base map key.

    Returns
    -------
    Window
        Left and top in whole global pixels.

    Raises
    ------
    ValueError
        When the base has no centre.
    """
    if base.centre is None:
        raise ValueError('a map base without a centre has no window')
    return map_window(centre=base.centre, mpp=base.mpp)


def paste(
    *,
    out: NDArray[np.uint8],
    tile: NDArray[np.uint8],
    at: tuple[int, int],
) -> None:
    """
    Copy the part of a tile that falls inside the map.

    Parameters
    ----------
    out : NDArray[np.uint8]
        The map, changed in place.
    tile : NDArray[np.uint8]
        A whole tile.
    at : tuple[int, int]
        The tile's top-left corner in map pixels; may be negative.
    """
    x, y = at
    height, width = out.shape
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + TILE, width), min(y + TILE, height)
    out[y0:y1, x0:x1] = tile[y0 - y : y1 - y, x0 - x : x1 - x]


def tiles_in(
    *,
    tiles: TileRepository,
    mpp: int,
    window: Window,
    variant: TileVariant,
) -> NDArray[np.uint8]:
    """
    Stitch the stored tiles under a map window.

    Parameters
    ----------
    tiles : TileRepository
        Where the tiles come from.
    mpp : int
        Metres per pixel.
    window : Window
        The map's top-left corner in global pixels.
    variant : TileVariant
        Which rendering to read.

    Returns
    -------
    NDArray[np.uint8]
        ``MAP_HEIGHT`` by ``MAP_WIDTH`` grey levels; missing tiles are
        paper.
    """
    left, top = window
    out = np.full((MAP_HEIGHT, MAP_WIDTH), PAPER, dtype=np.uint8)
    for col, row in tiles_covering(
        left=left,
        top=top,
        right=left + MAP_WIDTH,
        bottom=top + MAP_HEIGHT,
    ):
        tile = tiles.tile(zoom=mpp, col=col, row=row, variant=variant)
        if tile is not None:
            paste(out=out, tile=tile, at=(col * TILE - left, row * TILE - top))
    return out


def compose_tiles(
    *,
    tiles: TileRepository,
    base: MapBase,
    variant: TileVariant,
) -> NDArray[np.uint8]:
    """
    Stitch the stored tiles under the base's map window.

    Parameters
    ----------
    tiles : TileRepository
        Where the tiles come from.
    base : MapBase
        The base map key; it must have a centre.
    variant : TileVariant
        Which rendering to read.

    Returns
    -------
    NDArray[np.uint8]
        ``MAP_HEIGHT`` by ``MAP_WIDTH`` grey levels; missing tiles are
        paper.
    """
    return tiles_in(
        tiles=tiles,
        mpp=base.mpp,
        window=window_of(base=base),
        variant=variant,
    )


def route_pixels(
    *,
    lines: Sequence[Sequence[Point]],
    centre: Point,
    mpp: int,
) -> list[NDArray[np.float64]]:
    """
    Convert route lines to map pixels.

    Parameters
    ----------
    lines : Sequence[Sequence[Point]]
        Route lines in metres.
    centre : Point
        The map centre in metres.
    mpp : int
        Metres per pixel.

    Returns
    -------
    list[NDArray[np.float64]]
        One n by 2 array per line; lines of fewer than two points are
        dropped.
    """
    return [
        np.array(
            [
                screen_point(centre=centre, mpp=mpp, point=point)
                for point in line
            ],
            dtype=np.float64,
        )
        for line in lines
        if len(line) >= 2
    ]


def segments(
    *,
    routes: Sequence[NDArray[np.float64]],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """
    List every segment of the route lines.

    Parameters
    ----------
    routes : Sequence[NDArray[np.float64]]
        Polylines, n by 2.

    Returns
    -------
    tuple[NDArray[np.float64], NDArray[np.float64]]
        Segment starts and ends, each m by 2.
    """
    starts = [line[:-1] for line in routes] or [np.empty((0, 2))]
    ends = [line[1:] for line in routes] or [np.empty((0, 2))]
    return (
        np.concatenate(starts).astype(np.float64),
        np.concatenate(ends).astype(np.float64),
    )


def nearest_segments(
    *,
    points: NDArray[np.float64],
    starts: NDArray[np.float64],
    ends: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """
    Find each point's nearest segment.

    Parameters
    ----------
    points : NDArray[np.float64]
        Points, k by 2.
    starts : NDArray[np.float64]
        Segment starts, m by 2, with m at least 1.
    ends : NDArray[np.float64]
        Segment ends, m by 2.

    Returns
    -------
    tuple[NDArray[np.float64], NDArray[np.float64]]
        Each point's distance to its nearest segment, and that segment's
        direction in degrees, clockwise on screen.
    """
    span = ends - starts
    squared = np.maximum(np.sum(span**2, axis=1), np.finfo(np.float64).tiny)
    rel = points[:, None, :] - starts[None, :, :]
    along = np.clip(np.sum(rel * span[None], axis=2) / squared, 0.0, 1.0)
    gap = np.linalg.norm(rel - along[..., None] * span[None], axis=2)
    nearest = np.argmin(gap, axis=1)
    direction = np.degrees(np.arctan2(span[:, 1], span[:, 0]))
    return gap[np.arange(len(points)), nearest], direction[nearest]


def on_route(
    *,
    label: StreetLabel,
    routes: Sequence[NDArray[np.float64]],
    offset: Window,
) -> bool:
    """
    Decide whether the route runs along a street label's stretch.

    Nine evenly spaced points on the stretch are checked. At least six
    must lie within 5 px of the route, where the route's direction is
    within 25 degrees of the label's angle, either way round. Six rather
    than seven keeps a name that straddles a turn in the route.

    Parameters
    ----------
    label : StreetLabel
        The baked label, in global pixels.
    routes : Sequence[NDArray[np.float64]]
        The drawn route polylines in map pixels, n by 2.
    offset : Window
        The map window's top-left corner in global pixels.

    Returns
    -------
    bool
        True when the label names a street the route runs along.
    """
    starts, ends = segments(routes=routes)
    if starts.size == 0:
        return False
    points = np.linspace(label.start, label.end, num=MATCH_POINTS) - offset
    gap, direction = nearest_segments(points=points, starts=starts, ends=ends)
    turn = np.abs((direction - label.angle + 90.0) % 180.0 - 90.0)
    matched = (gap <= MATCH_REACH) & (turn <= MATCH_TURN)
    return int(np.count_nonzero(matched)) >= MATCH_NEEDED


def as_points(*, line: NDArray[np.float64]) -> list[Point]:
    """
    Turn an n by 2 array into canvas points.

    Parameters
    ----------
    line : NDArray[np.float64]
        Polyline.

    Returns
    -------
    list[Point]
        The vertices.
    """
    return [(float(x), float(y)) for x, y in line]


def draw_route(
    canvas: Canvas,
    *,
    routes: Sequence[NDArray[np.float64]],
    mpp: int,
) -> None:
    """
    Stroke the route in ink, beaded with paper dashes where styled.

    Parameters
    ----------
    canvas : Canvas
        Target.
    routes : Sequence[NDArray[np.float64]]
        Polylines in map pixels.
    mpp : int
        Metres per pixel.
    """
    outer, bead = ROUTE_STYLE[mpp]
    for line in routes:
        points = as_points(line=line)
        canvas.line(points=points, width=outer, grey=INK)
        if bead is not None:
            canvas.line(points=points, width=bead, grey=PAPER, dash=BEAD_DASH)


def draw_route_names(
    canvas: Canvas,
    *,
    labels: Sequence[StreetLabel],
    routes: Sequence[NDArray[np.float64]],
    offset: Window,
) -> list[StreetLabel]:
    """
    Redraw the names of streets the route runs along, over the line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    labels : Sequence[StreetLabel]
        Baked street labels near the window, in global pixels.
    routes : Sequence[NDArray[np.float64]]
        Polylines in map pixels.
    offset : Window
        The map window's top-left corner in global pixels.

    Returns
    -------
    list[StreetLabel]
        The labels redrawn.
    """
    kept = [
        label
        for label in labels
        if on_route(label=label, routes=routes, offset=offset)
    ]
    for label in kept:
        canvas.text(
            at=(label.x - offset[0], label.y - offset[1]),
            text=label.name,
            face=STREET_FACE,
            grey=DARK,
            anchor='mm',
            halo=HALO,
            angle=label.angle,
        )
    return kept


def label_query(*, window: Window) -> tuple[float, float, float, float]:
    """
    Give the box to fetch street labels for, grown by the label reach.

    Parameters
    ----------
    window : Window
        The map's top-left corner in global pixels.

    Returns
    -------
    tuple[float, float, float, float]
        Left, top, right and bottom in global pixels.
    """
    left, top = window
    return (
        left - LABEL_REACH,
        top - LABEL_REACH,
        left + MAP_WIDTH + LABEL_REACH,
        top + MAP_HEIGHT + LABEL_REACH,
    )


def draw_line_and_names(
    canvas: Canvas,
    *,
    base: MapBase,
    centre: Point,
    window: Window,
    tiles: TileRepository,
) -> list[StreetLabel]:
    """
    Draw the route line, then the street names it runs along.

    Parameters
    ----------
    canvas : Canvas
        Target.
    base : MapBase
        The base map key.
    centre : Point
        The map centre in metres.
    window : Window
        The map's top-left corner in global pixels.
    tiles : TileRepository
        Where the street labels come from.

    Returns
    -------
    list[StreetLabel]
        The labels redrawn over the line.
    """
    routes = route_pixels(lines=base.route, centre=centre, mpp=base.mpp)
    draw_route(canvas, routes=routes, mpp=base.mpp)
    labels = tiles.street_labels(
        zoom=base.mpp,
        box=label_query(window=window),
    )
    return draw_route_names(
        canvas,
        labels=labels,
        routes=routes,
        offset=window,
    )


def draw_stop(canvas: Canvas, *, at: Point) -> None:
    """
    Draw the From stop marker: a paper disc, an ink ring and an ink dot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : Point
        The stop's centre on the canvas.
    """
    x, y = at
    canvas.ellipse(
        box=(x - STOP_DISC, y - STOP_DISC, x + STOP_DISC, y + STOP_DISC),
        grey=PAPER,
    )
    canvas.ellipse(
        box=(x - STOP_RING, y - STOP_RING, x + STOP_RING, y + STOP_RING),
        grey=INK,
        stroke=STOP_RING_WIDTH,
    )
    canvas.ellipse(
        box=(x - STOP_DOT, y - STOP_DOT, x + STOP_DOT, y + STOP_DOT),
        grey=INK,
    )


def scale_metres(*, mpp: int) -> int:
    """
    Pick the scale bar's length.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    int
        The largest step that fits in 143 px, else the smallest step.
    """
    return next(
        (metres for metres in SCALE_STEPS if metres / mpp <= SCALE_REACH),
        SCALE_STEPS[-1],
    )


def scale_text(*, mpp: int) -> str:
    """
    Label the scale bar with its length and the zoom step.

    Parameters
    ----------
    mpp : int
        Metres per pixel, one of ``ZOOMS``.

    Returns
    -------
    str
        For example ``1 km · zoom 3 of 5``.
    """
    metres = scale_metres(mpp=mpp)
    length = f'{metres // 1000} km' if metres >= 1000 else f'{metres} m'
    return f'{length} · zoom {ZOOMS.index(mpp) + 1} of {len(ZOOMS)}'


def draw_scale(canvas: Canvas, *, mpp: int) -> None:
    """
    Draw the scale bar and its label at the bottom left, on paper.

    Parameters
    ----------
    canvas : Canvas
        Target.
    mpp : int
        Metres per pixel.
    """
    length = scale_metres(mpp=mpp) / mpp
    text = scale_text(mpp=mpp)
    text_left = SCALE_BAR_LEFT + length + SCALE_TEXT_GAP
    reach = text_left + canvas.length(text=text, face=SCALE_FACE)
    canvas.rect(
        box=(
            SCALE_BOX_LEFT,
            MAP_HEIGHT - SCALE_BOX_TOP,
            reach + SCALE_BOX_PAD,
            MAP_HEIGHT - SCALE_BOX_BOTTOM,
        ),
        grey=PAPER,
    )
    canvas.rect(
        box=(
            SCALE_BAR_LEFT,
            MAP_HEIGHT - SCALE_BAR_TOP,
            SCALE_BAR_LEFT + length,
            MAP_HEIGHT - SCALE_BAR_BOTTOM,
        ),
        grey=INK,
    )
    canvas.text(
        at=(text_left, MAP_HEIGHT - SCALE_BASELINE),
        text=text,
        face=SCALE_FACE,
        grey=INK,
    )


def draw_notes(canvas: Canvas, *, installed: bool) -> None:
    """
    Draw the OSM credit, or a note below the stop when there is no map.

    Parameters
    ----------
    canvas : Canvas
        Target.
    installed : bool
        False when there is no tiles file.
    """
    if installed:
        canvas.text(
            at=(MAP_WIDTH - EDGE, MAP_HEIGHT - EDGE),
            text=CREDIT,
            face=CREDIT_FACE,
            grey=DARK,
            anchor='rs',
            halo=HALO,
        )
        return
    canvas.text(
        at=(MAP_WIDTH / 2, MAP_HEIGHT / 2 + NOT_INSTALLED_DROP),
        text=NOT_INSTALLED,
        face=NOT_INSTALLED_FACE,
        grey=INK,
        anchor='mm',
        halo=HALO,
    )


def name_region(*, label: StreetLabel, offset: Window) -> Region:
    """
    Find the map pixels a redrawn name and its halo may cover.

    Parameters
    ----------
    label : StreetLabel
        The label, in global pixels.
    offset : Window
        The map window's top-left corner in global pixels.

    Returns
    -------
    Region
        Row and column slices, clipped to the map.
    """
    half = math.hypot(label.width / 2, STREET_FACE.px) + HALO
    x, y = label.x - offset[0], label.y - offset[1]
    return (
        slice(max(0, math.floor(y - half)), max(0, math.ceil(y + half))),
        slice(max(0, math.floor(x - half)), max(0, math.ceil(x + half))),
    )


def finish(
    *,
    grey: NDArray[np.uint8],
    variant: TileVariant,
    names: Sequence[Region] = (),
) -> NDArray[np.uint8]:
    """
    Snap the drawn base to the variant's levels.

    For BITS, the redrawn names are dithered like the baked ones before
    everything else is thresholded.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        The drawn base.
    variant : TileVariant
        GREY keeps four levels; BITS keeps ink and paper.
    names : Sequence[Region]
        Where the redrawn names are.

    Returns
    -------
    NDArray[np.uint8]
        The finished base.
    """
    if variant == TileVariant.GREY:
        return quantise4(grey=grey)
    out = grey.copy()
    for region in names:
        if out[region].size:
            out[region] = np.where(diffuse(grey=out[region]), PAPER, INK)
    return np.where(out < THRESHOLD, INK, PAPER).astype(np.uint8)


def draw_map_base(
    *,
    base: MapBase,
    tiles: TileRepository,
    variant: TileVariant,
) -> NDArray[np.uint8]:
    """
    Draw the map under the markers: tiles, route, names, stop and notes.

    Parameters
    ----------
    base : MapBase
        The base map key.
    tiles : TileRepository
        Where the tiles and street labels come from.
    variant : TileVariant
        Which rendering to read and the levels to finish to.

    Returns
    -------
    NDArray[np.uint8]
        ``MAP_HEIGHT`` by ``MAP_WIDTH`` grey levels; plain paper when the
        stop has no location.
    """
    if base.centre is None:
        return np.full((MAP_HEIGHT, MAP_WIDTH), PAPER, dtype=np.uint8)
    window = window_of(base=base)
    canvas = Canvas(width=MAP_WIDTH, height=MAP_HEIGHT)
    canvas.image(
        grey=tiles_in(
            tiles=tiles,
            mpp=base.mpp,
            window=window,
            variant=variant,
        ),
        at=(0, 0),
    )
    names = draw_line_and_names(
        canvas,
        base=base,
        centre=base.centre,
        window=window,
        tiles=tiles,
    )
    draw_stop(
        canvas,
        at=screen_point(centre=base.centre, mpp=base.mpp, point=base.centre),
    )
    draw_scale(canvas, mpp=base.mpp)
    draw_notes(canvas, installed=tiles.version() is not None)
    return finish(
        grey=canvas.grey(),
        variant=variant,
        names=[name_region(label=label, offset=window) for label in names],
    )
