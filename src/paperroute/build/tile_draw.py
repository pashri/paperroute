"""Draw map tiles from the scratch features and the placed labels."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.build.features import FeatureStore, envelope
from paperroute.build.geometry import Line, Ring, clip_lines, clip_rings
from paperroute.build.map_style import (
    AREA_GREYS,
    DARK,
    RIVER_STYLE,
    ROAD_LAYERS,
    STATION_FACE,
    STREET_FACE,
    SUBURB_FACE,
    SUPERSAMPLE,
    TRANSIT_LAYERS,
    road_style,
    transit_style,
)
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.geo import MetreBox
from paperroute.core.tiles import (
    GRID_ORIGIN,
    LABEL_REACH,
    TILE,
    LabelKind,
    from_pixels,
)
from paperroute.render.canvas import INK, PAPER, Canvas, Point
from paperroute.render.tile_codec import (
    diffuse,
    pack_bits,
    pack_grey,
    quantise4,
)

GEOMETRY_MARGIN: Final[int] = 16  # pixels of geometry kept past the edge
SMALLEST_AREA: Final[int] = 3  # pixels; smaller bounds are not filled
HALO: Final[int] = 2
SQUARE_HALF: Final[tuple[int, int]] = (5, 3)  # rank 0, other ranks


@dataclass(frozen=True)
class Window:
    """
    A rectangle of a zoom's global pixel grid.

    Attributes
    ----------
    mpp : int
        Metres per pixel.
    left : int
        Left edge in global pixels.
    top : int
        Top edge in global pixels.
    width : int
        Width in pixels.
    height : int
        Height in pixels.
    """

    mpp: int
    left: int
    top: int
    width: int
    height: int

    def pixels(self, *, grow: int) -> tuple[float, float, float, float]:
        """
        Give the window's edges, grown on every side.

        Parameters
        ----------
        grow : int
            Pixels added on each side.

        Returns
        -------
        tuple[float, float, float, float]
            Left, top, right and bottom in global pixels.
        """
        return (
            float(self.left - grow),
            float(self.top - grow),
            float(self.left + self.width + grow),
            float(self.top + self.height + grow),
        )

    def metres(self, *, grow: int) -> MetreBox:
        """
        Give the window, grown on every side, in metres.

        Parameters
        ----------
        grow : int
            Pixels added on each side.

        Returns
        -------
        MetreBox
            West, south, east and north.
        """
        left, top, right, bottom = self.pixels(grow=grow)
        west, north = from_pixels(pixel=(left, top), mpp=self.mpp)
        east, south = from_pixels(pixel=(right, bottom), mpp=self.mpp)
        return (west, south, east, north)

    def local(self, *, points: Line) -> list[Point]:
        """
        Convert vertices in metres to pixels inside the window.

        Parameters
        ----------
        points : Line
            Vertices in metres.

        Returns
        -------
        list[Point]
            Each vertex as x and y from the window's top left corner.
        """
        xs = (points[:, 0] - GRID_ORIGIN[0]) / self.mpp - self.left
        ys = (GRID_ORIGIN[1] - points[:, 1]) / self.mpp - self.top
        return list(zip(xs.tolist(), ys.tolist(), strict=True))


def big_enough(*, rings: Sequence[Ring], mpp: int) -> bool:
    """
    Decide whether an area's bounds are worth filling at a zoom.

    Parameters
    ----------
    rings : Sequence[Ring]
        The whole area's rings in metres.
    mpp : int
        Metres per pixel.

    Returns
    -------
    bool
        True when the bounds cover more than ``SMALLEST_AREA`` squared
        pixels.
    """
    west, east, south, north = envelope(rings=rings)
    return (east - west) * (north - south) > (SMALLEST_AREA * mpp) ** 2


def draw_areas(*, canvas: Canvas, store: FeatureStore, window: Window) -> None:
    """
    Fill parks, then water and sea.

    Parameters
    ----------
    canvas : Canvas
        Target, the window's size.
    store : FeatureStore
        The features.
    window : Window
        The window drawn.
    """
    box = window.metres(grow=GEOMETRY_MARGIN)
    areas = [
        (layer, rings)
        for layer, rings in store.areas_in(box=box)
        if layer in AREA_GREYS and big_enough(rings=rings, mpp=window.mpp)
    ]
    for layer, rings in sorted(areas, key=lambda area: area[0] != 'park'):
        canvas.rings(
            rings=[
                window.local(points=ring)
                for ring in clip_rings(rings=rings, box=box)
            ],
            grey=AREA_GREYS[layer],
        )


def stroke_all(
    *,
    canvas: Canvas,
    window: Window,
    lines: Sequence[Line],
    stroke: tuple[float, int, tuple[float, float] | None],
) -> None:
    """
    Stroke lines alike.

    Parameters
    ----------
    canvas : Canvas
        Target.
    window : Window
        The window drawn.
    lines : Sequence[Line]
        Lines in metres.
    stroke : tuple[float, int, tuple[float, float] | None]
        Width, grey and dash pattern.
    """
    width, grey, dash = stroke
    for line in lines:
        canvas.line(
            points=window.local(points=line),
            width=width,
            grey=grey,
            dash=dash,
        )


def by_layer(*, store: FeatureStore, box: MetreBox) -> dict[str, list[Line]]:
    """
    Find the lines meeting a box, grouped by layer.

    Parameters
    ----------
    store : FeatureStore
        The features.
    box : MetreBox
        West, south, east and north.

    Returns
    -------
    dict[str, list[Line]]
        Each layer's lines in metres, in the order they were added.
    """
    layers: defaultdict[str, list[Line]] = defaultdict(list)
    for layer, _, points in store.lines_in(box=box):
        layers[layer].append(points)
    return layers


def draw_roads(
    *,
    canvas: Canvas,
    window: Window,
    layers: dict[str, list[Line]],
) -> None:
    """
    Stroke rivers, then roads from minor to motorway.

    Parameters
    ----------
    canvas : Canvas
        Target, the window's size.
    window : Window
        The window drawn.
    layers : dict[str, list[Line]]
        Lines in metres by layer.
    """
    box = window.metres(grow=GEOMETRY_MARGIN)
    width, grey = RIVER_STYLE
    styles = [('river', (width, grey))] + [
        (layer, road_style(layer=layer, mpp=window.mpp))
        for layer in ROAD_LAYERS
    ]
    for layer, (width, grey) in styles:
        if width and layer in layers:
            stroke_all(
                canvas=canvas,
                window=window,
                lines=clip_lines(lines=layers[layer], box=box),
                stroke=(width, grey, None),
            )


def draw_lines(*, canvas: Canvas, store: FeatureStore, window: Window) -> None:
    """
    Stroke rivers, then roads by class, then transit in a fixed order.

    Transit is drawn from whole lines, not clipped ones, so a dashed
    line's dashes start at the same place in every tile.

    Parameters
    ----------
    canvas : Canvas
        Target, the window's size.
    store : FeatureStore
        The features.
    window : Window
        The window drawn.
    """
    layers = by_layer(store=store, box=window.metres(grow=GEOMETRY_MARGIN))
    draw_roads(canvas=canvas, window=window, layers=layers)
    for layer in TRANSIT_LAYERS:
        for stroke in transit_style(layer=layer, mpp=window.mpp):
            stroke_all(
                canvas=canvas,
                window=window,
                lines=layers.get(layer, []),
                stroke=stroke,
            )


def draw_station(*, canvas: Canvas, label: PlacedLabel, at: Point) -> None:
    """
    Mark a station's point with a square and name it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    label : PlacedLabel
        The station label; ``start`` is the station's point.
    at : Point
        The window's top left corner in global pixels.
    """
    half = SQUARE_HALF[0] if label.rank == 0 else SQUARE_HALF[1]
    x, y = label.start[0] - at[0], label.start[1] - at[1]
    canvas.rect(
        box=(x - half - 1, y - half - 1, x + half + 1, y + half + 1),
        grey=PAPER,
    )
    canvas.rect(box=(x - half, y - half, x + half, y + half), grey=INK)
    canvas.text(
        at=(label.x - at[0], label.y - at[1]),
        text=label.name,
        face=STATION_FACE,
        grey=INK,
        anchor='mm',
        halo=HALO,
    )


def draw_name(*, canvas: Canvas, label: PlacedLabel, at: Point) -> None:
    """
    Draw a suburb or street name, turned to its angle.

    Parameters
    ----------
    canvas : Canvas
        Target.
    label : PlacedLabel
        The label.
    at : Point
        The window's top left corner in global pixels.
    """
    face = SUBURB_FACE if label.kind == LabelKind.SUBURB else STREET_FACE
    canvas.text(
        at=(label.x - at[0], label.y - at[1]),
        text=label.name,
        face=face,
        grey=DARK if label.kind == LabelKind.SUBURB else INK,
        anchor='mm',
        halo=HALO,
        angle=label.angle,
    )


def fits_reach(*, label: PlacedLabel) -> bool:
    """
    Decide whether a label is narrow enough to draw in every tile it meets.

    A tile only finds labels centred within ``LABEL_REACH`` of it, so a
    label whose text and halo reach further would be cut at a tile edge.
    Such labels are drawn nowhere. With the street face that takes a
    name of about 80 characters. A label's width includes a few pixels
    of margin, which covers the extra pixel a smoothed edge can shade.

    Parameters
    ----------
    label : PlacedLabel
        The label.

    Returns
    -------
    bool
        True when half the label's width plus its halo is within reach.
    """
    return label.width / 2 + HALO <= LABEL_REACH


def draw_labels(
    *,
    canvas: Canvas,
    store: FeatureStore,
    window: Window,
) -> None:
    """
    Draw every label near the window: stations, suburbs, then streets.

    Parameters
    ----------
    canvas : Canvas
        Target, the window's size.
    store : FeatureStore
        The features and placed labels.
    window : Window
        The window drawn.
    """
    labels = store.labels_in(
        zoom=window.mpp,
        box=window.pixels(grow=LABEL_REACH),
    )
    labels = [label for label in labels if fits_reach(label=label)]
    at = (float(window.left), float(window.top))
    for label in labels:
        if label.kind == LabelKind.STATION:
            draw_station(canvas=canvas, label=label, at=at)
    for kind in (LabelKind.SUBURB, LabelKind.STREET):
        for label in labels:
            if label.kind == kind:
                draw_name(canvas=canvas, label=label, at=at)


def draw_window(*, store: FeatureStore, window: Window) -> NDArray[np.uint8]:
    """
    Draw a window of the map in the four grey levels.

    Everything is drawn smoothly at ``SUPERSAMPLE`` times the size, then
    each block of sub-pixels is averaged to one pixel and snapped to the
    nearest level.

    Parameters
    ----------
    store : FeatureStore
        The features and placed labels.
    window : Window
        The window to draw.

    Returns
    -------
    NDArray[np.uint8]
        Height by width pixels, each 0, 85, 170 or 255.
    """
    canvas = Canvas(
        width=window.width,
        height=window.height,
        scale=SUPERSAMPLE,
        smooth=True,
    )
    draw_areas(canvas=canvas, store=store, window=window)
    draw_lines(canvas=canvas, store=store, window=window)
    draw_labels(canvas=canvas, store=store, window=window)
    return quantise4(grey=canvas.grey())


def render_tile(
    *,
    store: FeatureStore,
    mpp: int,
    col: int,
    row: int,
) -> tuple[bytes, bytes]:
    """
    Draw one tile and pack both of its renderings.

    Parameters
    ----------
    store : FeatureStore
        The features and placed labels.
    mpp : int
        Metres per pixel.
    col : int
        Tile column.
    row : int
        Tile row.

    Returns
    -------
    tuple[bytes, bytes]
        The packed grey tile and the packed dithered tile.
    """
    grey = draw_window(
        store=store,
        window=Window(
            mpp=mpp,
            left=col * TILE,
            top=row * TILE,
            width=TILE,
            height=TILE,
        ),
    )
    return pack_grey(grey=grey), pack_bits(paper=diffuse(grey=grey))
