"""Tests for drawing the hooks buses arrive on from roads off the strip."""

from dataclasses import replace

import numpy as np

from paperroute_device.render.canvas import INK, Canvas
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.strip_hooks import hook_station
from paperroute_transit.core.strip_model import (
    HOOK_LENGTH,
    HOOK_TIP,
    ROW_H,
    TRACK_X,
)
from paperroute_transit.render.strip import (
    TRACK_WIDTH,
    draw_rows,
    hook_arrow,
    track_points,
)
from paperroute_transit.render.strip_road_hooks import draw_road_hooks
from tests.render.test_strip import (
    FROM_Y,
    NAME_X,
    RIGHT,
    TIME_RIGHT,
    exit_row,
    strip_row,
)


def test_draw_road_hook_uses_the_curved_spur_style() -> None:
    """The hook's ink is the exit hook's curve, its arrow pointing in."""
    row = replace(
        exit_row(y=FROM_Y + ROW_H, parent_y=FROM_Y),
        name='Military Rd',
        station=hook_station(road='Military Rd'),
    )
    canvas = Canvas()
    columns = draw_rows(
        canvas, rows=(row,), track_x=TRACK_X, name_x=NAME_X,
        time_right=TIME_RIGHT, right=RIGHT, rules=NameRules(),
    )
    draw_road_hooks(canvas, rows=(row,), columns=columns)
    want = Canvas()
    want.line(
        points=track_points(row=row, parent_y=FROM_Y, track_x=TRACK_X),
        width=TRACK_WIDTH,
        grey=INK,
    )
    end = TRACK_X + HOOK_LENGTH
    hook_arrow(want, x=end + HOOK_TIP, y=row.y, bearing=270.0)
    edge = int(end + HOOK_TIP)
    got, expected = canvas.grey()[:, :edge], want.grey()[:, :edge]
    assert np.array_equal(got < 128, expected < 128)
    tip = np.count_nonzero(expected[:, int(end) + 1] < 128)
    base = np.count_nonzero(expected[:, edge - 1] < 128)
    assert tip < base


def test_draw_road_hook_leaves_other_rows_alone() -> None:
    """Plain rows and ordinary exits get no inward arrow."""
    rows = (
        strip_row(y=FROM_Y, name='J'),
        exit_row(y=FROM_Y + ROW_H, parent_y=FROM_Y),
    )
    canvas = Canvas()
    columns = draw_rows(
        canvas, rows=rows, track_x=TRACK_X, name_x=NAME_X,
        time_right=TIME_RIGHT, right=RIGHT, rules=NameRules(),
    )
    before = canvas.grey()
    draw_road_hooks(canvas, rows=rows, columns=columns)
    assert np.array_equal(canvas.grey(), before)
