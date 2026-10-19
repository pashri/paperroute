"""Draw strip rows, the From band and the strip's vehicles in ink only."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from paperroute.core.map_view import Marker
from paperroute.core.strip_model import INDENT, ROW_H, Band, StripRow
from paperroute.render.canvas import INK, PAPER, Canvas, Face
from paperroute.render.markers import draw_hollow, draw_live

TRACK_WIDTH: Final[int] = 3
RIDE_WIDTH: Final[int] = 9
RING_RADIUS: Final[float] = 6.0
RING_STROKE: Final[float] = 3.0
DISC_RADIUS: Final[float] = 11.0
MAIN_PX: Final[int] = 15
SPUR_PX: Final[int] = 13
TIME_FACE: Final[Face] = Face(weight=400, px=15)
TAG_FACE: Final[Face] = Face(weight=700, px=13)
TAG_HALF: Final[float] = 10.0  # pixels above and below the row centre
TAG_PAD: Final[float] = 12.0  # pixels around the tag text in all
TAG_MIN: Final[float] = 44.0
TAG_GAP: Final[float] = 8.0  # pixels between a name and a tag
NAME_ROOM: Final[int] = 40  # pixels kept clear left of the time column
BASELINE_DROP: Final[float] = 5.0  # text baseline below the row centre
CURSOR_INSET: Final[float] = 8.0  # pixels left of the name
CURSOR_EDGE: Final[float] = 4.0  # pixels short of the right edge
CURSOR_HALF: Final[float] = 16.0  # pixels above and below the row centre
BAND_FACE: Final[Face] = Face(weight=700, px=13)
BAND_INSET: Final[float] = 3.0  # pixels inside the row slot
BAND_ARROW: Final[float] = 14.0  # arrow x from the band's left
BAND_TEXT: Final[float] = 30.0  # text x from the band's left


@dataclass(frozen=True)
class Columns:
    """
    Where a strip's parts sit across the screen.

    Attributes
    ----------
    track_x : int
        Track x at depth 0.
    name_x : int
        Name x at depth 0.
    time_right : int
        Right edge of the times and tags.
    right : int
        Right edge of the strip.
    """

    track_x: int
    name_x: int
    time_right: int
    right: int


def draw_track(canvas: Canvas, *, row: StripRow, track_x: int) -> None:
    """
    Draw the track from a row's parent to the row.

    A change of depth bends half a row from the parent, then runs
    straight down or up to the row.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row; nothing is drawn when it has no parent.
    track_x : int
        Track x at depth 0.
    """
    if row.parent_y is None:
        return
    start = (track_x + INDENT * row.parent_depth, row.parent_y)
    x = track_x + INDENT * row.depth
    bend = math.copysign(ROW_H / 2, row.y - row.parent_y)
    elbow = [] if start[0] == x else [(x, row.parent_y + bend)]
    canvas.line(
        points=[start, *elbow, (x, row.y)],
        width=RIDE_WIDTH if row.ride else TRACK_WIDTH,
        grey=INK,
    )


def draw_station(canvas: Canvas, *, row: StripRow, x: float) -> None:
    """
    Draw a row's station: a disc for the From and Destination, else a ring.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row.
    x : float
        Track x at the row's depth.
    """
    if row.bold:
        canvas.ellipse(
            box=(x - DISC_RADIUS, row.y - DISC_RADIUS,
                 x + DISC_RADIUS, row.y + DISC_RADIUS),
            grey=INK,
        )
        return
    box = (x - RING_RADIUS, row.y - RING_RADIUS,
           x + RING_RADIUS, row.y + RING_RADIUS)
    canvas.ellipse(box=box, grey=PAPER)
    canvas.ellipse(box=box, grey=INK, stroke=RING_STROKE)


def draw_tag(canvas: Canvas, *, row: StripRow, right: float) -> float:
    """
    Draw a row's tag box ending at an edge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row; its tag is inverted, or paper with ink text on the
        cursor row.
    right : float
        Right edge.

    Returns
    -------
    float
        Left edge of the box.
    """
    width = max(canvas.length(text=row.tag, face=TAG_FACE) + TAG_PAD,
                TAG_MIN)
    left = right - width
    canvas.rect(
        box=(left, row.y - TAG_HALF, right, row.y + TAG_HALF),
        grey=PAPER if row.cursor else INK,
    )
    canvas.text(
        at=((left + right) / 2, row.y + 1),
        text=row.tag,
        face=TAG_FACE,
        grey=INK if row.cursor else PAPER,
        anchor='mm',
    )
    return left


def name_face(*, row: StripRow) -> Face:
    """
    Choose a row's name face.

    Parameters
    ----------
    row : StripRow
        The row.

    Returns
    -------
    Face
        15 px on the main line and 13 px on spurs; bold for the From,
        the Destination and the cursor row.
    """
    return Face(
        weight=700 if row.bold or row.cursor else 400,
        px=MAIN_PX if row.depth == 0 else SPUR_PX,
    )


def draw_end(canvas: Canvas, *, row: StripRow, columns: Columns) -> float:
    """
    Draw a row's tag, or its time when it has no tag.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row.
    columns : Columns
        Where the strip's parts sit.

    Returns
    -------
    float
        Where the name must end.
    """
    edge = columns.time_right - NAME_ROOM
    if row.tag:
        left = draw_tag(canvas, row=row, right=columns.time_right)
        return min(edge, left - TAG_GAP)
    if row.time:
        canvas.text(
            at=(columns.time_right, row.y + BASELINE_DROP),
            text=row.time,
            face=TIME_FACE,
            grey=PAPER if row.cursor else INK,
            anchor='rs',
        )
    return edge


def draw_row(canvas: Canvas, *, row: StripRow, columns: Columns) -> None:
    """
    Draw one row's cursor bar, station, name, time and tag.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row.
    columns : Columns
        Where the strip's parts sit.
    """
    left = columns.name_x + INDENT * row.depth
    if row.cursor:
        canvas.rect(
            box=(left - CURSOR_INSET, row.y - CURSOR_HALF,
                 columns.right - CURSOR_EDGE, row.y + CURSOR_HALF),
            grey=INK,
        )
    draw_station(canvas, row=row, x=columns.track_x + INDENT * row.depth)
    edge = draw_end(canvas, row=row, columns=columns)
    face = name_face(row=row)
    canvas.text(
        at=(left, row.y + BASELINE_DROP),
        text=canvas.fit(text=row.name, face=face, width=edge - left),
        face=face,
        grey=PAPER if row.cursor else INK,
    )


def draw_rows(
    canvas: Canvas,
    *,
    rows: Sequence[StripRow],
    track_x: int,
    name_x: int,
    time_right: int,
    right: int,
) -> None:
    """
    Draw a strip's track, then each row over it.

    Parameters
    ----------
    canvas : Canvas
        Target; the caller clips it to the strip.
    rows : Sequence[StripRow]
        Visible rows.
    track_x : int
        Track x at depth 0; each depth adds `INDENT`.
    name_x : int
        Name x at depth 0; each depth adds `INDENT`.
    time_right : int
        Right edge of the times and tags.
    right : int
        Right edge of the strip.
    """
    columns = Columns(
        track_x=track_x, name_x=name_x, time_right=time_right, right=right,
    )
    for row in rows:
        draw_track(canvas, row=row, track_x=track_x)
    for row in rows:
        draw_row(canvas, row=row, columns=columns)


def upright_arrow(canvas: Canvas, *, x: float, y: float, up: bool) -> None:
    """
    Draw an up or down arrow as shapes.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Centre x.
    y : float
        Centre y.
    up : bool
        Point up when True, down when False.
    """
    sign = -1 if up else 1
    canvas.line(
        points=[(x, y - sign * 7), (x, y + sign * 2)], width=2, grey=PAPER,
    )
    canvas.polygon(
        points=[(x, y + sign * 8), (x - 5, y + sign * 2),
                (x + 5, y + sign * 2)],
        grey=PAPER,
    )


def draw_band(canvas: Canvas, *, band: Band, left: int, right: int) -> None:
    """
    Draw the From band in the top or bottom row slot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    band : Band
        Text and slot.
    left : int
        Left edge.
    right : int
        Right edge.
    """
    top = band.y
    canvas.rect(
        box=(left, top + BAND_INSET, right, top + ROW_H - BAND_INSET),
        grey=INK,
    )
    middle = top + ROW_H / 2
    upright_arrow(canvas, x=left + BAND_ARROW, y=middle, up=band.top)
    canvas.text(
        at=(left + BAND_TEXT, middle + BASELINE_DROP),
        text=canvas.fit(
            text=band.text,
            face=BAND_FACE,
            width=right - left - BAND_TEXT - CURSOR_INSET,
        ),
        face=BAND_FACE,
        grey=PAPER,
    )


def draw_strip_markers(canvas: Canvas, *, markers: Sequence[Marker]) -> None:
    """
    Draw the strip's vehicles where they stand, live ones on top.

    Parameters
    ----------
    canvas : Canvas
        Target.
    markers : Sequence[Marker]
        Badges in screen pixels, already pushed apart.
    """
    for marker in sorted(markers, key=lambda each: each.live):
        draw = draw_live if marker.live else draw_hollow
        draw(canvas, marker=marker, centre=(marker.x, marker.y))
