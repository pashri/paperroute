from dataclasses import replace
from typing import Final

import numpy as np
import pytest

from paperroute_device.render.canvas import INK, Canvas, Face, text_width
from paperroute_testkit.render.factories import stop_row
from paperroute_transit.core.stop_view import Estimate, Row
from paperroute_transit.render.late_marks import (
    MARK_GAP,
    mark_room,
    mark_width,
    marked_countdown,
    raised_mark,
)
from tests.render.test_chrome import Recording

FACE: Final[Face] = Face(weight=700, px=22)
MARK: Final[Face] = Face(weight=700, px=13)
MARKED: Final[Row] = replace(
    stop_row(), estimate=Estimate(due='8:05', late=4, uncertain=False),
)


def test_mark_room_is_zero_without_a_mark() -> None:
    """No mark takes no room."""
    assert mark_room(row=stop_row(), face=MARK) == 0.0
    assert mark_room(row=replace(MARKED, live=False), face=MARK) == 0.0


def test_mark_room_counts_the_mark_and_its_gap() -> None:
    """A mark takes its width and the gap before it."""
    assert mark_room(row=MARKED, face=MARK) == pytest.approx(
        text_width(text='+4', face=MARK) + MARK_GAP,
    )


def test_marked_countdown_without_a_mark_matches_plain_text() -> None:
    """With no mark the countdown is drawn as before."""
    plain, drawn = Canvas(), Canvas()
    expected = plain.text(
        at=(300, 100), text='9 min', face=FACE, grey=INK, anchor='rs',
    )
    width = marked_countdown(
        drawn, row=stop_row(), text='9 min', at=(300, 100), face=FACE,
        mark_face=MARK,
    )
    assert width == pytest.approx(expected)
    assert (drawn.grey() == plain.grey()).all()


def test_marked_countdown_puts_the_mark_after_the_countdown() -> None:
    """The group ends at the anchor and is as wide as it says."""
    canvas = Recording()
    width = marked_countdown(
        canvas, row=MARKED, text='9 min', at=(300, 100), face=FACE,
        mark_face=MARK,
    )
    assert sorted(canvas.drawn) == ['+4', '9 min']
    assert width == pytest.approx(
        text_width(text='9 min', face=FACE)
        + mark_room(row=MARKED, face=MARK),
    )
    ink = np.flatnonzero((canvas.grey() < 128).any(axis=0))
    assert 300 - width - 1 <= ink.min() and ink.max() <= 300


def test_marked_countdown_raises_the_mark_to_the_cap_line() -> None:
    """The mark's top is level with the countdown's; it sits higher."""
    canvas = Canvas()
    marked_countdown(
        canvas, row=MARKED, text='9', at=(300, 100), face=FACE,
        mark_face=MARK,
    )
    grey = canvas.grey() < 128
    split = int(300 - text_width(text='+4', face=MARK)) - 1
    mark = np.flatnonzero(grey[:, split:].any(axis=1))
    count = np.flatnonzero(grey[:, :split - MARK_GAP].any(axis=1))
    assert abs(int(mark.min()) - int(count.min())) <= 1
    assert mark.max() < count.max()


def test_mark_width_counts_the_mark_and_its_gap() -> None:
    """No mark takes no room; a mark takes its width and its gap."""
    assert mark_width(mark='', face=MARK) == 0.0
    assert mark_width(mark='+4', face=MARK) == pytest.approx(
        text_width(text='+4', face=MARK) + MARK_GAP,
    )


def test_raised_mark_follows_the_text_at_its_cap_line() -> None:
    """The mark starts after the text, its top level with the text's."""
    canvas = Canvas()
    end = 100 + canvas.text(at=(100, 100), text='9', face=FACE, grey=INK)
    room = raised_mark(
        canvas, mark='+4', at=(end, 100), face=FACE, mark_face=MARK,
    )
    assert room == pytest.approx(mark_width(mark='+4', face=MARK))
    grey = canvas.grey() < 128
    mark = np.flatnonzero(grey[:, int(end + MARK_GAP):].any(axis=1))
    count = np.flatnonzero(grey[:, :int(end)].any(axis=1))
    assert abs(int(mark.min()) - int(count.min())) <= 1
    assert mark.max() < count.max()
    assert raised_mark(
        Canvas(), mark='', at=(end, 100), face=FACE, mark_face=MARK,
    ) == 0.0
