"""Facts about the drawn strip rows, band and vehicles (no golden images)."""

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from paperroute.core.map_view import Marker
from paperroute.core.reach import RowKind
from paperroute.core.strip_model import (
    INDENT,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    TRACK_X,
    Band,
    StripRow,
)
from paperroute.render.canvas import INK, PAPER, Canvas
from paperroute.render.strip import draw_band, draw_rows, draw_strip_markers

NAME_X = 52
TIME_RIGHT = 424
RIGHT = 440
FROM_Y = STRIP_TOP + 2 * ROW_H + ROW_H / 2


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
