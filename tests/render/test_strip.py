"""Facts about the drawn strip rows, band and vehicles (no golden images)."""

import math
from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas, Face
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_model import (
    HOOK_GAP,
    HOOK_LENGTH,
    HOOK_RADIUS,
    HOOK_TIP,
    INDENT,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    TRACK_X,
    Band,
    CarryNote,
    StripRow,
)
from paperroute_transit.render.strip import (
    MAIN_PX,
    NAME_ROOM,
    NOTE_MAX,
    TAG_GAP,
    TAG_HALF,
    draw_band,
    draw_rows,
    draw_strip_markers,
    hook_arrow,
    track_points,
)

NAME_X = 52
TIME_RIGHT = 424
RIGHT = 440
FROM_Y = STRIP_TOP + 2 * ROW_H + ROW_H / 2
NOTE = CarryNote(route='T8', then='T2 Leppington')


def strip_row(
    *,
    y: float,
    parent_y: float | None = None,
    depth: int = 0,
    parent_depth: int = 0,
    tag: str = '',
    ride: bool = False,
    cursor: bool = False,
    name: str = 'Wynyard',
    time: str = '',
    bold: bool = False,
) -> StripRow:
    """
    Make a strip row for drawing.

    Parameters
    ----------
    y : float
        Row centre.
    parent_y : float | None
        Parent row centre, or None.
    depth : int
        Indent depth.
    parent_depth : int
        Parent's depth.
    tag : str
        Tag text.
    ride : bool
        On the ridden section.
    cursor : bool
        The row being picked.
    name : str
        Station name.
    time : str
        Clock time.
    bold : bool
        The From or the Destination.

    Returns
    -------
    StripRow
        The row.
    """
    return StripRow(
        name=name,
        depth=depth,
        kind=RowKind.MAIN if depth == 0 else RowKind.BRANCH,
        y=y,
        parent_y=parent_y,
        parent_depth=parent_depth,
        tag=tag,
        time=time,
        ride=ride,
        cursor=cursor,
        bold=bold,
        station=name,
    )


def drawn(*, rows: tuple[StripRow, ...]) -> NDArray[np.uint8]:
    """
    Draw rows on the Strip's columns.

    Parameters
    ----------
    rows : tuple[StripRow, ...]
        Rows.

    Returns
    -------
    NDArray[np.uint8]
        The canvas pixels.
    """
    canvas = Canvas()
    draw_rows(
        canvas,
        rows=rows,
        track_x=TRACK_X,
        name_x=NAME_X,
        time_right=TIME_RIGHT,
        right=RIGHT,
    )
    return canvas.grey()


def ink_columns(*, frame: NDArray[np.uint8], y: float, left: int,
                right: int) -> list[int]:
    """
    List the ink columns of one pixel row in a range.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        Pixels.
    y : float
        Pixel row.
    left : int
        First column.
    right : int
        Column past the last.

    Returns
    -------
    list[int]
        Columns holding ink.
    """
    line = frame[int(y), left:right]
    return [left + int(x) for x in np.flatnonzero(line < 128)]


def test_track_is_thick_on_ride() -> None:
    """The track is 9 px from FROM to TO and 3 px past TO."""
    step = ROW_H
    rows = (
        strip_row(y=FROM_Y, bold=True, ride=True),
        strip_row(y=FROM_Y + step, parent_y=FROM_Y, ride=True),
        strip_row(y=FROM_Y + 2 * step, parent_y=FROM_Y + step, ride=True,
                  bold=True),
        strip_row(y=FROM_Y + 3 * step, parent_y=FROM_Y + 2 * step),
    )
    frame = drawn(rows=rows)
    ridden = ink_columns(frame=frame, y=FROM_Y + step / 2, left=0, right=60)
    after = ink_columns(
        frame=frame, y=FROM_Y + 2.5 * step, left=0, right=60,
    )
    assert len(ridden) == 9
    assert len(after) == 3
    assert TRACK_X in ridden
    assert TRACK_X in after


def test_spur_rows_indent_by_16() -> None:
    """A depth-1 row's ring and name sit 16 px right of a depth-0 row's."""
    main = drawn(rows=(strip_row(y=FROM_Y),))
    spur = drawn(rows=(strip_row(y=FROM_Y, depth=1),))
    main_ring = ink_columns(frame=main, y=FROM_Y, left=0, right=45)
    spur_ring = ink_columns(frame=spur, y=FROM_Y, left=0, right=61)
    assert [x + INDENT for x in main_ring] == spur_ring
    main_name = ink_columns(frame=main, y=FROM_Y + 2, left=45, right=RIGHT)
    spur_name = ink_columns(frame=spur, y=FROM_Y + 2, left=61, right=RIGHT)
    assert NAME_X <= main_name[0] < NAME_X + 4
    assert NAME_X + INDENT <= spur_name[0] < NAME_X + INDENT + 4


def test_elbow_joins_a_spur_to_its_parent() -> None:
    """A spur's track leaves the parent's x and arrives at its own."""
    rows = (
        strip_row(y=FROM_Y, bold=True),
        strip_row(y=FROM_Y + ROW_H, parent_y=FROM_Y, depth=1),
    )
    frame = drawn(rows=rows)
    near_parent = ink_columns(
        frame=frame, y=FROM_Y + 12, left=0, right=60,
    )
    near_row = ink_columns(
        frame=frame, y=FROM_Y + ROW_H - 8, left=0, right=60,
    )
    assert TRACK_X + INDENT not in near_parent
    assert TRACK_X + INDENT in near_row


def test_from_and_to_are_discs_and_others_rings() -> None:
    """FROM is a filled disc; a plain station is a hollow ring."""
    frame = drawn(rows=(
        strip_row(y=FROM_Y, bold=True),
        strip_row(y=FROM_Y + ROW_H, parent_y=FROM_Y),
    ))
    assert frame[int(FROM_Y), TRACK_X] == INK
    assert frame[int(FROM_Y), TRACK_X + 8] == INK
    assert frame[int(FROM_Y + ROW_H), TRACK_X + 8] == PAPER
    assert frame[int(FROM_Y + ROW_H), TRACK_X - 3] == PAPER


def test_fold_row_is_a_double_ring() -> None:
    """A closed fold is two hollow rings, one inside the other."""
    fold = replace(
        strip_row(y=FROM_Y + ROW_H, parent_y=FROM_Y, name='A … B +5'),
        kind=RowKind.FOLD,
    )
    frame = drawn(rows=(strip_row(y=FROM_Y, bold=True), fold))
    y = int(FROM_Y + ROW_H)
    assert frame[y, TRACK_X] == PAPER
    assert frame[y, TRACK_X + 4] == INK
    assert frame[y, TRACK_X + 6] == PAPER
    assert frame[y, TRACK_X + 8] == INK
    assert frame[y, TRACK_X + 11] == PAPER


def exit_row(*, y: float, parent_y: float) -> StripRow:
    """
    Make a depth-0 exit row.

    Parameters
    ----------
    y : float
        Row centre.
    parent_y : float
        Its junction's row centre.

    Returns
    -------
    StripRow
        The row, named 'Berala · T3'.
    """
    return replace(
        strip_row(y=y, parent_y=parent_y, name='Berala · T3'),
        kind=RowKind.EXIT,
    )


def ink_in(*, frame: NDArray[np.uint8], left: float, top: float,
           size: float) -> int:
    """
    Count the ink pixels in a square.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        Pixels.
    left : float
        Left edge.
    top : float
        Top edge.
    size : float
        Side.

    Returns
    -------
    int
        Ink pixels.
    """
    box = frame[int(top):int(top + size), int(left):int(left + size)]
    return int(np.count_nonzero(box < 128))


def test_exit_track_turns_right_at_once() -> None:
    """Down the junction's track, a quarter circle, then the straight."""
    row = exit_row(y=FROM_Y + ROW_H, parent_y=FROM_Y)
    points = track_points(row=row, parent_y=FROM_Y, track_x=TRACK_X)
    centre = (TRACK_X + HOOK_RADIUS, row.y - HOOK_RADIUS)
    assert points[0] == (TRACK_X, FROM_Y)
    assert len(points) >= 6
    for x, y in points[:-1]:
        on_arc = math.isclose(
            math.dist((x, y), centre), HOOK_RADIUS, abs_tol=1e-9,
        )
        assert x == TRACK_X or (on_arc and y >= centre[1])
    assert [y for _, y in points[-2:]] == [row.y, row.y]
    assert points[-1][0] == TRACK_X + HOOK_LENGTH


def test_hooks_above_and_below_bend_their_own_way() -> None:
    """An exit above its junction mirrors one below it."""
    below, above = (
        track_points(
            row=exit_row(y=FROM_Y + offset, parent_y=FROM_Y),
            parent_y=FROM_Y,
            track_x=TRACK_X,
        )
        for offset in (ROW_H, -ROW_H)
    )
    assert [x for x, _ in below] == [x for x, _ in above]
    for (_, low), (_, high) in zip(below, above):
        assert math.isclose(low - FROM_Y, FROM_Y - high)


def test_hook_arrow_points_right() -> None:
    """The arrow's ink sits right of its base and narrows to the tip."""
    canvas = Canvas()
    x, y = 40.0, 20.0
    used = hook_arrow(canvas, x=x, y=y)
    frame = canvas.grey()
    assert used == HOOK_TIP + HOOK_GAP
    assert np.count_nonzero(frame[:, :int(x) - 1] < 128) == 0
    tip = np.count_nonzero(frame[:, int(x + HOOK_TIP) - 1] < 128)
    base = np.count_nonzero(frame[:, int(x) + 1] < 128)
    assert tip < base


def test_exit_label_follows_the_arrow() -> None:
    """The label's ink all lies past the arrow's tip."""
    row = exit_row(y=FROM_Y + ROW_H, parent_y=FROM_Y)
    bare = drawn(rows=(strip_row(y=FROM_Y, name='J'),))
    frame = drawn(rows=(strip_row(y=FROM_Y, name='J'), row))
    tip = TRACK_X + HOOK_LENGTH + HOOK_TIP
    top, bottom = int(row.y - ROW_H / 2) + 1, int(row.y + ROW_H / 2)
    label = (frame[top:bottom] < 128) & ~(bare[top:bottom] < 128)
    columns = np.flatnonzero(label[:, int(tip) + 1:].any(axis=0))
    assert columns.size > 0
    first = int(tip) + 1 + int(columns[0])
    assert first >= tip + HOOK_GAP - 1


def test_exit_row_draws_no_ring() -> None:
    """An exit row has no station mark at its junction's track."""
    y = FROM_Y + ROW_H
    frame = drawn(rows=(
        strip_row(y=FROM_Y, name='J'), exit_row(y=y, parent_y=FROM_Y),
    ))
    assert frame[int(y), TRACK_X + 4] == PAPER
    assert frame[int(y) + 6, TRACK_X] == PAPER


def test_three_exits_draw_three_hooks() -> None:
    """Each of three exit rows below J has ink along its own straight."""
    exits = tuple(
        exit_row(y=FROM_Y + k * ROW_H, parent_y=FROM_Y) for k in (1, 2, 3)
    )
    frame = drawn(rows=(strip_row(y=FROM_Y, name='J'), *exits))
    for row in exits:
        middle = int(TRACK_X + HOOK_LENGTH / 2)
        assert frame[int(row.y), middle] == INK


def test_from_row_shows_its_time_without_a_box() -> None:
    """The From keeps its disc and shows its time in ink, with no box."""
    frame = drawn(rows=(strip_row(y=FROM_Y, bold=True, time='8:09'),))
    column = frame[int(FROM_Y) - 9:int(FROM_Y) + 9, TIME_RIGHT - 40:TIME_RIGHT]
    assert column.mean() > 128
    assert (column == INK).any()
    assert frame[int(FROM_Y), TRACK_X + 8] == INK


def test_cursor_row_is_inverted() -> None:
    """The cursor row is ink from the name to the edge, with a paper TO?."""
    frame = drawn(rows=(strip_row(y=FROM_Y, tag='TO?', cursor=True),))
    top, bottom = int(FROM_Y) - 14, int(FROM_Y) + 14
    bar = frame[top:bottom, NAME_X - 6:RIGHT - 6]
    assert bar.mean() < 128
    assert (frame[top:bottom, NAME_X:NAME_X + 60] == PAPER).any()
    box = frame[int(FROM_Y) - 8:int(FROM_Y) + 8, TIME_RIGHT - 40:TIME_RIGHT]
    assert box.mean() > 128
    assert frame[int(FROM_Y), NAME_X - 10] == PAPER


def test_open_tag_is_a_paper_box_like_to() -> None:
    """A cursor on a fold carries a paper OPEN tag at the row's end."""
    frame = drawn(rows=(strip_row(y=FROM_Y, tag='OPEN', cursor=True),))
    to = drawn(rows=(strip_row(y=FROM_Y, tag='TO?', cursor=True),))
    box = frame[int(FROM_Y) - 8:int(FROM_Y) + 8, TIME_RIGHT - 40:TIME_RIGHT]
    assert box.mean() > 128
    assert (box == INK).any()
    assert (frame != to).any()


def test_cursor_row_shows_its_time_in_paper() -> None:
    """A cursor row without a tag keeps its time, in paper."""
    plain = drawn(rows=(strip_row(y=FROM_Y, cursor=True),))
    timed = drawn(rows=(strip_row(y=FROM_Y, cursor=True, time='8:09'),))
    column = (slice(int(FROM_Y) - 8, int(FROM_Y) + 8),
              slice(TIME_RIGHT - 34, TIME_RIGHT))
    assert (plain[column] == INK).all()
    assert (timed[column] == PAPER).any()


def test_long_names_fit_their_column() -> None:
    """A 120-character name stops 40 px short of the time column."""
    name = 'Long Station Name ' * 7
    frame = drawn(rows=(strip_row(y=FROM_Y, name=name[:120], time='8:09'),))
    band = frame[int(FROM_Y) - 12:int(FROM_Y) + 12]
    assert (band[:, NAME_X:NAME_X + 40] == INK).any()
    gap = band[:, TIME_RIGHT - 40:TIME_RIGHT - 36]
    assert (gap == PAPER).all()
    assert (band[:, TIME_RIGHT - 30:TIME_RIGHT] == INK).any()


def test_timed_name_without_note_keeps_its_edge() -> None:
    """A name just fitting short of the time's room is drawn whole."""
    face = Face(weight=400, px=MAIN_PX)
    room = TIME_RIGHT - NAME_ROOM - NAME_X
    name = 'Wynyard'
    while Canvas().length(text=name, face=face) <= room - 4:
        name += 'i'
    width = Canvas().length(text=name, face=face)
    assert width <= room
    frame = drawn(rows=(strip_row(y=FROM_Y, name=name, time='8:09'),))
    assert name_end(frame=frame, before=TIME_RIGHT - 30) >= (
        NAME_X + width - 3
    )


def test_long_names_stop_short_of_a_tag() -> None:
    """A long name never runs into the cursor's TO? tag."""
    name = 'Long Station Name ' * 7
    frame = drawn(
        rows=(strip_row(y=FROM_Y, name=name, tag='TO?', cursor=True),),
    )
    row = frame[int(FROM_Y) - 12:int(FROM_Y) + 12, NAME_X:TIME_RIGHT]
    ink = np.flatnonzero((row == PAPER).any(axis=0))
    gaps = np.flatnonzero(np.diff(ink) > 4)
    assert len(gaps) >= 1


def note_box(*, frame: NDArray[np.uint8]) -> tuple[int, int]:
    """
    Find the note box's left and right columns on the row at FROM_Y.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        Pixels.

    Returns
    -------
    tuple[int, int]
        First and last ink column of the box's top edge.
    """
    edge = ink_columns(
        frame=frame, y=FROM_Y - TAG_HALF, left=NAME_X, right=TIME_RIGHT,
    )
    return edge[0], edge[-1]


def name_end(*, frame: NDArray[np.uint8], before: int) -> int:
    """
    Find the last ink column of the name, left of a column.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        Pixels.
    before : int
        Column the name must end before.

    Returns
    -------
    int
        Last ink column on the row's text lines.
    """
    band = frame[int(FROM_Y) - 8:int(FROM_Y) + 8, NAME_X:before]
    return NAME_X + int(np.flatnonzero((band == INK).any(axis=0))[-1])


def test_carry_note_is_outlined_between_name_and_time() -> None:
    """A 2 px ink box left of the time's room, clear of the name."""
    frame = drawn(
        rows=(replace(strip_row(y=FROM_Y, time='8:06'), note=NOTE),),
    )
    left, right = note_box(frame=frame)
    top = int(FROM_Y - TAG_HALF)
    assert right <= TIME_RIGHT - NAME_ROOM
    assert (frame[top:top + 2, left:right + 1] == INK).all()
    assert (frame[top + 2, left + 2:right - 1] == PAPER).all()
    assert (frame[int(FROM_Y), left:left + 2] == INK).all()
    assert frame[int(FROM_Y), left + 2] == PAPER
    assert (frame[top + 4:top + 16, left + 4:right - 3] == INK).any()
    assert name_end(frame=frame, before=left) <= left - TAG_GAP


def test_long_name_yields_to_the_note() -> None:
    """A long name is cut short of the box; the note keeps its text."""
    name = ('Long Station Name ' * 2)[:30]
    short = drawn(rows=(replace(strip_row(y=FROM_Y), note=NOTE),))
    frame = drawn(rows=(replace(strip_row(y=FROM_Y, name=name), note=NOTE),))
    left, right = note_box(frame=frame)
    assert (left, right) == note_box(frame=short)
    assert name_end(frame=frame, before=left) <= left - TAG_GAP
    wordy = CarryNote(route='T8', then='T2 ' + 'Leppington ' * 8)
    full = drawn(rows=(replace(strip_row(y=FROM_Y), note=wordy),))
    wide_left, wide_right = note_box(frame=full)
    assert NOTE_MAX - 12 < wide_right - wide_left + 1 <= NOTE_MAX


def test_no_note_without_room() -> None:
    """A row whose name starts too near the time draws no note."""
    row = replace(strip_row(y=FROM_Y), name_after=TIME_RIGHT - 60)
    assert (drawn(rows=(replace(row, note=NOTE),)) == drawn(rows=(row,))).all()


def test_no_note_on_the_cursor_row() -> None:
    """A cursor row draws no note, with or without a tag."""
    for tag in ('', 'TO?'):
        row = strip_row(y=FROM_Y, cursor=True, tag=tag, time='8:06')
        assert (
            drawn(rows=(replace(row, note=NOTE),)) == drawn(rows=(row,))
        ).all()


def test_band_is_inverted_with_text() -> None:
    """The band fills its slot in ink with paper text, top or bottom."""
    for top, slot in ((True, 0), (False, STRIP_ROWS - 1)):
        canvas = Canvas()
        draw_band(
            canvas,
            band=Band(
                text='FROM Town Hall · 3 stops away',
                top=top,
                y=STRIP_TOP + slot * ROW_H,
            ),
            left=8,
            right=RIGHT - 4,
        )
        frame = canvas.grey()
        y = STRIP_TOP + slot * ROW_H
        box = frame[y + 4:y + ROW_H - 4, 12:RIGHT - 8]
        assert box.mean() < 128
        assert (box[:, 40:] == PAPER).any()
        assert frame[y + ROW_H // 2, 2] == PAPER


def test_markers_drawn_on_track() -> None:
    """A live badge sits on the track; nothing is drawn far from it."""
    marker = Marker(
        route='T1', x=TRACK_X, y=200.0, bearing=180.0, live=True,
        pinned=True,
    )
    canvas = Canvas()
    draw_strip_markers(canvas, markers=(marker,))
    frame = canvas.grey()
    assert frame[194:206, TRACK_X - 10:TRACK_X + 10].mean() < 128
    assert (frame[:, 120:] == PAPER).all()
    assert (frame[:150] == PAPER).all()


def test_hollow_marker_has_age_tab() -> None:
    """A stale timetable badge is hollow with an age tab under it."""
    marker = Marker(
        route='T1', x=TRACK_X, y=200.0, bearing=None, live=False,
        pinned=True, age=180.0,
    )
    canvas = Canvas()
    draw_strip_markers(canvas, markers=(replace(marker, age=None),))
    bare = canvas.grey()
    canvas = Canvas()
    draw_strip_markers(canvas, markers=(marker,))
    tabbed = canvas.grey()
    assert (bare[196:204, TRACK_X - 8:TRACK_X + 8] == PAPER).any()
    assert (tabbed[212:224] == INK).sum() > (bare[212:224] == INK).sum()
