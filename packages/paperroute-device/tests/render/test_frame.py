"""Tests for the drawing helpers shared by the device's screens."""

from typing import Final

import numpy as np

from paperroute_device.render.canvas import (
    INK,
    PAPER,
    Canvas,
    Face,
    text_width,
)
from paperroute_device.render.frame import HEADER, finish, wrap

FACE: Final[Face] = Face(weight=700, px=20)


def test_finish_thresholds_to_ink_and_paper() -> None:
    """Dark greys become ink and light greys paper."""
    canvas = Canvas(width=4, height=1)
    canvas.rect(box=(0, 0, 1, 1), grey=INK)
    canvas.rect(box=(1, 0, 2, 1), grey=127)
    canvas.rect(box=(2, 0, 3, 1), grey=128)
    frame = finish(canvas)
    assert frame.dtype == np.uint8
    assert frame.tolist() == [[INK, INK, PAPER, PAPER]]


def test_finish_keeps_the_canvas_size() -> None:
    """A full canvas gives a 480 by 800 frame."""
    assert finish(Canvas()).shape == (480, 800)


def test_wrap_fills_each_line_greedily() -> None:
    """Words join a line while they fit the width."""
    width = text_width(text='one two', face=FACE)
    lines = wrap(text='one two three four', face=FACE, width=width)
    assert lines[0] == 'one two'
    assert all(text_width(text=line, face=FACE) <= width for line in lines)
    assert ' '.join(lines) == 'one two three four'


def test_wrap_gives_a_long_word_its_own_line() -> None:
    """A word wider than the width is not split."""
    assert wrap(text='a enormous b', face=FACE, width=1) == (
        'a',
        'enormous',
        'b',
    )


def test_wrap_empty_text_has_no_lines() -> None:
    """Blank text gives no lines."""
    assert not wrap(text='  ', face=FACE, width=100)


def test_header_is_forty_pixels() -> None:
    """The header band is 40 pixels tall."""
    assert HEADER == 40
