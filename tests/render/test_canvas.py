"""Tests for the Skia canvas wrapper."""

import numpy as np

from paperroute.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
    encode_png,
)


def test_grey_is_paper_by_default() -> None:
    """A new canvas is 800x480 and all paper."""
    grey = Canvas().grey()
    assert grey.shape == (HEIGHT, WIDTH)
    assert grey.dtype == np.uint8
    assert int(grey.min()) == PAPER


def test_rect_fills_with_ink() -> None:
    """A rectangle fills its box."""
    canvas = Canvas()
    canvas.rect(box=(10, 10, 20, 20), grey=INK)
    grey = canvas.grey()
    assert int(grey[15, 15]) == INK
    assert int(grey[25, 25]) == PAPER


def test_text_is_aliased_and_returns_width() -> None:
    """Black text on paper uses only ink and paper pixels."""
    canvas = Canvas()
    width = canvas.text(at=(20, 60), text='Parramatta 8:41',
                        face=Face(weight=700, px=18), grey=INK)
    grey = canvas.grey()
    assert width > 50
    assert set(np.unique(grey).tolist()) <= {INK, PAPER}
    assert int(grey[40:65, 20:20 + int(width)].min()) == INK


def test_text_right_anchor_ends_at_x() -> None:
    """A right-anchored string leaves no ink past its anchor."""
    canvas = Canvas()
    canvas.text(at=(700, 100), text='8:41', face=Face(weight=800, px=28),
                grey=INK, anchor='rs')
    assert int(canvas.grey()[:, 702:].min()) == PAPER


def test_fit_keeps_short_text() -> None:
    """Text that fits is unchanged."""
    face = Face(weight=400, px=13)
    assert Canvas().fit(text='Penrith', face=face, width=200) == 'Penrith'


def test_fit_truncates_long_text() -> None:
    """Text too wide is cut and ends with an ASCII ellipsis."""
    canvas = Canvas()
    face = Face(weight=400, px=13)
    fitted = canvas.fit(text='Our Lady Of Mercy College, OConnell St',
                        face=face, width=120)
    assert fitted.endswith('...')
    assert canvas.length(text=fitted, face=face) <= 120


def test_encode_png_has_signature() -> None:
    """PNG output starts with the PNG signature."""
    png = encode_png(grey=Canvas().grey())
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
