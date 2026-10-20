"""Draw strip rows, the From band and the strip's vehicles in ink only."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from paperroute.core.map_view import Marker
from paperroute.core.reach import RowKind
from paperroute.core.strip_model import INDENT, ROW_H, Band, StripRow
from paperroute.render.canvas import INK, PAPER, Canvas, Face
from paperroute.render.chrome import ARROW_WIDTH, arrow
from paperroute.render.markers import draw_hollow, draw_live

TRACK_WIDTH: Final[int] = 3
RIDE_WIDTH: Final[int] = 9
RING_RADIUS: Final[float] = 6.0
RING_STROKE: Final[float] = 3.0
DISC_RADIUS: Final[float] = 11.0
FOLD_RINGS: Final[tuple[float, ...]] = (9.0, 4.0)  # a fold's ring radii
FOLD_STROKE: Final[float] = 2.0
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
EXIT_FACE: Final[Face] = Face(weight=700, px=13)
EXIT_ARROW: Final[float] = 16.0  # an exit arrow's width, gap included
NOTE_FACE: Final[Face] = Face(weight=700, px=12)
NOTE_MAX: Final[float] = 190.0  # a carry-on note box's widest
NOTE_OUTLINE: Final[float] = 2.0
NOTE_PAD: Final[float] = 6.0  # pixels inside each end of the note box
NOTE_GAP: Final[float] = 4.0  # pixels between the route and the arrow


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


def track_points(*, row: StripRow, parent_y: float, track_x: int
                 ) -> list[tuple[float, float]]:
    """
    Give the points of the track from a row's parent to the row.

    A change of depth bends half a row from the parent, then runs
    straight down or up to the row. An exit's stub runs down or up its
    junction's track to a quarter row short of the row, then
    diagonally one indent right to the row.

    Parameters
    ----------
    row : StripRow
        The row.
    parent_y : float
        Its parent's centre line.
    track_x : int
        Track x at depth 0.

    Returns
    -------
    list[tuple[float, float]]
        Points from the parent to the row.
    """
    start = (track_x + INDENT * row.parent_depth, parent_y)
    x = track_x + INDENT * row.depth
    if row.kind is RowKind.EXIT:
        knee = row.y - math.copysign(ROW_H / 4, row.y - parent_y)
        return [start, (x, knee), (x + INDENT, row.y)]
    bend = math.copysign(ROW_H / 2, row.y - parent_y)
    elbow = [] if start[0] == x else [(x, parent_y + bend)]
    return [start, *elbow, (x, row.y)]


def draw_track(canvas: Canvas, *, row: StripRow, track_x: int) -> None:
    """
    Draw the track from a row's parent to the row (see `track_points`).

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
    canvas.line(
        points=track_points(
            row=row, parent_y=row.parent_y, track_x=track_x,
        ),
        width=RIDE_WIDTH if row.ride else TRACK_WIDTH,
        grey=INK,
    )


def draw_station(canvas: Canvas, *, row: StripRow, x: float) -> None:
    """
    Draw a row's station mark.

    The From and Destination get a disc, a closed fold two rings, an
    exit nothing, and any other station a ring.

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
    if row.kind is RowKind.FOLD:
        draw_fold(canvas, y=row.y, x=x)
        return
    if row.kind is RowKind.EXIT:
        return
    box = (x - RING_RADIUS, row.y - RING_RADIUS,
           x + RING_RADIUS, row.y + RING_RADIUS)
    canvas.ellipse(box=box, grey=PAPER)
    canvas.ellipse(box=box, grey=INK, stroke=RING_STROKE)


def draw_fold(canvas: Canvas, *, y: float, x: float) -> None:
    """
    Draw a closed fold's mark: two hollow rings, one inside the other.

    Parameters
    ----------
    canvas : Canvas
        Target.
    y : float
        Row centre.
    x : float
        Track x at the row's depth.
    """
    outer = FOLD_RINGS[0] + FOLD_STROKE
    canvas.ellipse(
        box=(x - outer, y - outer, x + outer, y + outer), grey=PAPER,
    )
    for radius in FOLD_RINGS:
        canvas.ellipse(
            box=(x - radius, y - radius, x + radius, y + radius),
            grey=INK,
            stroke=FOLD_STROKE,
        )


def draw_tag(canvas: Canvas, *, row: StripRow, right: float) -> float:
    """
    Draw a cursor row's tag as a paper box ending at an edge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The cursor row; its tag is ink on paper over the ink bar.
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
        box=(left, row.y - TAG_HALF, right, row.y + TAG_HALF), grey=PAPER,
    )
    canvas.text(
        at=((left + right) / 2, row.y + 1),
        text=row.tag,
        face=TAG_FACE,
        grey=INK,
        anchor='mm',
    )
    return left


def draw_note(canvas: Canvas, *, row: StripRow, right: float,
              floor: float) -> float:
    """
    Draw a row's carry-on note as an outlined box ending at an edge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row; drawn only when it has a note.
    right : float
        Right edge of the box.
    floor : float
        Furthest left the box may start.

    Returns
    -------
    float
        Left edge of the box; right when nothing is drawn.
    """
    note = row.note
    if note is None:
        return right
    route = canvas.length(text=note.route, face=NOTE_FACE)
    fixed = 2 * NOTE_PAD + route + NOTE_GAP + ARROW_WIDTH
    room = min(NOTE_MAX, right - floor) - fixed
    then = canvas.fit(text=note.then, face=NOTE_FACE, width=room)
    if room <= 0 or not then:
        return right
    left = right - fixed - canvas.length(text=then, face=NOTE_FACE)
    top, bottom = row.y - TAG_HALF, row.y + TAG_HALF
    canvas.rect(box=(left, top, right, bottom), grey=INK)
    canvas.rect(
        box=(left + NOTE_OUTLINE, top + NOTE_OUTLINE,
             right - NOTE_OUTLINE, bottom - NOTE_OUTLINE),
        grey=PAPER,
    )
    note_text(
        canvas, texts=(note.route, then), x=left + NOTE_PAD, y=row.y + 1,
    )
    return left


def note_text(canvas: Canvas, *, texts: tuple[str, str], x: float,
              y: float) -> None:
    """
    Draw a note's route, an arrow and what it carries on as.

    Parameters
    ----------
    canvas : Canvas
        Target.
    texts : tuple[str, str]
        The route, and the route and headsign it carries on as.
    x : float
        Left end.
    y : float
        Centre line.
    """
    route, then = texts
    canvas.text(at=(x, y), text=route, face=NOTE_FACE, grey=INK, anchor='lm')
    x += canvas.length(text=route, face=NOTE_FACE) + NOTE_GAP
    x += arrow(canvas, x=x, y=y, grey=INK)
    canvas.text(at=(x, y), text=then, face=NOTE_FACE, grey=INK, anchor='lm')


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
    Draw a cursor row's tag, or the row's time when it has no tag.

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


def exit_arrow(canvas: Canvas, *, x: float, y: float, up: bool) -> float:
    """
    Draw an up-right or down-right arrow as shapes.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left end.
    y : float
        Centre line.
    up : bool
        Point up and right when True, down and right when False.

    Returns
    -------
    float
        Width used, the gap before the text included.
    """
    sign = -1 if up else 1
    canvas.line(
        points=[(x + 2, y - sign * 6), (x + 10, y + sign * 2)],
        width=2,
        grey=INK,
    )
    canvas.polygon(
        points=[(x + 13, y + sign * 5), (x + 13, y - sign * 3),
                (x + 5, y + sign * 5)],
        grey=INK,
    )
    return EXIT_ARROW


def name_start(canvas: Canvas, *, row: StripRow, columns: Columns
               ) -> tuple[float, Face]:
    """
    Find where a row's name starts and its face, drawing an exit's arrow.

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
    tuple[float, Face]
        The name's x, at its `name_after` when that is further right,
        and its face. An exit's arrow goes at its stub's name place, or
        at its `name_after`, and its text after the arrow.
    """
    if row.kind is not RowKind.EXIT:
        left = columns.name_x + INDENT * row.depth
        return max(left, row.name_after or left), name_face(row=row)
    x = max(columns.name_x + INDENT * (row.depth + 1), row.name_after or 0.0)
    up = row.parent_y is not None and row.parent_y > row.y
    return x + exit_arrow(canvas, x=x, y=row.y, up=up), EXIT_FACE


def draw_row(canvas: Canvas, *, row: StripRow, columns: Columns) -> None:
    """
    Draw one row's cursor bar, station, name, time, tag and note.

    The name starts at the row's `name_after` when that is further
    right, past badges beside its disc. A carry-on note goes between
    the name and the time on a row with no tag that isn't the cursor,
    and the name is cut short of it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row.
    columns : Columns
        Where the strip's parts sit.
    """
    left: float = columns.name_x + INDENT * row.depth
    if row.cursor:
        canvas.rect(
            box=(left - CURSOR_INSET, row.y - CURSOR_HALF,
                 columns.right - CURSOR_EDGE, row.y + CURSOR_HALF),
            grey=INK,
        )
    draw_station(canvas, row=row, x=columns.track_x + INDENT * row.depth)
    edge = draw_end(canvas, row=row, columns=columns)
    left, face = name_start(canvas, row=row, columns=columns)
    if not row.tag and not row.cursor:
        box = draw_note(
            canvas,
            row=row,
            right=columns.time_right - (NAME_ROOM if row.time else 0),
            floor=left + TAG_GAP,
        )
        edge = min(edge, box - TAG_GAP)
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
