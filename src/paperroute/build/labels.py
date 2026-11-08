"""Place map labels once per zoom, so neighbouring tiles agree."""

import math
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.build.map_style import (
    CITY_PLACES,
    PLACE_KINDS,
    ROAD_LAYERS,
    STATION_FACE,
    STREET_FACE,
    STREET_REPEAT_PX,
    STREET_ZOOMS,
    SUBURB_FACE,
    named_roads,
)
from paperroute_device.render.canvas import Face, text_width
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.geometry import Line, merge_lines
from paperroute_transit.build.tile_set import BLOCK, blocks
from paperroute_transit.build.tile_store import PlacedLabel
from paperroute_transit.core.geo import MetreBox, Point, Projection
from paperroute_transit.core.tiles import (
    CITY,
    POINT_HALF_HEIGHT,
    STATION_ZOOMS,
    STREET_HEIGHT,
    SUBURB_ZOOMS,
    TILE,
    LabelKind,
    PixelBox,
    metres_of,
    text_box,
    to_pixels,
)

BLOCK_PX: Final[int] = BLOCK * TILE
TEXT_MARGIN: Final[int] = 6  # pixels added to the measured text width
STRAIGHT: Final[float] = 0.94  # end-to-end distance over text width
STATION_RISE: Final[int] = 15
POINT_REACH: Final[int] = 32  # pixels searched past a block for places
STREET_REACH: Final[int] = 256  # pixels searched past a block for streets
STREET_FIRST: Final[int] = STREET_REPEAT_PX // 2
STREET_OFFSETS: Final[tuple[int, ...]] = (0, 64, -64, 128, -128)
FALLBACK_SHARES: Final[tuple[float, ...]] = (0.5, 0.25, 0.75)
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
            ``SAME_NAME_PX`` either way. The test is a square, so on the
            diagonals it refuses centres up to about 424 px apart.
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
    projection : Projection
        The projection whose pixel grid the block sits on.
    """

    mpp: int
    col: int
    row: int
    projection: Projection

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
        return metres_of(
            box=(
                left,
                top,
                left + BLOCK_PX + 2 * grow,
                top + BLOCK_PX + 2 * grow,
            ),
            mpp=self.mpp,
            projection=self.projection,
        )


@dataclass(frozen=True, eq=False)
class Street:
    """
    A merged street piece in global pixels, measured once.

    Attributes
    ----------
    name : str
        Street name.
    line : Line
        Vertices in global pixels.
    lengths : NDArray[np.float64]
        Distance along the line at each vertex.
    width : float
        Text width with its margin.
    """

    name: str
    line: Line
    lengths: NDArray[np.float64]
    width: float

    @property
    def length(self) -> float:
        """
        Give the piece's length.

        Returns
        -------
        float
            Length in pixels.
        """
        return float(self.lengths[-1])

    @classmethod
    def measure(cls, *, name: str, line: Line) -> Street:
        """
        Measure a piece and its name.

        Parameters
        ----------
        name : str
            Street name.
        line : Line
            Vertices in global pixels.

        Returns
        -------
        Street
            The measured piece.
        """
        return cls(
            name=name,
            line=line,
            lengths=cumulative(line=line),
            width=text_width(text=name, face=STREET_FACE) + TEXT_MARGIN,
        )


def cumulative(*, line: Line) -> NDArray[np.float64]:
    """
    Measure distance along a line at each vertex.

    Parameters
    ----------
    line : Line
        Vertices.

    Returns
    -------
    NDArray[np.float64]
        Distance from the first vertex, starting at 0.
    """
    steps = np.hypot(*np.diff(line, axis=0).T)
    return np.concatenate(([0.0], np.cumsum(steps)))


def interpolate(
    *,
    line: Line,
    lengths: NDArray[np.float64],
    distance: float,
) -> tuple[float, float]:
    """
    Find the point a distance along a line.

    Parameters
    ----------
    line : Line
        Vertices.
    lengths : NDArray[np.float64]
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
    lengths: NDArray[np.float64] | None = None,
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
    lengths : NDArray[np.float64] | None
        The line's `cumulative` lengths, if already measured.

    Returns
    -------
    PlacedLabel | None
        The label, or None when the text runs off the line or the
        stretch under it is not straight enough.
    """
    if lengths is None:
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
    pixel = to_pixels(
        point=point,
        mpp=block.mpp,
        projection=block.projection,
    )
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
    Name the block's suburbs and towns in spaced capitals, by rank.

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
    places = [
        place
        for place in store.places_in(box=block.metres(grow=POINT_REACH))
        if place[0] in kinds
    ]
    for kind, name, rank, point in sorted(places, key=lambda p: p[2]):
        label = point_label(
            index=index,
            block=block,
            place=(kind, ' '.join(name.upper()), rank, point),
            kind=LabelKind.SUBURB,
        )
        if label is not None:
            yield label


def to_global(*, points: Line, mpp: int, projection: Projection) -> Line:
    """
    Convert a line from metres to a zoom's global pixels.

    Parameters
    ----------
    points : Line
        Vertices in metres.
    mpp : int
        Metres per pixel.
    projection : Projection
        The projection whose grid origin is pixel zero.

    Returns
    -------
    Line
        Vertices in global pixels.
    """
    grid = projection.grid_origin
    return np.column_stack(
        (
            (points[:, 0] - grid[0]) / mpp,
            (grid[1] - points[:, 1]) / mpp,
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
            lines.append(
                to_global(
                    points=points,
                    mpp=block.mpp,
                    projection=block.projection,
                ),
            )
            streets[name] = (max(rank, ROAD_LAYERS.index(layer)), lines)
    return streets


def street_pieces(*, store: FeatureStore, block: Block) -> list[Street]:
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
    list[Street]
        Merged pieces, by highest road class, then longest.
    """
    ranked = [
        (rank, Street.measure(name=name, line=piece))
        for name, (rank, lines) in named_lines(
            store=store,
            block=block,
        ).items()
        for piece in merge_lines(lines=lines)
    ]
    ranked.sort(key=lambda item: (-item[0], -item[1].length))
    return [street for _, street in ranked]


def street_fits(
    *,
    street: Street,
    block: Block,
    along: Iterable[float],
) -> list[PlacedLabel]:
    """
    Fit a street's name at each of several distances along it.

    Parameters
    ----------
    street : Street
        The merged piece.
    block : Block
        The block being placed.
    along : Iterable[float]
        Distances of the text centre, in the order to try them.

    Returns
    -------
    list[PlacedLabel]
        The fits that lie on a straight enough stretch, in order.
    """
    fits = (
        fit_street(
            line=street.line,
            name=street.name,
            width=street.width,
            along=distance,
            zoom=block.mpp,
            lengths=street.lengths,
        )
        for distance in along
    )
    return [fit for fit in fits if fit is not None]


def fit_groups(*, street: Street, block: Block) -> list[list[PlacedLabel]]:
    """
    List the fits to try along a street, one group per label.

    Parameters
    ----------
    street : Street
        The merged piece.
    block : Block
        The block being placed.

    Returns
    -------
    list[list[PlacedLabel]]
        For each centre at ``STREET_FIRST`` plus whole steps of
        ``STREET_REPEAT_PX``, its fits at ``STREET_OFFSETS``. A street
        with no fit at any of those gets one group instead: its middle,
        then its quarter points.
    """
    centres = range(STREET_FIRST, math.ceil(street.length), STREET_REPEAT_PX)
    groups = [
        street_fits(
            street=street,
            block=block,
            along=[centre + offset for offset in STREET_OFFSETS],
        )
        for centre in centres
    ]
    if any(groups):
        return groups
    along = [street.length * share for share in FALLBACK_SHARES]
    return [street_fits(street=street, block=block, along=along)]


def accept(
    *,
    index: LabelIndex,
    block: Block,
    fits: Sequence[PlacedLabel],
) -> PlacedLabel | None:
    """
    Keep the first fit that belongs to the block and is free.

    Parameters
    ----------
    index : LabelIndex
        Taken boxes and named streets.
    block : Block
        The block being placed.
    fits : Sequence[PlacedLabel]
        Fits in the order to try them.

    Returns
    -------
    PlacedLabel | None
        The accepted label, or None. A fit is refused when its centre is
        outside the block, a label of the same name is near, or its box
        is taken.
    """
    for fit in fits:
        if (
            block.holds(x=fit.x, y=fit.y)
            and not index.near(name=fit.name, x=fit.x, y=fit.y)
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
    for street in street_pieces(store=store, block=block):
        for fits in fit_groups(street=street, block=block):
            label = accept(index=index, block=block, fits=fits)
            if label is not None:
                yield label


def place_labels(
    *,
    store: FeatureStore,
    tiles: Mapping[int, set[tuple[int, int]]],
    index: LabelIndex,
    projection: Projection,
) -> Iterator[PlacedLabel]:
    """
    Place every label once per zoom, block by block.

    Each zoom takes three passes over its blocks: every station first,
    then every place, then every street, so a label never loses to a
    lesser one placed earlier from a neighbouring block. A label belongs
    to the block holding its centre.

    Parameters
    ----------
    store : FeatureStore
        The features.
    tiles : Mapping[int, set[tuple[int, int]]]
        Tiles to build, keyed by metres per pixel.
    index : LabelIndex
        Scratch index, emptied for each zoom.
    projection : Projection
        The projection whose pixel grid the tiles sit on.

    Yields
    ------
    PlacedLabel
        Each label placed.
    """
    for mpp, wanted in tiles.items():
        index.reset()
        zoom_blocks = [
            Block(mpp=mpp, col=col, row=row, projection=projection)
            for col, row in blocks(tiles=wanted)
        ]
        for placer in (station_labels, place_names, street_labels):
            for block in zoom_blocks:
                yield from placer(store=store, index=index, block=block)
