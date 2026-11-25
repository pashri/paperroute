"""Facts about a drawn street heading and its bracket."""

from dataclasses import replace

from paperroute_device.render.canvas import Canvas
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.strip_model import ROW_H, STRIP_TOP, Label
from paperroute_transit.core.strip_streets import HEADING_ROOM
from paperroute_transit.render.strip import draw_rows
from paperroute_transit.render.strip_heading import (
    BRACKET,
    FOOT,
    TAB_GAP,
    heading_mid,
)
from tests.render.test_strip import NAME_X, RIGHT, TIME_RIGHT, strip_row

TOP = STRIP_TOP + 2 * ROW_H + HEADING_ROOM + ROW_H / 2


def _drawn() -> Canvas:
    """Draw a three-row run on Norton St under one plain row."""
    rows = (
        strip_row(y=TOP - ROW_H - HEADING_ROOM, name='Wynyard'),
        replace(
            strip_row(y=TOP, name='X1, Norton St'),
            label=Label(short='X1', heading='Norton St'),
        ),
        replace(
            strip_row(y=TOP + ROW_H, name='X2, Norton St'),
            label=Label(short='X2'),
        ),
        replace(
            strip_row(y=TOP + 2 * ROW_H, name='X3, Norton St'),
            label=Label(short='X3'),
        ),
    )
    canvas = Canvas()
    draw_rows(
        canvas,
        rows=rows,
        track_x=20,
        name_x=NAME_X,
        time_right=TIME_RIGHT,
        right=RIGHT,
        rules=NameRules(),
    )
    return canvas


def test_draw_heading_stops_the_tab_three_px_before_the_name() -> None:
    """The tab runs level with the name's middle and stops short of it."""
    frame = _drawn().grey()
    mid = int(heading_mid(top=TOP))
    line = frame[mid, NAME_X:NAME_X + 80] < 128
    inked = [x for x, ink in enumerate(line) if ink]
    assert inked[0] == 0
    foot, gap = int(FOOT), int(TAB_GAP)
    assert all(line[:foot])
    assert not any(line[foot:foot + gap])
    assert any(line[foot + gap:foot + gap + 3])


def test_draw_heading_drops_a_bar_to_a_foot_under_the_run() -> None:
    """The bar runs from the heading to a foot under the last row."""
    frame = _drawn().grey()
    drop = frame[int(heading_mid(top=TOP)):int(TOP + 2 * ROW_H + 8), NAME_X]
    assert (drop < 128).all()
    foot = frame[int(TOP + 2 * ROW_H + 8), NAME_X:NAME_X + int(FOOT)]
    assert (foot < 128).all()
    assert frame[int(TOP), NAME_X + int(BRACKET) + 1] > 128


def test_draw_rows_moves_run_names_right() -> None:
    """Names in a run draw their own part, moved right of the bracket."""
    frame = _drawn().grey()
    name = frame[int(TOP) - 8:int(TOP) + 8, NAME_X + int(BRACKET):NAME_X + 8]
    assert (name > 128).all()
