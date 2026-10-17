"""Place map labels once per zoom, so neighbouring tiles agree."""

import math
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from paperroute.build.features import FeatureStore
from paperroute.build.geometry import Line, merge_lines
from paperroute.build.map_style import (
    CITY_PLACES,
    PLACE_KINDS,
    RESERVE_PX,
    RESERVE_ZOOMS,
    ROAD_LAYERS,
    STATION_FACE,
    STATION_ZOOMS,
    STREET_FACE,
    STREET_REPEAT_PX,
    STREET_ZOOMS,
    SUBURB_FACE,
    SUBURB_ZOOMS,
    named_roads,
)
from paperroute.build.tile_set import BLOCK, blocks
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.geo import MetreBox, Point
from paperroute.core.tiles import (
    CITY,
    GRID_ORIGIN,
    TILE,
    LabelKind,
    from_pixels,
    to_pixels,
)
from paperroute.render.canvas import Face, text_width

PixelBox = tuple[float, float, float, float]

BLOCK_PX: Final[int] = BLOCK * TILE
TEXT_MARGIN: Final[int] = 6  # pixels added to the measured text width
STREET_HEIGHT: Final[int] = 14
POINT_HALF_HEIGHT: Final[float] = 0.7  # of the face's pixel size
STRAIGHT: Final[float] = 0.94  # end-to-end distance over text width
STATION_RISE: Final[int] = 15
POINT_REACH: Final[int] = 32  # pixels searched past a block for places
STREET_REACH: Final[int] = 256  # pixels searched past a block for streets
STREET_FIRST: Final[int] = STREET_REPEAT_PX // 2
STREET_OFFSETS: Final[tuple[int, ...]] = (0, 64, -64, 128, -128)
SAME_NAME_PX: Final[int] = 300
POINT_FACES: Final[Mapping[LabelKind, Face]] = {
    LabelKind.STATION: STATION_FACE,
    LabelKind.SUBURB: SUBURB_FACE,
}
INDEX_SCHEMA: Final[str] = """
CREATE VIRTUAL TABLE IF NOT EXISTS label_box USING rtree(id, x0, x1, y0, y1);
CREATE VIRTUAL TABLE IF NOT EXISTS street_near
  USING rtree(id, x0, x1, y0, y1, +name TEXT);
"""
SELECT_OVERLAP: Final[str] = """
SELECT 1 FROM label_box WHERE x0 < ? AND x1 > ? AND y0 < ? AND y1 > ?
LIMIT 1
"""
SELECT_NEAR: Final[str] = """
SELECT 1 FROM street_near
WHERE x0 <= ? AND x1 >= ? AND y0 <= ? AND y1 >= ? AND name = ?
LIMIT 1
"""


class LabelIndex:
    """
    Taken label boxes in a scratch SQLite R*Tree.

    Parameters
    ----------
    conn : sqlite3.Connection
        A scratch database on disk, so memory stays bounded. The index
        adds its own tables.
    """

    def __init__(self, *, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(INDEX_SCHEMA)

    def reset(self) -> None:
        """Forget every box and street name."""
        self.conn.execute('DELETE FROM label_box')
        self.conn.execute('DELETE FROM street_near')

    def claim(self, *, box: PixelBox) -> bool:
        """
        Take a box if it overlaps nothing taken.

        Parameters
        ----------
        box : PixelBox
            Left, top, right and bottom in global pixels.

        Returns
        -------
        bool
            True when the box was free and is now taken.
        """
        left, top, right, bottom = box
        hit = self.conn.execute(
            SELECT_OVERLAP,
            (right, left, bottom, top),
        ).fetchone()
        if hit is not None:
            return False
        self.reserve(box=box)
        return True

    def reserve(self, *, box: PixelBox) -> None:
        """
        Take a box whatever it overlaps.

        Parameters
        ----------
        box : PixelBox
            Left, top, right and bottom in global pixels.
        """
        left, top, right, bottom = box
        self.conn.execute(
            'INSERT INTO label_box (x0, x1, y0, y1) VALUES (?, ?, ?, ?)',
            (left, right, top, bottom),
        )

    def near(self, *, name: str, x: float, y: float) -> bool:
        """
        Say whether a street of this name is already named nearby.

        Parameters
        ----------
        name : str
            Street name.
        x : float
            Centre x in global pixels.
        y : float
            Centre y in global pixels.

        Returns
        -------
        bool
            True when an accepted label of the name has its centre within
            ``SAME_NAME_PX`` either way.
        """
        reach = SAME_NAME_PX
        hit = self.conn.execute(
            SELECT_NEAR,
            (x + reach, x - reach, y + reach, y - reach, name),
        ).fetchone()
        return hit is not None

    def remember(self, *, label: PlacedLabel) -> None:
        """
        Record an accepted street label's centre.

        Parameters
        ----------
        label : PlacedLabel
            The accepted label.
        """
        self.conn.execute(
            'INSERT INTO street_near (x0, x1, y0, y1, name)'
            ' VALUES (?, ?, ?, ?, ?)',
            (label.x, label.x, label.y, label.y, label.name),
        )


@dataclass(frozen=True)
class Block:
    """
    A square of ``BLOCK`` by ``BLOCK`` tiles at one zoom.

    Attributes
    ----------
    mpp : int
        Metres per pixel.
    col : int
        Block column.
    row : int
        Block row.
    """

    mpp: int
    col: int
    row: int

    def holds(self, *, x: float, y: float) -> bool:
        """
        Say whether a global pixel lies in the block.

        Parameters
        ----------
        x : float
            Global pixel x.
        y : float
            Global pixel y.

        Returns
        -------
        bool
            True when inside, counting the top and left edges only.
        """
        left, top = self.col * BLOCK_PX, self.row * BLOCK_PX
        return left <= x < left + BLOCK_PX and top <= y < top + BLOCK_PX

    def metres(self, *, grow: float) -> MetreBox:
        """
        Give the block, grown on every side, in metres.

        Parameters
        ----------
        grow : float
            Pixels to add on each side.

        Returns
        -------
        MetreBox
            West, south, east and north.
        """
        left = self.col * BLOCK_PX - grow
        top = self.row * BLOCK_PX - grow
        west, north = from_pixels(pixel=(left, top), mpp=self.mpp)
        east, south = from_pixels(
            pixel=(left + BLOCK_PX + 2 * grow, top + BLOCK_PX + 2 * grow),
            mpp=self.mpp,
        )
        return (west, south, east, north)


def cumulative(*, line: Line) -> np.ndarray:
    """
    Measure distance along a line at each vertex.

    Parameters
    ----------
    line : Line
        Vertices.

    Returns
    -------
    np.ndarray
        Distance from the first vertex, starting at 0.
    """
    steps = np.hypot(*np.diff(line, axis=0).T)
    return np.concatenate(([0.0], np.cumsum(steps)))


def interpolate(
    *,
    line: Line,
    lengths: np.ndarray,
    distance: float,
) -> tuple[float, float]:
    """
    Find the point a distance along a line.

    Parameters
    ----------
    line : Line
        Vertices.
    lengths : np.ndarray
        Distance along the line at each vertex.
    distance : float
        Distance from the first vertex.

    Returns
    -------
    tuple[float, float]
        The point.
    """
    return (
        float(np.interp(distance, lengths, line[:, 0])),
        float(np.interp(distance, lengths, line[:, 1])),
    )


def upright(*, angle: float) -> float:
    """
    Turn a text angle so the text never reads upside down.

    Parameters
    ----------
    angle : float
        Degrees.

    Returns
    -------
    float
        The same direction modulo 180, in (-90, 90].
    """
    if angle > 90:
        return angle - 180
    if angle <= -90:
        return angle + 180
    return angle


def text_box(
    *,
    centre: Point,
    width: float,
    height: float,
    angle: float,
) -> PixelBox:
    """
    Bound a turned text box.

    Parameters
    ----------
    centre : Point
        Centre in global pixels.
    width : float
        Text width.
    height : float
        Text height.
    angle : float
        Rotation in degrees.

    Returns
    -------
    PixelBox
        Left, top, right and bottom.
    """
    turn = math.radians(angle)
    cos, sin = abs(math.cos(turn)), abs(math.sin(turn))
    half_x = (cos * width + sin * height) / 2
    half_y = (sin * width + cos * height) / 2
    x, y = centre
    return (x - half_x, y - half_y, x + half_x, y + half_y)


def label_box(*, label: PlacedLabel) -> PixelBox:
    """
    Give the collision box of a placed label.

    Parameters
    ----------
    label : PlacedLabel
        The label.

    Returns
    -------
    PixelBox
        Left, top, right and bottom in global pixels. A street's box
        bounds its turned text, ``STREET_HEIGHT`` tall; a station's or
        place's is level and scales with its face.
    """
    if label.kind == LabelKind.STREET:
        height = float(STREET_HEIGHT)
    else:
        height = 2 * POINT_HALF_HEIGHT * POINT_FACES[label.kind].px
    return text_box(
        centre=(label.x, label.y),
        width=label.width,
        height=height,
        angle=label.angle,
    )


def fit_street(
    *,
    line: Line,
    name: str,
    width: float,
    along: float,
    zoom: int,
) -> PlacedLabel | None:
    """
    Fit a street name centred at a distance along a line.

    Parameters
    ----------
    line : Line
        The street in global pixels.
    name : str
        Street name.
    width : float
        Text width with its margin.
    along : float
        Distance of the text centre along the line.
    zoom : int
        Metres per pixel.

    Returns
    -------
    PlacedLabel | None
        The label, or None when the text runs off the line or the
        stretch under it is not straight enough.
    """
    lengths = cumulative(line=line)
    first, last = along - width / 2, along + width / 2
    if first < 0 or last > lengths[-1]:
        return None
    start = interpolate(line=line, lengths=lengths, distance=first)
    end = interpolate(line=line, lengths=lengths, distance=last)
    if math.dist(start, end) < STRAIGHT * width:
        return None
    angle = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    return PlacedLabel(
        name=name,
        x=float(round((start[0] + end[0]) / 2)),
        y=float(round((start[1] + end[1]) / 2)),
        angle=upright(angle=angle),
        width=width,
        start=start,
        end=end,
        zoom=zoom,
        kind=LabelKind.STREET,
    )


def point_label(
    *,
    index: LabelIndex,
    block: Block,
    place: tuple[str, str, int, Point],
    kind: LabelKind,
) -> PlacedLabel | None:
    """
    Name a station above its point, or a place at its point.

    Parameters
    ----------
    index : LabelIndex
        Taken boxes.
    block : Block
        The block being placed.
    place : tuple[str, str, int, Point]
        Kind, name to draw, rank and position in metres.
    kind : LabelKind
        Label kind.

    Returns
    -------
    PlacedLabel | None
        The label, or None when its centre is outside the block or its
        box is taken.
    """
    _, name, rank, point = place
    pixel = to_pixels(point=point, mpp=block.mpp)
    rise = STATION_RISE if kind == LabelKind.STATION else 0
    x, y = float(round(pixel[0])), float(round(pixel[1] - rise))
    if not block.holds(x=x, y=y):
        return None
    label = PlacedLabel(
        name=name,
        x=x,
        y=y,
        angle=0.0,
        width=text_width(text=name, face=POINT_FACES[kind]) + TEXT_MARGIN,
        start=pixel,
        end=pixel,
        zoom=block.mpp,
        kind=kind,
        rank=rank,
    )
    return label if index.claim(box=label_box(label=label)) else None


def station_labels(
    *,
    store: FeatureStore,
    index: LabelIndex,
    block: Block,
) -> Iterator[PlacedLabel]:
    """
    Name the block's stations, full stations first.

    Parameters
    ----------
    store : FeatureStore
        The features.
    index : LabelIndex
        Taken boxes.
    block : Block
        The block being placed.

    Yields
    ------
    PlacedLabel
        Each station label placed.
    """
    if block.mpp not in STATION_ZOOMS:
        return
    stations = [
        place
        for place in store.places_in(box=block.metres(grow=POINT_REACH))
        if place[0] == 'station'
    ]
    for place in sorted(stations, key=lambda station: station[2]):
        label = point_label(
            index=index,
            block=block,
            place=place,
            kind=LabelKind.STATION,
        )
        if label is not None:
            yield label


def place_names(
    *,
    store: FeatureStore,
    index: LabelIndex,
    block: Block,
) -> Iterator[PlacedLabel]:
    """
    Name the block's suburbs and towns in spaced capitals.

    Parameters
    ----------
    store : FeatureStore
        The features.
    index : LabelIndex
        Taken boxes.
    block : Block
        The block being placed.

    Yields
    ------
    PlacedLabel
        Each place label placed.
    """
    if block.mpp not in SUBURB_ZOOMS:
        return
    kinds = CITY_PLACES if block.mpp == CITY else PLACE_KINDS
    for kind, name, rank, point in store.places_in(
        box=block.metres(grow=POINT_REACH),
    ):
        if kind not in kinds:
            continue
        label = point_label(
            index=index,
            block=block,
            place=(kind, ' '.join(name.upper()), rank, point),
            kind=LabelKind.SUBURB,
        )
        if label is not None:
            yield label


def to_global(*, points: Line, mpp: int) -> Line:
    """
    Convert a line from metres to a zoom's global pixels.

    Parameters
    ----------
    points : Line
        Vertices in metres.
    mpp : int
        Metres per pixel.

    Returns
    -------
    Line
        Vertices in global pixels.
    """
    return np.column_stack(
        (
            (points[:, 0] - GRID_ORIGIN[0]) / mpp,
            (GRID_ORIGIN[1] - points[:, 1]) / mpp,
        ),
    )


def named_lines(
    *,
    store: FeatureStore,
    block: Block,
) -> dict[str, tuple[int, list[Line]]]:
    """
    Gather the named streets near a block.

    Parameters
    ----------
    store : FeatureStore
        The features.
    block : Block
        The block being placed.

    Returns
    -------
    dict[str, tuple[int, list[Line]]]
        For each name, its highest road class and its lines in global
        pixels.
    """
    layers = named_roads(mpp=block.mpp)
    streets: dict[str, tuple[int, list[Line]]] = {}
    for layer, name, points in store.lines_in(
        box=block.metres(grow=STREET_REACH),
    ):
        if name and layer in layers:
            rank, lines = streets.get(name, (-1, []))
            lines.append(to_global(points=points, mpp=block.mpp))
            streets[name] = (max(rank, ROAD_LAYERS.index(layer)), lines)
    return streets


def street_pieces(
    *,
    store: FeatureStore,
    block: Block,
) -> list[tuple[str, Line]]:
    """
    Merge the named streets near a block, most important first.

    Parameters
    ----------
    store : FeatureStore
        The features.
    block : Block
        The block being placed.

    Returns
    -------
    list[tuple[str, Line]]
        Name and merged piece, by highest road class, then longest.
    """
    ranked = [
        (rank, float(cumulative(line=piece)[-1]), name, piece)
        for name, (rank, lines) in named_lines(
            store=store,
            block=block,
        ).items()
        for piece in merge_lines(lines=lines)
    ]
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    return [(name, piece) for _, _, name, piece in ranked]


def first_fit(
    *,
    index: LabelIndex,
    block: Block,
    street: tuple[str, Line, float],
    centre: float,
) -> PlacedLabel | None:
    """
    Try a street name around one centre, keeping the first free fit.

    Parameters
    ----------
    index : LabelIndex
        Taken boxes and named streets.
    block : Block
        The block being placed.
    street : tuple[str, Line, float]
        Name, merged piece in global pixels and text width.
    centre : float
        Distance along the piece to try first.

    Returns
    -------
    PlacedLabel | None
        The accepted label, or None.
    """
    name, piece, width = street
    for offset in STREET_OFFSETS:
        fit = fit_street(
            line=piece,
            name=name,
            width=width,
            along=centre + offset,
            zoom=block.mpp,
        )
        if (
            fit is not None
            and block.holds(x=fit.x, y=fit.y)
            and not index.near(name=name, x=fit.x, y=fit.y)
            and index.claim(box=label_box(label=fit))
        ):
            index.remember(label=fit)
            return fit
    return None


def street_labels(
    *,
    store: FeatureStore,
    index: LabelIndex,
    block: Block,
) -> Iterator[PlacedLabel]:
    """
    Name the block's streets every ``STREET_REPEAT_PX`` along them.

    Parameters
    ----------
    store : FeatureStore
        The features.
    index : LabelIndex
        Taken boxes and named streets.
    block : Block
        The block being placed.

    Yields
    ------
    PlacedLabel
        Each street label placed.
    """
    if block.mpp not in STREET_ZOOMS:
        return
    for name, piece in street_pieces(store=store, block=block):
        width = text_width(text=name, face=STREET_FACE) + TEXT_MARGIN
        length = float(cumulative(line=piece)[-1])
        for centre in range(STREET_FIRST, math.ceil(length), STREET_REPEAT_PX):
            label = first_fit(
                index=index,
                block=block,
                street=(name, piece, width),
                centre=centre,
            )
            if label is not None:
                yield label


def reserve_stops(
    *,
    index: LabelIndex,
    stops: Sequence[Point],
    mpp: int,
) -> None:
    """
    Keep labels off a square around every stop.

    Parameters
    ----------
    index : LabelIndex
        Taken boxes.
    stops : Sequence[Point]
        Stops in metres.
    mpp : int
        Metres per pixel.
    """
    half = RESERVE_PX / 2
    for stop in stops:
        x, y = to_pixels(point=stop, mpp=mpp)
        index.reserve(box=(x - half, y - half, x + half, y + half))


def place_labels(
    *,
    store: FeatureStore,
    tiles: Mapping[int, set[tuple[int, int]]],
    stops: Sequence[Point],
    index: LabelIndex,
) -> Iterator[PlacedLabel]:
    """
    Place every label once per zoom, block by block.

    Within a block, stations go first, then places, then streets. A
    label belongs to the block holding its centre.

    Parameters
    ----------
    store : FeatureStore
        The features.
    tiles : Mapping[int, set[tuple[int, int]]]
        Tiles to build, keyed by metres per pixel.
    stops : Sequence[Point]
        Stops in metres. Labels keep off them at ``RESERVE_ZOOMS``.
    index : LabelIndex
        Scratch index, emptied for each zoom.

    Yields
    ------
    PlacedLabel
        Each label placed.
    """
    for mpp, wanted in tiles.items():
        index.reset()
        if mpp in RESERVE_ZOOMS:
            reserve_stops(index=index, stops=stops, mpp=mpp)
        for col, row in blocks(tiles=wanted):
            block = Block(mpp=mpp, col=col, row=row)
            yield from station_labels(store=store, index=index, block=block)
            yield from place_names(store=store, index=index, block=block)
            yield from street_labels(store=store, index=index, block=block)
