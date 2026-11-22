"""Draw strip rows, the From band and the strip's vehicles in ink only."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from paperroute_device.render.canvas import INK, PAPER, Canvas, Face
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_model import (
    HOOK_GAP,
    HOOK_LENGTH,
    HOOK_RADIUS,
    HOOK_TIP,
    INDENT,
    ROW_H,
    Band,
    StripRow,
)
from paperroute_transit.render.chrome import ARROW_WIDTH, arrow
from paperroute_transit.render.kerb_menu import MARK_RIGHT_GAP, two_way_mark
from paperroute_transit.render.markers import draw_hollow, draw_live
from paperroute_transit.render.names import Naming, fit_name

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
SMALL_TIME_FACE: Final[Face] = Face(weight=400, px=SPUR_PX)
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
HOOK_STEPS: Final[int] = 6  # points along a hook's quarter circle
HOOK_HALF: Final[float] = 6.0  # the exit arrow's half height
NOTE_FACE: Final[Face] = Face(weight=700, px=12)
NOTE_MAX: Final[float] = 190.0  # a carry-on note box's widest
NOTE_OUTLINE: Final[float] = 2.0
NOTE_PAD: Final[float] = 6.0  # pixels inside each end of the note box
NOTE_GAP: Final[float] = 4.0  # pixels between the route and the arrow
DASH: Final[int] = 7  # px
DASH_GAP: Final[int] = 5  # px
DASH_LEAD: Final[float] = 18.0  # pixels above a dashed top slot's centre
DASH_CLEAR: Final[float] = 2.0  # pixels between a ring and a dash
STUB_INSET: Final[int] = 4  # pixels a stub's text sits right of names
EXIT_LINES: Final[int] = 3
EXIT_ROWS: Final[tuple[tuple[float, ...], ...]] = (
    (0.0,),
    (-2.0, 12.0),
    (-9.0, 4.0, 17.0),
)  # exit label text centres from the row's centre line
NO_MARK: Final[frozenset[RowKind]] = frozenset(
    {RowKind.EXIT, RowKind.PASSED, RowKind.DASHED},
)  # kinds with no station mark
SMALL_KINDS: Final[frozenset[RowKind]] = frozenset(
    {RowKind.PASSED, RowKind.EARLIER, RowKind.DASHED},
)  # kinds drawn in the spur size


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
    naming : Naming
        How row names are shortened.
    """

    track_x: int
    name_x: int
    time_right: int
    right: int
    naming: Naming


def track_points(*, row: StripRow, parent_y: float, track_x: int
                 ) -> list[tuple[float, float]]:
    """
    Give the points of the track from a row's parent to the row.

    A change of depth bends half a row from the parent, then runs
    straight down or up to the row. An exit's hook runs down or up its
    junction's track, turns right in a quarter circle onto the row's
    centre line, then runs straight to where its arrow starts.

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
    if row.kind is RowKind.EXIT:
        return [start, *hook_points(x=start[0], y=row.y, parent_y=parent_y)]
    x = track_x + INDENT * row.depth
    bend = math.copysign(ROW_H / 2, row.y - parent_y)
    elbow = [] if start[0] == x else [(x, parent_y + bend)]
    return [start, *elbow, (x, row.y)]


def hook_points(*, x: float, y: float, parent_y: float
                ) -> list[tuple[float, float]]:
    """
    Give an exit hook's points after the junction.

    Parameters
    ----------
    x : float
        The junction's track x.
    y : float
        The exit row's centre line.
    parent_y : float
        The junction's centre line.

    Returns
    -------
    list[tuple[float, float]]
        The bend's start, points round the quarter circle, and the
        straight's right end.
    """
    sign = math.copysign(1.0, y - parent_y)
    centre_y = y - sign * HOOK_RADIUS
    arc = [
        (
            x + HOOK_RADIUS - HOOK_RADIUS * math.cos(angle),
            centre_y + sign * HOOK_RADIUS * math.sin(angle),
        )
        for angle in (
            math.pi / 2 * step / HOOK_STEPS
            for step in range(HOOK_STEPS + 1)
        )
    ]
    arc[-1] = (x + HOOK_RADIUS, y)
    return [*arc, (x + HOOK_LENGTH, y)]


def dash_span(*, row: StripRow, parent_y: float) -> tuple[float, float]:
    """
    Give the y range of the dashes on a dashed row's track.

    Parameters
    ----------
    row : StripRow
        A dashed row.
    parent_y : float
        Its parent's centre line.

    Returns
    -------
    tuple[float, float]
        Where the dashes start and end. A row above its parent, the top
        slot, starts at the slot's top and ends short of the parent's
        ring; a row below it, a stub, starts below the parent's ring and
        ends at the stub.
    """
    if row.y < parent_y:
        return row.y - DASH_LEAD, parent_y - RING_RADIUS - DASH_CLEAR
    return parent_y + RING_RADIUS + DASH_CLEAR, row.y


def draw_dashes(
    canvas: Canvas, *, x: float, span: tuple[float, float], width: float,
) -> None:
    """
    Draw a vertical run of dashes.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Centre x.
    span : tuple[float, float]
        Where the first dash starts and where the run ends.
    width : float
        Stroke width.
    """
    canvas.line(
        points=[(x, span[0]), (x, span[1])],
        width=width,
        grey=INK,
        dash=(DASH, DASH_GAP),
    )


def draw_track(
    canvas: Canvas,
    *,
    row: StripRow,
    track_x: int,
    dashed_from: bool = False,
) -> None:
    """
    Draw the track from a row's parent to the row (see `track_points`).

    A dashed row, and a row whose parent is a dashed stub, get dashes
    in place of the line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row; nothing is drawn when it has no parent.
    track_x : int
        Track x at depth 0.
    dashed_from : bool
        Whether the row's parent is a dashed row.
    """
    if row.parent_y is None:
        return
    width = RIDE_WIDTH if row.ride else TRACK_WIDTH
    x = track_x + INDENT * row.depth
    if row.kind is RowKind.DASHED:
        span = dash_span(row=row, parent_y=row.parent_y)
    elif dashed_from:
        span = (row.parent_y, row.y)
    else:
        canvas.line(
            points=track_points(
                row=row, parent_y=row.parent_y, track_x=track_x,
            ),
            width=width,
            grey=INK,
        )
        return
    draw_dashes(canvas, x=x, span=span, width=width)


def draw_station(canvas: Canvas, *, row: StripRow, x: float) -> None:
    """
    Draw a row's station mark.

    The From and Destination get a disc, a closed fold two rings, and
    an exit, a passed station and a dashed row nothing. Any other
    station gets a ring.

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
    if row.kind in NO_MARK:
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
    fixed = (
        2 * NOTE_PAD
        + canvas.length(text=note.route, face=NOTE_FACE)
        + NOTE_GAP
        + ARROW_WIDTH
    )
    room = min(NOTE_MAX, right - floor) - fixed
    then = canvas.fit(text=note.then, face=NOTE_FACE, width=room)
    if room <= 0 or not then:
        return right
    left = right - fixed - canvas.length(text=then, face=NOTE_FACE)
    outlined_box(
        canvas, box=(left, row.y - TAG_HALF, right, row.y + TAG_HALF),
    )
    note_text(
        canvas, texts=(note.route, then), x=left + NOTE_PAD, y=row.y + 1,
    )
    return left


def outlined_box(
    canvas: Canvas, *, box: tuple[float, float, float, float],
) -> None:
    """
    Draw a paper box with an ink outline.

    Parameters
    ----------
    canvas : Canvas
        Target.
    box : tuple[float, float, float, float]
        Left, top, right and bottom.
    """
    left, top, right, bottom = box
    canvas.rect(box=box, grey=INK)
    canvas.rect(
        box=(left + NOTE_OUTLINE, top + NOTE_OUTLINE,
             right - NOTE_OUTLINE, bottom - NOTE_OUTLINE),
        grey=PAPER,
    )


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
        15 px on the main line and 13 px on spurs, passed stations,
        earlier calls and dashed rows; bold for the From, the
        Destination and the cursor row.
    """
    small = row.depth != 0 or row.kind in SMALL_KINDS
    return Face(
        weight=700 if row.bold or row.cursor else 400,
        px=SPUR_PX if small else MAIN_PX,
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
            face=SMALL_TIME_FACE if row.kind is RowKind.EARLIER else TIME_FACE,
            grey=PAPER if row.cursor else INK,
            anchor='rs',
        )
    return edge


def hook_arrow(canvas: Canvas, *, x: float, y: float) -> float:
    """
    Draw an exit hook's filled arrow pointing right.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        The arrow's base.
    y : float
        Centre line.

    Returns
    -------
    float
        Width used, the gap before the label included.
    """
    canvas.polygon(
        points=[(x, y - HOOK_HALF), (x + HOOK_TIP, y), (x, y + HOOK_HALF)],
        grey=INK,
    )
    return HOOK_TIP + HOOK_GAP


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
        and its face. An exit's arrow goes at its hook's end and its
        label after the arrow, or at its `name_after` if further right.
    """
    if row.kind is not RowKind.EXIT:
        left = columns.name_x + INDENT * row.depth
        if row.kind is RowKind.DASHED:
            left += STUB_INSET
        return max(left, row.name_after or left), name_face(row=row)
    x = columns.track_x + INDENT * row.parent_depth + HOOK_LENGTH
    label = x + hook_arrow(canvas, x=x, y=row.y)
    return max(label, row.name_after or label), EXIT_FACE


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
    if row.cursor:
        start = columns.name_x + INDENT * row.depth
        canvas.rect(
            box=(start - CURSOR_INSET, row.y - CURSOR_HALF,
                 columns.right - CURSOR_EDGE, row.y + CURSOR_HALF),
            grey=INK,
        )
    draw_station(canvas, row=row, x=columns.track_x + INDENT * row.depth)
    edge: float = draw_end(canvas, row=row, columns=columns)
    if row.paired:
        edge = min(
            edge,
            two_way_mark(
                canvas,
                right=columns.time_right - MARK_RIGHT_GAP,
                y=row.y,
                grey=PAPER if row.cursor else INK,
            ) - TAG_GAP,
        )
    left, face = name_start(canvas, row=row, columns=columns)
    if not row.tag and not row.cursor:
        edge = noted_edge(
            canvas, row=row, columns=columns, left=left, edge=edge,
        )
    draw_name(
        canvas,
        row=row,
        left=left,
        face=face,
        edge=edge,
        naming=columns.naming,
    )


def draw_name(
    canvas: Canvas,
    *,
    row: StripRow,
    left: float,
    face: Face,
    edge: float,
    naming: Naming,
) -> None:
    """
    Draw a row's name, or an exit's label of place names.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row.
    left : float
        Where the name starts.
    face : Face
        The name's face.
    edge : float
        Where the name must end.
    naming : Naming
        How the name is shortened.
    """
    if row.names:
        lines = exit_lines(canvas=canvas, names=row.names, width=edge - left)
        for line, offset in zip(lines, EXIT_ROWS[len(lines) - 1]):
            canvas.text(
                at=(left, row.y + offset),
                text=canvas.fit(text=line, face=face, width=edge - left),
                face=face,
                grey=INK,
                anchor='lm',
            )
    elif row.name:
        canvas.text(
            at=(left, row.y + BASELINE_DROP),
            text=fit_name(
                text=row.name, face=face, width=edge - left, naming=naming,
            )[0],
            face=face,
            grey=PAPER if row.cursor else INK,
        )


def exit_lines(
    *, canvas: Canvas, names: Sequence[str], width: float,
) -> tuple[str, ...]:
    """
    Wrap an exit's place names into at most `EXIT_LINES` lines.

    Parameters
    ----------
    canvas : Canvas
        Used to measure text.
    names : Sequence[str]
        The places, in order.
    width : float
        The widest a line may be.

    Returns
    -------
    tuple[str, ...]
        The label 'to a, b, c' broken at word breaks. Names are dropped
        from the end until every line fits, with no count of what was
        dropped. When even the first name does not fit, one line cut
        short.
    """
    for count in range(len(names), 0, -1):
        lines = wrapped(
            canvas=canvas,
            text='to ' + ', '.join(names[:count]),
            width=width,
        )
        if len(lines) <= EXIT_LINES and all(
            canvas.length(text=line, face=EXIT_FACE) <= width
            for line in lines
        ):
            return lines
    if not names:
        return ()
    return (
        canvas.fit(text='to ' + names[0], face=EXIT_FACE, width=width),
    )


def wrapped(
    *, canvas: Canvas, text: str, width: float,
) -> tuple[str, ...]:
    """
    Break text into lines at word breaks, filling each line.

    Parameters
    ----------
    canvas : Canvas
        Used to measure text.
    text : str
        The text.
    width : float
        The widest a line may be; a word wider than it gets a line.

    Returns
    -------
    tuple[str, ...]
        The lines.
    """
    lines: list[str] = []
    for word in text.split():
        joined = f'{lines[-1]} {word}' if lines else word
        if lines and canvas.length(text=joined, face=EXIT_FACE) <= width:
            lines[-1] = joined
        else:
            lines.append(word)
    return tuple(lines)


def noted_edge(
    canvas: Canvas,
    *,
    row: StripRow,
    columns: Columns,
    left: float,
    edge: float,
) -> float:
    """
    Draw a row's carry-on note and say where its name must end.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        The row, with no tag and not the cursor.
    columns : Columns
        Where the strip's parts sit.
    left : float
        Where the name starts.
    edge : float
        Where the name must end without a note.

    Returns
    -------
    float
        The edge, moved short of the note when one was drawn.
    """
    right = columns.time_right - (NAME_ROOM if row.time else 0)
    box = draw_note(canvas, row=row, right=right, floor=left + TAG_GAP)
    return min(edge, box - TAG_GAP) if box < right else edge


def draw_rows(
    canvas: Canvas,
    *,
    rows: Sequence[StripRow],
    track_x: int,
    name_x: int,
    time_right: int,
    right: int,
    rules: NameRules,
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
    rules : NameRules
        Word lists and options for shortening row names.
    """
    columns = Columns(
        track_x=track_x,
        name_x=name_x,
        time_right=time_right,
        right=right,
        naming=Naming(
            rules=rules,
            siblings=tuple(row.name for row in rows if row.name),
        ),
    )
    stubs = {row.y for row in rows if row.kind is RowKind.DASHED}
    for row in rows:
        draw_track(
            canvas,
            row=row,
            track_x=track_x,
            dashed_from=(
                row.kind is not RowKind.DASHED and row.parent_y in stubs
            ),
        )
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
