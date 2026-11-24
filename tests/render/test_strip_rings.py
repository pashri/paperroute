"""Facts about drawn strip rings and spurs (no golden images)."""

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, Canvas
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_detours import DetourKind, DetourPart
from paperroute_transit.core.strip_model import (
    INDENT,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    StripRow,
)
from paperroute_transit.core.strip_rings import ring_of
from paperroute_transit.render.names import Naming
from paperroute_transit.render.strip import (
    RING_RADIUS,
    TAG_GAP,
    TIME_FACE,
    Columns,
    draw_rows,
)
from paperroute_transit.render.strip_rings import draw_rings, draw_spurs

COLUMNS = Columns(
    track_x=TRACK_X,
    name_x=52,
    time_right=424,
    right=440,
    naming=Naming(rules=NameRules(), siblings=()),
)
TOP = STRIP_TOP + ROW_H * 1.5


def row(*, y: float, part: DetourPart | None = None) -> StripRow:
    """
    Make a strip row whose parent is the row above.

    Parameters
    ----------
    y : float
        Row centre.
    part : DetourPart | None
        Its place on a detour.

    Returns
    -------
    StripRow
        The row.
    """
    return StripRow(
        name=f'Stop {y:.0f}',
        depth=0,
        kind=RowKind.MAIN,
        y=y,
        parent_y=y - ROW_H,
        parent_depth=0,
        tag='',
        time='8:00',
        ride=False,
        cursor=False,
        bold=False,
        station=f's{y}',
        detour=part,
    )


def loop_rows(*, stops: int) -> list[StripRow]:
    """
    Make a stand, a loop's rows last call first, the stand back, a stop.

    Parameters
    ----------
    stops : int
        Stops on the ring.

    Returns
    -------
    list[StripRow]
        The rows.
    """
    inside = [
        row(
            y=TOP + ROW_H * (1 + place),
            part=DetourPart(
                kind=DetourKind.LOOP,
                group=0,
                slot=stops - 1 - place,
                size=stops,
            ),
        )
        for place in range(stops)
    ]
    return [
        row(y=TOP),
        *inside,
        row(y=TOP + ROW_H * (stops + 1)),
        row(y=TOP + ROW_H * (stops + 2)),
    ]


def spur_rows(*, levels: int) -> list[StripRow]:
    """
    Make a stop, a junction, a spur's levels and a stop after.

    Parameters
    ----------
    levels : int
        Levels past the junction.

    Returns
    -------
    list[StripRow]
        The rows.
    """
    spur = [
        row(
            y=TOP + ROW_H * (1 + level),
            part=DetourPart(
                kind=DetourKind.SPUR, group=0, slot=level, size=levels,
            ),
        )
        for level in range(levels + 1)
    ]
    return [row(y=TOP), *spur, row(y=TOP + ROW_H * (levels + 2))]


def drawn(*, rows: list[StripRow]) -> NDArray[np.uint8]:
    """
    Draw rows, then their rings and spurs, as the Strip does.

    Parameters
    ----------
    rows : list[StripRow]
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
        track_x=COLUMNS.track_x,
        name_x=COLUMNS.name_x,
        time_right=COLUMNS.time_right,
        right=COLUMNS.right,
        rules=NameRules(),
    )
    draw_rings(canvas, rows=rows, columns=COLUMNS)
    draw_spurs(canvas, rows=rows, columns=COLUMNS)
    return canvas.grey()


def test_draw_rings_inks_the_ring_right_of_the_track() -> None:
    """The ring's right side is inked between two of its stops."""
    rows = loop_rows(stops=5)
    ring = ring_of(rows=rows, group=0, track_x=TRACK_X)
    assert ring is not None
    frame = drawn(rows=rows)
    right = int(ring.cx + ring.right)
    y = int(ring.cy + ROW_H / 2)
    assert (frame[y - 2:y + 3, right - 4:right + 2] == INK).any()


def test_draw_rings_draws_no_bypass_line() -> None:
    """The rows leave the trunk x blank; only the ring's left inks it."""
    rows = loop_rows(stops=5)
    ring = ring_of(rows=rows, group=0, track_x=TRACK_X)
    assert ring is not None
    canvas = Canvas()
    draw_rows(
        canvas,
        rows=rows,
        track_x=TRACK_X,
        name_x=COLUMNS.name_x,
        time_right=COLUMNS.time_right,
        right=COLUMNS.right,
        rules=NameRules(),
    )
    top, bottom = int(rows[0].y + ROW_H / 2), int(rows[6].y - ROW_H / 2)
    assert not (canvas.grey()[top:bottom, TRACK_X] == INK).any()
    assert drawn(rows=rows)[int(ring.cy + ROW_H / 2), TRACK_X] == INK


def test_draw_rings_draws_rows_when_cut_off() -> None:
    """A ring missing a stand keeps the plain trunk line."""
    rows = loop_rows(stops=5)[1:]
    frame = drawn(rows=rows)
    y = int(rows[2].y + ROW_H / 2)
    assert (frame[y - 8:y + 8, TRACK_X] == INK).all()


def test_draw_spurs_draws_the_turn_back_arrow() -> None:
    """Ink sits below and right of the far end's stop ring."""
    rows = spur_rows(levels=2)
    far = rows[3]
    x = TRACK_X + INDENT
    frame = drawn(rows=rows)
    below = frame[
        int(far.y + RING_RADIUS + 2):int(far.y + ROW_H / 2),
        x:x + 12,
    ]
    assert (below == INK).any()
    plain = Canvas()
    draw_rows(
        plain,
        rows=[replace(each, detour=None) for each in rows],
        track_x=TRACK_X,
        name_x=COLUMNS.name_x,
        time_right=COLUMNS.time_right,
        right=COLUMNS.right,
        rules=NameRules(),
    )
    assert not (
        plain.grey()[
            int(far.y + RING_RADIUS + 2):int(far.y + ROW_H / 2),
            x:x + 12,
        ] == INK
    ).any()


def test_draw_spurs_keeps_the_trunk_straight_past_the_spur() -> None:
    """The trunk runs from the junction to the row after it."""
    rows = spur_rows(levels=2)
    frame = drawn(rows=rows)
    assert (frame[int(rows[1].y) + 8:int(rows[4].y) - 8, TRACK_X] == INK).all()


def test_draw_rows_keeps_a_name_clear_of_a_wide_time() -> None:
    """A spur row's two times push its name's end left."""
    wide = replace(
        row(y=TOP),
        name='Moncrieff Dr After Cressy Rd / Elliott Av At Moncrieff Dr',
        time='18:04 / 18:06',
    )
    canvas = Canvas()
    draw_rows(
        canvas,
        rows=[wide],
        track_x=TRACK_X,
        name_x=COLUMNS.name_x,
        time_right=COLUMNS.time_right,
        right=COLUMNS.right,
        rules=NameRules(),
    )
    width = canvas.length(text=wide.time, face=TIME_FACE)
    gap = int(COLUMNS.time_right - width - TAG_GAP / 2)
    y = int(TOP)
    assert not (canvas.grey()[y - 8:y + 6, gap - 2:gap + 2] == INK).any()
