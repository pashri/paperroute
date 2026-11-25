"""Tests for map view: sidebar rows."""

from dataclasses import replace

import numpy as np

from paperroute_device.render.canvas import INK, Canvas, Face, text_width
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.connection_view import PairRow
from paperroute_transit.core.stop_view import Pill
from paperroute_transit.render.chrome import badge
from paperroute_transit.render.map_view import (
    BADGE_PX,
    COUNTDOWN_FACE,
    PAIR_STEP,
    PAIRS_TOP,
    SIDEBAR_LEFT,
    SIDEBAR_PAIR_ROOM,
    SIDEBAR_RIGHT,
    SIDEBAR_TEXT,
    draw_map_view,
)
from paperroute_transit.render.stop_view import WIDEST_COUNT
from tests.render.map_view.helpers import ROW_TOPS, VIEW, paper, row


def test_map_sidebar_omits_the_destination_line_without_one() -> None:
    """With no destination the sidebar opens straight on its rows."""
    frame = draw_map_view(
        view=replace(VIEW, destination=None, sidebar=()), base=paper())
    band = frame[HEADER:90, SIDEBAR_LEFT + 3:]
    assert np.all(band == band[0, 0])
    assert np.any(
        draw_map_view(view=replace(VIEW, sidebar=()), base=paper())[
            HEADER:90, SIDEBAR_LEFT + 3:
        ]
        != band
    )


def test_sidebar_minute_countdown_clears_a_wide_badge() -> None:
    """'100 min' fits right of a five-letter badge and of a pair's room."""
    route = badge(Canvas(), x=SIDEBAR_TEXT, y=0, route='SH100', px=BADGE_PX)
    wide = text_width(text=WIDEST_COUNT, face=COUNTDOWN_FACE)
    assert SIDEBAR_TEXT + route + 8 < SIDEBAR_RIGHT - wide
    assert SIDEBAR_TEXT + SIDEBAR_PAIR_ROOM + 8 <= SIDEBAR_RIGHT - wide


def test_carry_on_sidebar_row_stays_inside_and_clear_of_the_time() -> None:
    """Both badges fit the sidebar and end before the countdown starts."""
    top = ROW_TOPS[0]
    count = Canvas().length(text='99 min', face=Face(weight=800, px=34))
    start = int(784 - count)
    band = (slice(top - 8, top - 4), slice(538, start - 2))
    frames = [
        draw_map_view(
            view=replace(
                VIEW,
                sidebar=(
                    row(route='BMT', onto=onto, countdown='99'),
                ) * 5,
            ),
            base=paper(),
        )
        for onto in (None, 'CCN')
    ]
    ends = [
        np.flatnonzero((frame[band] == INK).any(axis=0))[-1]
        for frame in frames
    ]
    assert ends[1] > ends[0] + 30
    assert ends[1] + 538 < start - 4
    assert not np.any(frames[1][top - 14 : top + 13, 785:] == INK)


def test_sidebar_widest_count_clears_a_long_pair() -> None:
    """A BMT to CCN pair fits the sidebar and ends before '100 min'."""
    top = ROW_TOPS[0]
    count = Canvas().length(text=WIDEST_COUNT, face=Face(weight=800, px=34))
    start = int(784 - count)
    band = (slice(top - 8, top - 4), slice(538, start - 2))
    frames = [
        draw_map_view(
            view=replace(
                VIEW,
                sidebar=(
                    row(route='BMT', onto=onto, countdown='100'),
                ) * 5,
            ),
            base=paper(),
        )
        for onto in (None, 'CCN')
    ]
    ends = [
        np.flatnonzero((frame[band] == INK).any(axis=0))[-1]
        for frame in frames
    ]
    assert ends[1] > ends[0] + 30
    assert ends[1] + 538 < start - 4
    assert not np.any(frames[1][top - 14 : top + 13, 785:] == INK)


def test_sidebar_arrival_leaves_out_the_stop_count() -> None:
    """A row with stops draws the same as one without, clear of FASTER."""
    frames = [
        draw_map_view(
            view=replace(
                VIEW,
                sidebar=(
                    replace(row(pill=Pill.FASTER), stops=stops),
                ) * 5,
            ),
            base=paper(),
        )
        for stops in (None, 12)
    ]
    assert np.array_equal(frames[0], frames[1])


PAIR_ROW = PairRow(
    leaves_at='7:33',
    chips=('606', '665'),
    detail='24 min · change at Constitution Hill',
    arrives_at='7:57',
    minutes=24,
)


def test_pairs_replace_the_sidebar() -> None:
    """With pairs, the sidebar heads them and inverts the first."""
    view = replace(VIEW, sidebar=(), pairs=(PAIR_ROW,) * 4)
    frame = draw_map_view(view=view, base=paper())
    plain = draw_map_view(view=replace(VIEW, sidebar=()), base=paper())
    heading = frame[HEADER + 14:HEADER + 36, SIDEBAR_TEXT:SIDEBAR_RIGHT]
    assert (heading == INK).any()
    first = frame[PAIRS_TOP + 8:PAIRS_TOP + PAIR_STEP - 8, 600:700]
    second = frame[
        PAIRS_TOP + PAIR_STEP + 8:PAIRS_TOP + 2 * PAIR_STEP - 8, 600:700,
    ]
    assert (first == INK).mean() > 0.5
    assert (second == INK).mean() < 0.5
    before = plain[HEADER + 14:HEADER + 36, SIDEBAR_TEXT:SIDEBAR_RIGHT]
    assert (heading != before).any()
