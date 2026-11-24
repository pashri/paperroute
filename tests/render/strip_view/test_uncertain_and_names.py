"""Tests for strip view: uncertain and names."""

from dataclasses import replace
from typing import Any, Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.core.text import DOT
from paperroute_device.render.canvas import INK, PAPER, Canvas, text_width
from paperroute_testkit.render.factories import STRIP_PANEL as PANEL
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.stop_view import UNCERTAIN, Estimate, Pill, Row
from paperroute_transit.render import strip_view
from paperroute_transit.render.chrome import (
    NAME_GAP,
    NOTE_GAP,
    badge_width,
    note_room,
)
from paperroute_transit.render.late_marks import mark_room
from paperroute_transit.render.names import fit_headsign
from paperroute_transit.render.stop_view import TAG_FACE
from paperroute_transit.render.strip_view import (
    BADGE_PX,
    CANCELLED_LEFT,
    COUNTDOWN_FACE,
    FASTER_LEFT,
    HEADSIGN_FACE,
    MARK_FACE,
    MIDDLE_X,
    RULE_RIGHT,
    SECOND_DROP,
    TEXT_LEFT,
    TEXT_RIGHT,
    TIME_DROP,
    name_room,
    note_width,
    panel_heading,
)
from paperroute_transit.render.strip_view import panel_row as paint_panel_row
from tests.render.strip_view.helpers import (
    LATE,
    MIDDLE,
    NAMING,
    PANEL_TOP,
    SECOND_LINE,
    WHOLE,
    central,
    panel_frame,
)
from tests.render.test_chrome import Recording

LONG_STOP: Final[str] = (
    'Macquarie University Station, Herring Road Interchange'
)
from tests.helpers import exactly_two

UNSURE: Final[Estimate] = Estimate(due='8:05', late=1, uncertain=True)


def painted(*, row: Row) -> NDArray[np.uint8]:
    """
    Paint one panel row on the destination board and read it back.

    Parameters
    ----------
    row : Row
        The departure.

    Returns
    -------
    NDArray[np.uint8]
        The grey frame.
    """
    canvas = Canvas()
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=False, naming=NAMING,
    )
    return canvas.grey()


def test_uncertain_panel_row_says_so_under_its_countdown() -> None:
    """'uncertain' sits right-aligned under the countdown."""
    row = replace(panel_row(), destination='Emu Plains')
    found = np.argwhere(
        painted(row=row) != painted(row=replace(row, estimate=UNSURE)),
    )
    assert found.size
    assert found[:, 0].min() > PANEL_TOP + TIME_DROP
    assert found[:, 1].min() >= TEXT_RIGHT - note_room(face=TAG_FACE)
    assert found[:, 1].max() <= TEXT_RIGHT


def test_uncertain_carry_on_gives_way_to_the_note() -> None:
    """A long carry-on stops short of 'uncertain' by the gap."""
    then = CarryOn(route='T2', headsign='Leppington via Granville Junction')
    row = replace(
        panel_row(then=then), destination='Emu Plains', estimate=UNSURE,
    )
    start = TEXT_RIGHT - int(note_room(face=TAG_FACE))
    gap = painted(row=row)[SECOND_LINE, start : start + NOTE_GAP - 1]
    assert (gap == PAPER).all()


FASTER_BOX: Final[tuple[slice, slice]] = (
    slice(PANEL_TOP + 51, PANEL_TOP + 66), slice(712, RULE_RIGHT),
)


def test_note_width_adds_the_word_to_faster() -> None:
    """A FASTER row's second line loses the block, a gap and the word."""
    row = replace(panel_row(pill=Pill.FASTER), estimate=UNSURE)
    assert note_width(row=row) == pytest.approx(
        RULE_RIGHT - FASTER_LEFT + NOTE_GAP + note_room(face=TAG_FACE),
    )


def test_row_note_sits_left_of_faster() -> None:
    """'uncertain' ends a gap before the FASTER block, which is unchanged."""
    row = replace(
        panel_row(pill=Pill.FASTER), destination='Emu Plains',
        estimate=UNSURE,
    )
    drawn = painted(row=row)
    plain = painted(row=replace(row, estimate=None))
    found = np.argwhere(drawn != plain)
    assert found.size
    assert found[:, 1].max() <= FASTER_LEFT - NOTE_GAP
    assert found[:, 1].min() >= FASTER_LEFT - NOTE_GAP - note_room(
        face=TAG_FACE,
    )
    assert (drawn[FASTER_BOX] == plain[FASTER_BOX]).all()


def test_row_middle_stops_a_gap_before_uncertain() -> None:
    """An arrival with stops, or a carry-on, ends a gap before the word."""
    then = CarryOn(route='T2', headsign='Leppington via Granville Junction')
    word = int(
        FASTER_LEFT - NOTE_GAP - text_width(text=UNCERTAIN, face=TAG_FACE),
    )
    for row in (
        replace(panel_row(pill=Pill.FASTER), estimate=UNSURE, stops=12),
        replace(panel_row(pill=Pill.FASTER, then=then), estimate=UNSURE),
    ):
        line = painted(row=row)[SECOND_LINE]
        assert (line[:, word - NOTE_GAP + 1:word - 1] == PAPER).all()
        assert (line[:, word - 30:word - NOTE_GAP] == INK).any()


def test_a_carry_on_stops_short_of_faster() -> None:
    """A long carry-on leaves the FASTER box clear by the gap."""
    then = CarryOn(route='T2', headsign='Leppington via Granville Junction')
    row = replace(
        panel_row(then=then, pill=Pill.FASTER), destination='Emu Plains',
    )
    gap = painted(row=row)[SECOND_LINE, 712 - NOTE_GAP + 1 : 712]
    assert (gap == PAPER).all()


LONGEST = (
    'Our Lady Of Mercy College Via Old Windsor Rd And Every Other Street'
)


def test_name_room_ends_at_the_countdowns_left_edge() -> None:
    """Room runs to the count less the gap, to the block, or to the edge."""
    count = text_width(text='6 min', face=COUNTDOWN_FACE)
    assert name_room(row=panel_row(), left=600.0) == (
        TEXT_RIGHT - count - NAME_GAP - 600
    )
    timetabled = replace(panel_row(), live=False)
    assert name_room(row=timetabled, left=600.0) == TEXT_RIGHT - 600
    cancelled = panel_row(pill=Pill.CANCELLED)
    assert name_room(row=cancelled, left=600.0) == (
        CANCELLED_LEFT - NAME_GAP - 600
    )


def test_a_long_name_takes_the_room_a_timetable_row_leaves() -> None:
    """With no countdown a name runs on past the old limit."""
    row = replace(
        panel_row(), live=False, destination='London Liverpool Street',
    )
    frame = panel_frame(panel=WHOLE, row=row)
    assert (frame[PANEL_TOP + 28:PANEL_TOP + 44, 702:784] == INK).any()
    band = (slice(PANEL_TOP + 45, PANEL_TOP + 68), slice(556, 784))
    expected = panel_frame(panel=PANEL, row=row)
    assert (frame[band] == expected[band]).all()


def test_a_name_too_long_for_the_room_wraps_beside_a_centred_badge() -> None:
    """Two lines sit beside a badge 4 px higher than a one-line badge."""
    row = replace(panel_row(), destination='London Liverpool Street')
    two = panel_frame(panel=PANEL, row=row)
    one = panel_frame(panel=PANEL, row=panel_row())
    assert (two[PANEL_TOP + 22:PANEL_TOP + 38, 557] == INK).all()
    assert (two[PANEL_TOP + 41:PANEL_TOP + 44, 557] == PAPER).all()
    assert (one[PANEL_TOP + 26:PANEL_TOP + 43, 557] == INK).all()
    assert (two[SECOND_LINE, MIDDLE] == one[SECOND_LINE, MIDDLE]).all()


def test_a_two_line_name_ends_before_the_countdown() -> None:
    """Both lines stop a gap short of the count's left edge."""
    row = replace(panel_row(), destination=LONGEST)
    frame = panel_frame(panel=PANEL, row=row)
    count = text_width(text='6 min', face=COUNTDOWN_FACE)
    edge = int(TEXT_RIGHT - count)
    band = frame[PANEL_TOP + 14:PANEL_TOP + 46, edge - NAME_GAP + 2:edge - 1]
    assert (band == PAPER).all()


def test_a_name_still_too_long_never_takes_a_third_line() -> None:
    """Past two lines the name is cut, and the pill slot stays clear."""
    row = replace(panel_row(), destination=LONGEST)
    whole = panel_frame(panel=WHOLE, row=row)
    frame = panel_frame(panel=PANEL, row=row)
    band = (slice(PANEL_TOP + 48, PANEL_TOP + 68), slice(556, 784))
    assert (whole[band] == frame[band]).all()
    slot = frame[PANEL_TOP + 47:PANEL_TOP + 68, RULE_RIGHT - 74:RULE_RIGHT]
    assert (slot == PAPER).all()


def test_panel_heading_wraps_a_title_without_a_destination() -> None:
    """Without a destination the title takes both lines."""
    canvas = Recording()
    panel_heading(
        canvas, panel=replace(PANEL, title=LONG_STOP, destination=None),
        drop=0, rules=NameRules(),
    )
    first, second = exactly_two(items=canvas.drawn[-2:])
    assert f'{first} {second}' == LONG_STOP


def test_panel_heading_keeps_one_title_line_beside_a_destination() -> None:
    """With a destination the title is one line."""
    canvas = Recording()
    panel_heading(
        canvas, panel=replace(PANEL, title=LONG_STOP, destination='Wx'),
        drop=0, rules=NameRules(),
    )
    assert canvas.drawn[-1] == 'Wx'
    assert canvas.drawn[-2] != LONG_STOP


def test_panel_row_fits_its_headsign_by_the_rules() -> None:
    """A panel row draws the lines the headsign fitter gives."""
    row = replace(panel_row(), destination='Heathrow Airport Terminal 5')
    canvas = Recording()
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=False, naming=NAMING,
    )
    left = MIDDLE_X + badge_width(px=BADGE_PX) + 8
    expected = fit_headsign(
        text=row.destination, face=HEADSIGN_FACE,
        width=name_room(row=row, left=left), naming=NAMING, two_lines=True,
    )
    assert all(line in canvas.drawn for line in expected)


def test_panel_row_marks_lateness_after_the_countdown() -> None:
    """A panel row four minutes late reads +4."""
    canvas = Recording()
    paint_panel_row(
        canvas, row=replace(panel_row(), estimate=LATE), top=PANEL_TOP,
        whole=False, naming=NAMING,
    )
    assert '+4' in canvas.drawn


def test_name_room_leaves_room_for_the_mark() -> None:
    """The name ends the mark's room further left."""
    marked = replace(panel_row(), estimate=LATE)
    count = text_width(text='6 min', face=COUNTDOWN_FACE)
    assert name_room(row=marked, left=600.0) == pytest.approx(
        TEXT_RIGHT - count - mark_room(row=marked, face=MARK_FACE)
        - NAME_GAP - 600,
    )


def test_panel_row_shows_the_arrival_on_the_whole_board() -> None:
    """A whole-stop row reads its arrival on the second line."""
    canvas = Recording()
    paint_panel_row(
        canvas, row=panel_row(), top=PANEL_TOP, whole=True, naming=NAMING,
    )
    assert 'arr 8:47' in canvas.drawn


def test_continuing_middle_adds_the_arrival_when_it_fits() -> None:
    """A continuing whole-stop row reads 'Central · arr 8:47'."""
    canvas = Recording()
    paint_panel_row(
        canvas, row=central(onto='T8'), top=PANEL_TOP, whole=True,
        naming=NAMING,
    )
    assert f'Central{DOT}arr 8:47' in canvas.drawn


def test_continuing_middle_drops_the_arrival_for_a_long_name() -> None:
    """A long destination beside a carry-on keeps no arrival."""
    row = replace(
        central(onto='T8', then=CarryOn(route='T9', headsign='Hornsby')),
        destination='Macarthur via Airport and Sydenham',
    )
    canvas = Recording()
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=True, naming=NAMING,
    )
    assert not any('arr ' in text for text in canvas.drawn)


def test_continuing_middle_limits_the_pair_to_the_name_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route pair gets the width the name has before the countdown."""
    rooms: list[float | None] = []

    def record(canvas: Canvas, **kwargs: Any) -> float:
        """Note the room the pair was given."""
        del canvas
        rooms.append(kwargs['room'])
        return 0.0

    monkeypatch.setattr(strip_view, 'route_pair', record)
    row = replace(
        central(onto='T8'),
        estimate=Estimate(due='8:07', late=45, uncertain=True),
    )
    paint_panel_row(
        Recording(), row=row, top=PANEL_TOP, whole=True, naming=NAMING,
    )
    assert rooms == [name_room(row=row, left=MIDDLE_X)]


def test_leave_note_sits_under_the_leave_time() -> None:
    """'platform 4' is drawn left of the middle, on the second line."""
    row = replace(panel_row(), note='platform 4')
    found = np.argwhere(painted(row=row) != painted(row=panel_row()))
    assert found.size
    assert found[:, 0].min() >= PANEL_TOP + TIME_DROP + 4
    assert found[:, 0].max() <= PANEL_TOP + SECOND_DROP + 4
    assert found[:, 1].min() >= TEXT_LEFT
    assert found[:, 1].max() < MIDDLE_X - NAME_GAP


def test_panel_row_shows_the_platform_beside_uncertain() -> None:
    """The platform and 'uncertain' each keep their own place."""
    canvas = Recording()
    paint_panel_row(
        canvas,
        row=replace(panel_row(), note='platform 4', estimate=UNSURE),
        top=PANEL_TOP, whole=False, naming=NAMING,
    )
    assert {'platform 4', UNCERTAIN} <= set(canvas.drawn)
