"""Tests for the Skia canvas wrapper."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
    encode_png,
)

LIGHT: Final[int] = 170


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


def _ink_box(grey: NDArray[np.uint8]) -> tuple[int, int]:
    rows, cols = np.nonzero(grey == INK)
    return int(rows.max() - rows.min()), int(cols.max() - cols.min())


def test_text_halo_paints_paper_around_ink() -> None:
    """A halo leaves paper pixels on a grey ground; none without it."""
    face = Face(weight=700, px=18)
    results = []
    for halo in (0, 2):
        canvas = Canvas()
        canvas.rect(box=(0, 0, WIDTH, HEIGHT), grey=LIGHT)
        canvas.text(
            at=(20, 60), text='Map', face=face, grey=INK, halo=halo)
        results.append(canvas.grey())
    assert not (results[0] == PAPER).any()
    assert (results[1] == PAPER).any()


def test_rotated_text_is_taller_than_wide() -> None:
    """Text turned 90 degrees stands upright."""
    canvas = Canvas()
    canvas.text(
        at=(100, 100), text='Parramatta', face=Face(weight=700, px=18),
        grey=INK, angle=90)
    height, width = _ink_box(canvas.grey())
    rows, cols = np.nonzero(canvas.grey() == INK)
    assert height > width
    assert 60 < int(cols.mean()) < 140
    assert 100 <= int(rows.min()) < 130


def test_dashed_line_leaves_gaps() -> None:
    """A dashed line alternates ink and paper."""
    canvas = Canvas()
    canvas.line(points=[(10, 20), (90, 20)], width=2, grey=INK, dash=(4, 4))
    row = canvas.grey()[20, 10:90]
    assert INK in row
    assert PAPER in row


def test_ellipse_ring_has_paper_centre() -> None:
    """A stroked ellipse leaves its middle empty."""
    canvas = Canvas()
    canvas.ellipse(box=(10, 10, 50, 50), grey=INK, stroke=3)
    grey = canvas.grey()
    assert int(grey[30, 30]) == PAPER
    assert int(grey[30, 11]) == INK


def test_rings_even_odd_leaves_hole() -> None:
    """The inner ring is a hole."""
    canvas = Canvas()
    outer = [(10, 10), (60, 10), (60, 60), (10, 60)]
    inner = [(25, 25), (45, 25), (45, 45), (25, 45)]
    canvas.rings(rings=[outer, inner], grey=INK)
    grey = canvas.grey()
    assert int(grey[15, 15]) == INK
    assert int(grey[35, 35]) == PAPER


def test_image_blits_grey_levels() -> None:
    """A grey block reads back at its level."""
    canvas = Canvas()
    block = np.full((4, 6), 85, dtype=np.uint8)
    canvas.image(grey=block, at=(10, 30))
    grey = canvas.grey()
    assert grey[30:34, 10:16].tolist() == block.tolist()
    assert int(grey[34, 16]) == PAPER
    assert int(grey[10, 30]) == PAPER


def test_dots_draws_every_point() -> None:
    """Each point is inked and the space between stays paper."""
    canvas = Canvas()
    points = [(20.0, 20.0), (40.0, 20.0), (60.0, 20.0)]
    canvas.dots(points=points, diameter=7, grey=INK)
    grey = canvas.grey()
    assert all(int(grey[20, int(x)]) == INK for x, _ in points)
    assert int(grey[20, 30]) == PAPER


def test_dots_are_round() -> None:
    """A big dot is ink at its edge midpoints and paper at its corners."""
    canvas = Canvas()
    canvas.dots(points=[(50.0, 50.0)], diameter=21, grey=INK)
    grey = canvas.grey()
    assert int(grey[50, 41]) == INK
    assert int(grey[41, 50]) == INK
    assert int(grey[40, 40]) == PAPER


def test_clipped_keeps_outside_untouched() -> None:
    """Drawing outside the clip box changes nothing."""
    canvas = Canvas()
    with canvas.clipped(box=(0, 0, 50, 50)):
        canvas.rect(box=(0, 0, 100, 100), grey=INK)
    grey = canvas.grey()
    assert int(grey[25, 25]) == INK
    assert int(grey[75, 75]) == PAPER
    canvas.rect(box=(60, 60, 70, 70), grey=INK)
    assert int(canvas.grey()[65, 65]) == INK


def test_scaled_canvas_reads_back_at_its_own_size() -> None:
    """A canvas drawn at 4x reads back at the size it was asked for."""
    canvas = Canvas(width=10, height=6, scale=4)
    canvas.rect(box=(2, 1, 5, 3), grey=INK)
    grey = canvas.grey()
    assert grey.shape == (6, 10)
    assert grey.dtype == np.uint8
    assert (grey[1:3, 2:5] == INK).all()
    assert int(np.count_nonzero(grey == INK)) == 6


def test_scaled_canvas_averages_each_block() -> None:
    """A half-covered pixel reads back as the mean of its sub-pixels."""
    canvas = Canvas(width=8, height=4, scale=4, smooth=True)
    canvas.rect(box=(0, 0, 2.5, 4), grey=INK)
    canvas.rect(box=(4, 0, 4.25, 4), grey=INK)
    row = canvas.grey()[0].tolist()
    assert row == [INK, INK, 128, PAPER, 191, PAPER, PAPER, PAPER]


def test_smooth_text_has_grey_edges() -> None:
    """Smooth text leaves greys between ink and paper."""
    canvas = Canvas(width=120, height=40, scale=4, smooth=True)
    canvas.text(
        at=(10, 30), text='Parramatta', face=Face(weight=700, px=18),
        grey=INK)
    levels = set(np.unique(canvas.grey()).tolist())
    assert {INK, PAPER} < levels


def test_scaled_image_keeps_its_pixels() -> None:
    """A blit onto a scaled canvas reads back unchanged."""
    block = np.arange(24, dtype=np.uint8).reshape(4, 6) * 10
    canvas = Canvas(width=6, height=4, scale=4, smooth=True)
    canvas.image(grey=block, at=(0, 0))
    np.testing.assert_array_equal(canvas.grey(), block)
