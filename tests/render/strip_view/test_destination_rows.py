"""Tests for strip view: destination rows."""

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from paperroute_device.core.text import DOT, HINT_DOT
from paperroute_device.render.canvas import (
    INK,
    PAPER,
    Canvas,
    Face,
    text_width,
)
from paperroute_testkit.render.factories import STRIP_PANEL as PANEL
from paperroute_testkit.render.factories import STRIP_VIEW as VIEW
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.stop_view import Row
from paperroute_transit.core.strip_model import HINTS, OPEN_HINT
from paperroute_transit.render.chrome import ARROW_WIDTH, badge, route_pair
from paperroute_transit.render.stop_view import WIDEST_COUNT
from paperroute_transit.render.strip_view import (
    CARRY_GAP,
    HINT_BOX,
    HINT_FACE,
    HINT_PAD,
    TEXT_RIGHT,
    draw_strip_view,
    paint_strip_view,
)
from paperroute_transit.render.strip_view import panel_row as paint_panel_row
from tests.helpers import exactly_two
from tests.render.strip_view.helpers import (
    MIDDLE,
    NAMING,
    PANEL_STEP,
    PANEL_TOP,
    SECOND_LINE,
    WHOLE,
    central,
    panel_frame,
)

FIRST_LINE = slice(PANEL_TOP + 24, PANEL_TOP + 48)


def drawn_row(
    *, destination: str = 'Emu Plains', then: CarryOn | None = None,
    whole: bool = False,
) -> NDArray[np.uint8]:
    """
    Paint one panel row and read it back.

    Parameters
    ----------
    destination : str
        The row's destination.
    then : CarryOn | None
        Where the vehicle runs on to.
    whole : bool
        True for a whole-stop row.

    Returns
    -------
    NDArray[np.uint8]
        The grey frame.
    """
    canvas = Canvas()
    row = replace(panel_row(then=then), destination=destination)
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=whole, naming=NAMING,
    )
    return canvas.grey()


def test_destination_row_has_badge_and_destination() -> None:
    """With a destination the middle shows the badge and the name."""
    row = drawn_row()
    bare = drawn_row(destination='')
    badge_part = (FIRST_LINE, slice(556, 590))
    assert (row[badge_part] == bare[badge_part]).all()
    assert (row[badge_part] == INK).any()
    assert (row[FIRST_LINE, 610:700] == INK).any()
    assert (bare[FIRST_LINE, 610:700] == PAPER).all()


def test_destination_row_arrival_on_second_line() -> None:
    """The arrival sits on the second line, not in the first."""
    row = drawn_row()
    assert (row[SECOND_LINE, MIDDLE] == INK).any()
    whole = drawn_row(whole=True)
    assert (whole[SECOND_LINE, MIDDLE] == row[SECOND_LINE, MIDDLE]).all()
    assert (row[FIRST_LINE, 556:680] == whole[FIRST_LINE, 556:680]).all()


def test_destination_row_carry_on_after_arrival() -> None:
    """A carry-on extends the second line's ink to the right."""
    then = CarryOn(route='T9', headsign='Hornsby')

    def right_edge(frame: NDArray[np.uint8]) -> int:
        ink = (frame[SECOND_LINE, MIDDLE] == INK).any(0)
        return int(np.nonzero(ink)[0].max())

    assert right_edge(drawn_row(then=then)) > right_edge(drawn_row()) + 10


def test_whole_stop_row_adds_the_arrival() -> None:
    """A whole-stop row is the badge and headsign, with the arrival."""
    canvas = Canvas()
    width = badge(canvas, x=556, y=PANEL_TOP + 34, route='T1', px=14)
    face = Face(weight=400, px=14)
    canvas.text(at=(556 + width + 8, PANEL_TOP + 40), text='Emu Plains',
                face=face, grey=INK)
    expected = canvas.grey()[FIRST_LINE, 556:680]
    row = drawn_row(whole=True)
    assert (row[FIRST_LINE, 556:680] == expected).all()
    assert (
        row[SECOND_LINE, MIDDLE] == drawn_row()[SECOND_LINE, MIDDLE]
    ).all()


def test_continuing_row_drops_the_onto_tag_beside_an_arrival() -> None:
    """With a To, a continuing row draws as if it did not continue."""
    area = np.s_[PANEL_TOP - 2:PANEL_TOP + 68, 456:786]
    tagged = panel_frame(panel=PANEL, row=central(onto='T8'))
    plain = panel_frame(panel=PANEL, row=central(onto=None))
    assert (tagged[area] == plain[area]).all()


def test_whole_board_continuing_row_puts_the_destination_second() -> None:
    """On the whole-stop board the pair is alone on line 1."""
    frame = panel_frame(panel=WHOLE, row=central(onto='T8'))
    assert (frame[SECOND_LINE, 556:600] == INK).any()
    pair_end = 556 + route_pair(
        Canvas(), x=0, y=0, route='T1', onto='T8', px=14,
    )
    assert (frame[FIRST_LINE, int(pair_end) + 4:700] == PAPER).all()
    plain = panel_frame(panel=WHOLE, row=central(onto=None))
    expected = panel_frame(panel=PANEL, row=central(onto=None))
    assert (
        plain[SECOND_LINE, MIDDLE] == expected[SECOND_LINE, MIDDLE]
    ).all()


def test_whole_board_carry_on_follows_the_destination() -> None:
    """A carry-on starts CARRY_GAP after the destination on line 2."""
    then = CarryOn(route='T9', headsign='Hornsby')
    frame = panel_frame(panel=WHOLE, row=central(onto='T8', then=then))
    width = text_width(
        text=f'Central{DOT}arr 8:47', face=Face(weight=400, px=14),
    )
    x = 556 + round(width) + CARRY_GAP
    assert (frame[SECOND_LINE, x:x + int(ARROW_WIDTH)] == INK).any()
    assert (frame[SECOND_LINE, x - 6:x - 1] == PAPER).all()


def test_whole_board_carry_on_after_long_destination_fits() -> None:
    """A long line-2 destination leaves room for the carry-on's label."""
    then = CarryOn(route='T9', headsign='Hornsby')
    row = replace(
        central(onto='T8', then=then),
        destination='Macarthur Airport Sydenham Hornsby Parramatta',
    )
    frame = panel_frame(panel=WHOLE, row=row)
    assert (frame[SECOND_LINE, TEXT_RIGHT + 1:] == PAPER).all()
    assert (frame[SECOND_LINE, TEXT_RIGHT - 36:TEXT_RIGHT] == INK).any()


def test_whole_board_panel_row_draws_both_badges() -> None:
    """On the whole-stop board, a continuing row's pair stands alone."""
    rows = slice(PANEL_TOP + 30, PANEL_TOP + 43)
    pair_width = route_pair(
        Canvas(), x=556, y=0, route='T1', onto='T2', px=14,
    )
    plain_width = badge(Canvas(), x=556, y=0, route='T1', px=14)
    plain, pair = (
        panel_frame(panel=WHOLE, row=panel_row(onto=onto))
        for onto in (None, 'T2')
    )
    edge = int(556 + pair_width)
    assert (pair[rows, edge + 1:edge + 8] == PAPER).all()
    plain_edge = int(556 + plain_width)
    assert (plain[rows, plain_edge + 8:plain_edge + 12] == INK).any()
    count = text_width(text='6 min', face=Face(weight=700, px=22))
    end = min(edge + 80, int(784 - count) - 2)
    assert (pair[rows, edge + 8:end] == PAPER).all()


def assert_pair_clears_the_count(*, row: Row, count_text: str) -> None:
    """
    Check a whole-stop row's first line ends 8 px before its count.

    Parameters
    ----------
    row : Row
        The departure.
    count_text : str
        The countdown the row shows.
    """
    frame = panel_frame(panel=WHOLE, row=row)
    count = text_width(text=count_text, face=Face(weight=700, px=22))
    band = frame[PANEL_TOP + 22:PANEL_TOP + 46, 556:int(784 - count) - 2]
    ink = np.flatnonzero((band == INK).any(axis=0))
    assert ink.size
    assert 556 + ink.max() <= 784 - count - 8


def test_long_route_pair_leaves_the_countdown_clear() -> None:
    """A BMT to CCN pair stops before the time."""
    long = replace(
        panel_row(onto='CCN'), route='BMT', destination='Somewhere ' * 9,
    )
    assert_pair_clears_the_count(row=long, count_text='6 min')


def test_strip_widest_count_clears_a_long_pair() -> None:
    """A BMT to CCN pair stops before a '100 min' count."""
    long = replace(
        panel_row(onto='CCN'),
        route='BMT',
        destination='Somewhere ' * 9,
        countdown='100',
    )
    assert_pair_clears_the_count(row=long, count_text=WIDEST_COUNT)


def test_panel_arrival_with_stops_leaves_the_countdown_clear() -> None:
    """'arr 8:47 · 12 stops' is drawn whole and ends before the time."""
    frames = [
        draw_strip_view(view=replace(
            VIEW,
            panel=replace(
                PANEL, rows=(replace(panel_row(), stops=stops),),
            ),
        ))
        for stops in (None, 12)
    ]
    count = Canvas().length(text='99 min', face=Face(weight=700, px=22))
    band = (slice(PANEL_TOP + 48, PANEL_TOP + 64), slice(556, 784))
    ends = [
        np.flatnonzero((frame[band] == INK).any(axis=0))[-1]
        for frame in frames
    ]
    assert ends[1] > ends[0] + 40
    assert ends[1] + 556 < 784 - count - 4


def test_panel_heading_has_no_station_count() -> None:
    """Nothing is drawn between the Destination line and the first row."""
    frame = draw_strip_view(view=VIEW)
    assert (frame[112:PANEL_TOP - 4, 460:784] == PAPER).all()


def test_fullest_panels_end_on_screen_and_above_the_hint() -> None:
    """Five rows, or four under a banner or above the hint, all fit."""
    full = [
        draw_strip_view(view=replace(
            VIEW,
            banner=banner,
            panel=replace(PANEL, rows=(panel_row(),) * rows, hint=hint),
        ))
        for banner, rows, hint in (
            (None, 5, None),
            ('No live data yet', 4, None),
            (None, 4, HINTS['TO']),
            ('No live data yet', 3, HINTS['TO']),
        )
    ]
    for frame in full:
        assert (frame[PANEL_TOP:476, 456:786] == INK).any()
        assert (frame[476:480, 456:786] == PAPER).all()
    for frame in full[2:]:
        assert (frame[412:420, 456:786] == PAPER).all()


def test_the_picked_panel_row_is_inverted_in_ink_and_paper() -> None:
    """The picked row is paper on ink; the frame stays two-level."""
    canvas = Canvas()
    paint_strip_view(canvas, view=VIEW)
    plain = canvas.grey()
    canvas = Canvas()
    paint_strip_view(
        canvas, view=replace(VIEW, panel=replace(PANEL, selected=1)),
    )
    chosen = canvas.grey()
    assert set(np.unique(chosen).tolist()) == {INK, PAPER}
    top = PANEL_TOP + PANEL_STEP
    box = (slice(top, top + PANEL_STEP - 4), slice(456, 786))
    assert np.array_equal(chosen[box], PAPER - plain[box])
    others = np.ones(chosen.shape, dtype=bool)
    others[box] = False
    assert np.array_equal(chosen[others], plain[others])


def test_strip_hints_fit_the_pill() -> None:
    """Every Strip hint fits its pill without a cut."""
    room = HINT_BOX[2] - HINT_BOX[0] - 2 * HINT_PAD
    for hint in (*HINTS.values(), OPEN_HINT):
        assert text_width(text=hint, face=HINT_FACE) <= room, hint


def test_hint_separator_is_heavier() -> None:
    """In hint type the hint bullet inks at least twice the middle dot."""
    inks = []
    for mark in (HINT_DOT.strip(), DOT.strip()):
        canvas = Canvas()
        canvas.text(at=(100, 100), text=mark, face=HINT_FACE, grey=INK)
        inks.append(int((canvas.grey() == INK).sum()))
    bullet, dot = exactly_two(items=inks)
    assert bullet >= 2 * dot > 0
