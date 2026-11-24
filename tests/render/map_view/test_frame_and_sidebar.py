"""Tests for map view: frame and sidebar."""

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
    text_width,
)
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
)
from paperroute_transit.core.stop_view import Pill
from paperroute_transit.render.chrome import BANNER, view_header
from paperroute_transit.render.map_view import (
    EMPTY_MARGIN,
    SIDEBAR_LEFT,
    draw_map_view,
    empty_lines,
    snap,
)
from paperroute_transit.render.tile_codec import GREYS
from tests.render.map_view.helpers import (
    LONG_EMPTY,
    ROW_TOPS,
    VIEW,
    live,
    paper,
    row,
)


def threshold(*, canvas: Canvas) -> NDArray[np.uint8]:
    """
    Snap a reference canvas to ink and paper.

    Parameters
    ----------
    canvas : Canvas
        The drawn reference.

    Returns
    -------
    NDArray[np.uint8]
        Its pixels as ink or paper.
    """
    return np.where(canvas.grey() < 128, INK, PAPER).astype(np.uint8)


def test_map_frame_is_800_by_480_with_four_levels() -> None:
    """The map keeps four greys; everything else is ink or paper."""
    base = np.repeat(
        np.array(GREYS, dtype=np.uint8),
        MAP_WIDTH // len(GREYS),
    )
    frame = draw_map_view(view=VIEW, base=np.tile(base, (MAP_HEIGHT, 1)))
    assert frame.shape == (HEIGHT, WIDTH)
    assert frame.dtype == np.uint8
    assert set(np.unique(frame[HEADER:, :SIDEBAR_LEFT])) == set(GREYS)
    assert set(np.unique(frame[:HEADER])) <= {INK, PAPER}
    assert set(np.unique(frame[:, SIDEBAR_LEFT:])) <= {INK, PAPER}


def test_header_marks_the_map_tab() -> None:
    """The frame's header is the shared header with Map active."""
    canvas = Canvas()
    view_header(canvas, view=VIEW, active='Map')
    expected = threshold(canvas=canvas)
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.array_equal(frame[:HEADER], expected[:HEADER])


def test_rule_divides_map_and_sidebar() -> None:
    """A 3 px ink rule runs down from the header at x 520 to 523."""
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.all(frame[HEADER:, SIDEBAR_LEFT:SIDEBAR_LEFT + 3] == INK)
    assert np.all(frame[HEADER:, SIDEBAR_LEFT + 3] == PAPER)


def test_sidebar_draws_each_row() -> None:
    """Each row has a badge, a countdown, an arrival and a rule."""
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.any(frame[56:74, 538:788] == INK)
    for top in ROW_TOPS:
        assert frame[top, 540] == INK
        assert np.any(frame[top - 14 : top + 13, 740:785] == INK)
        assert not np.any(frame[top - 14 : top + 13, 785:800] == INK)
        assert np.any(frame[top + 15 : top + 27, 538:600] == INK)
        assert np.all(frame[top + 40, 534:788] == INK)


def test_destination_reads_to_the_destination() -> None:
    """The sidebar opens with "to" and the destination, fitted."""
    canvas = Canvas()
    face = Face(weight=700, px=16)
    canvas.text(
        at=(538, 70),
        text=canvas.fit(text='to Parramatta Station', face=face, width=250),
        face=face,
        grey=INK,
    )
    expected = threshold(canvas=canvas)
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.array_equal(frame[HEADER:90, 524:], expected[HEADER:90, 524:])


def test_cancelled_row_shows_a_block() -> None:
    """A cancelled row has an ink block where the countdown goes."""
    view = replace(VIEW, sidebar=(row(pill=Pill.CANCELLED),))
    frame = draw_map_view(view=view, base=paper())
    top = ROW_TOPS[0]
    assert frame[top - 12 : top + 12, 640:784].mean() < 128
    assert frame[top - 12 : top + 12, 785:800].min() == PAPER


def test_faster_row_has_a_pill() -> None:
    """A faster row has an ink pill right of its arrival time."""
    view = replace(VIEW, sidebar=(row(pill=Pill.FASTER),))
    frame = draw_map_view(view=view, base=paper())
    top = ROW_TOPS[0]
    assert frame[top + 17 : top + 32, 712:784].mean() < 128
    plain = draw_map_view(view=VIEW, base=paper())
    assert plain[top + 17 : top + 32, 712:784].min() == PAPER


def test_scheduled_row_shows_its_clock_time() -> None:
    """A row that isn't live shows when it leaves, not a countdown."""
    scheduled = replace(VIEW, sidebar=(row(live=False),))
    clock = replace(VIEW, sidebar=(row(countdown='8:09'),))
    assert np.array_equal(
        draw_map_view(view=scheduled, base=paper()),
        draw_map_view(view=clock, base=paper()),
    )


def test_sidebar_moves_below_the_banner() -> None:
    """With a banner the sidebar's content starts below it."""
    plain = draw_map_view(view=VIEW, base=paper())
    view = replace(VIEW, banner='Showing timetable only')
    frame = draw_map_view(view=view, base=paper())
    below = slice(SIDEBAR_LEFT + 3, WIDTH)
    assert np.array_equal(
        frame[HEADER + BANNER :, below],
        plain[HEADER : HEIGHT - BANNER, below],
    )
    assert not np.any(plain[HEIGHT - BANNER :, below] == INK)


def test_sidebar_stops_after_its_rows() -> None:
    """With two rows the rest of the sidebar is paper."""
    view = replace(VIEW, sidebar=(row(),) * 2)
    frame = draw_map_view(view=view, base=paper())
    assert np.all(frame[ROW_TOPS[2] - 20 :, SIDEBAR_LEFT + 3 :] == PAPER)


def test_markers_stay_inside_the_map() -> None:
    """A badge at the map's right edge never inks the sidebar."""
    plain = draw_map_view(view=VIEW, base=paper())
    marked = draw_map_view(
        view=replace(VIEW, markers=(live(at=(515.0, 200.0)),)),
        base=paper(),
    )
    changed = np.argwhere(plain != marked)
    assert changed.size
    assert changed[:, 1].max() < SIDEBAR_LEFT


def test_overlay_is_clipped_to_the_base_width() -> None:
    """At 480 px a badge at x 500 is clipped; at 520 px it is drawn."""
    badge = (live(at=(500.0, 200.0)),)
    narrow = replace(VIEW, base=replace(VIEW.base, width=STRIP_MAP_WIDTH))
    base = paper()[:, :STRIP_MAP_WIDTH]
    plain = draw_map_view(view=narrow, base=base)
    marked = draw_map_view(view=replace(narrow, markers=badge), base=base)
    assert np.array_equal(
        plain[:, STRIP_MAP_WIDTH:],
        marked[:, STRIP_MAP_WIDTH:],
    )
    wide_plain = draw_map_view(view=VIEW, base=paper())
    wide = draw_map_view(view=replace(VIEW, markers=badge), base=paper())
    assert np.any(wide_plain[:, STRIP_MAP_WIDTH:] != wide[:, STRIP_MAP_WIDTH:])


def test_narrow_stop_is_drawn_at_the_narrow_centre() -> None:
    """The stop is over the 480 px map's middle, not the 520 px one's."""
    narrow = replace(VIEW, base=replace(VIEW.base, width=STRIP_MAP_WIDTH))
    frame = draw_map_view(view=narrow, base=paper()[:, :STRIP_MAP_WIDTH])
    middle = HEADER + MAP_HEIGHT // 2
    assert frame[middle, STRIP_MAP_WIDTH // 2] == INK
    assert frame[middle, MAP_WIDTH // 2 + 8] == PAPER


def test_narrow_empty_text_is_centred_and_wrapped_within_the_width() -> None:
    """On a 480 px map the empty text is centred at 240 and fits."""
    narrow = replace(
        VIEW,
        base=replace(VIEW.base, width=STRIP_MAP_WIDTH),
        empty=LONG_EMPTY,
    )
    frame = draw_map_view(view=narrow, base=paper()[:, :STRIP_MAP_WIDTH])
    middle = HEADER + MAP_HEIGHT // 2
    band = frame[middle + 40 : middle + 100, :STRIP_MAP_WIDTH]
    ink = np.flatnonzero(np.any(band == INK, axis=0))
    assert ink.size
    assert ink.min() >= EMPTY_MARGIN - 4
    assert ink.max() <= STRIP_MAP_WIDTH - EMPTY_MARGIN + 4
    assert abs((ink.max() + ink.min() + 1) / 2 - STRIP_MAP_WIDTH / 2) <= 12
    lines, face = empty_lines(text=LONG_EMPTY, width=STRIP_MAP_WIDTH)
    room = STRIP_MAP_WIDTH - 2 * EMPTY_MARGIN
    assert all(text_width(text=line, face=face) <= room for line in lines)


def test_snap_follows_the_map_width() -> None:
    """On a 480 px map, columns from 480 on stay ink or paper."""
    grey = np.full((HEIGHT, WIDTH), 100, dtype=np.uint8)
    out = snap(grey=grey, width=STRIP_MAP_WIDTH)
    assert np.all(out[HEADER:, STRIP_MAP_WIDTH:] == INK)
    assert np.all(out[HEADER:, :STRIP_MAP_WIDTH] == GREYS[1])


def test_banner_is_drawn_over_the_map() -> None:
    """The banner's paper box covers a dark map."""
    dark = np.full((MAP_HEIGHT, MAP_WIDTH), INK, dtype=np.uint8)
    view = replace(VIEW, banner='Showing timetable only')
    assert draw_map_view(view=VIEW, base=dark)[HEADER + 6, 400] == INK
    frame = draw_map_view(view=view, base=dark)
    assert frame[HEADER + 6, 400] == PAPER
    assert frame[HEADER + 6, SIDEBAR_LEFT + 1] == PAPER
