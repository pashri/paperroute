"""Tests for map view: sidebar notes."""

from dataclasses import replace
from typing import Final

import numpy as np
import pytest

from paperroute_device.core.text import DOT
from paperroute_device.render.canvas import ELLIPSIS, INK, PAPER, text_width
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.core.map_view import MapView, TripMarks, screen_point
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.stop_view import UNCERTAIN, Estimate, Pill, Row
from paperroute_transit.render.chrome import NOTE_GAP
from paperroute_transit.render.late_marks import mark_room
from paperroute_transit.render.map_view import (
    ARRIVES_DROP,
    COUNTDOWN_FACE,
    FASTER_LEFT,
    MARK_FACE,
    SIDEBAR_RIGHT,
    SIDEBAR_TEXT,
    draw_map_view,
    draw_sidebar,
    end_boxes,
)
from paperroute_transit.render.markers import STOP_HALF, place
from paperroute_transit.render.stop_view import WIDEST_COUNT
from tests.render.map_base.helpers import base
from tests.render.map_view.helpers import (
    CENTRE,
    MPP,
    ROW_TOPS,
    VIEW,
    live,
    paper,
    row,
    stop_pixel,
)
from tests.render.test_chrome import Recording

UNSURE: Estimate = Estimate(due='8:09', late=1, uncertain=True)
from tests.helpers import sole


def test_uncertain_sidebar_row_says_so_under_its_time() -> None:
    """'uncertain' sits on the arrival line, right-aligned to the edge."""
    top = ROW_TOPS[0]
    for pill, limit in ((None, SIDEBAR_RIGHT), (Pill.FASTER, FASTER_LEFT)):
        plain = row(pill=pill)
        frames = [
            draw_map_view(
                view=replace(VIEW, sidebar=(item, *(plain,) * 4)),
                base=paper(),
            )
            for item in (plain, replace(plain, estimate=UNSURE))
        ]
        found = np.argwhere(frames[0] != frames[1])
        assert found.size
        assert found[:, 0].min() >= top + ARRIVES_DROP - 12
        assert found[:, 0].max() <= top + ARRIVES_DROP + 3
        assert found[:, 1].min() >= limit - 60
        assert found[:, 1].max() < limit


def test_end_boxes_shrink_with_the_marks() -> None:
    """The box markers keep clear of shrinks with the mark."""
    def end_width(mpp: int) -> float:
        marks = TripMarks(
            origin=CENTRE, second=(), walk=None, change=None, end=CENTRE,
            lettered=True,
        )
        left, _, right, _ = end_boxes(
            base=replace(base(mpp=mpp), trip=marks),
        )[0]
        return right - left

    assert end_width(45) == pytest.approx(end_width(14) * 0.75)
    assert end_width(25) == pytest.approx(end_width(14) * 0.85)


def test_sidebar_destination_is_shortened_by_the_rules() -> None:
    """The 'to' line is shortened by the rules, then cut to fit."""
    canvas = Recording()
    draw_sidebar(canvas, view=replace(
        VIEW, destination='Great Western Highway Opp Rooty Hill Road North',
        name_rules=NameRules(joining=JOINING_WORDS), pairs=(),
    ))
    (line,) = [text for text in canvas.drawn if text.startswith('to ')]
    assert line.startswith('to Great Western Hwy/Rooty')
    assert line.endswith(ELLIPSIS)


LATE_UNSURE: Estimate = Estimate(due='7:58', late=12, uncertain=True)


def test_sidebar_marks_lateness_after_the_countdown() -> None:
    """A sidebar row four minutes late reads +4."""
    late = Estimate(due='8:05', late=4, uncertain=False)
    canvas = Recording()
    draw_sidebar(
        canvas, view=replace(VIEW, sidebar=(replace(row(), estimate=late),)),
    )
    assert '+4' in canvas.drawn


def test_sidebar_folds_uncertainty_into_its_mark() -> None:
    """+12? replaces the word on the arrival line."""
    canvas = Recording()
    draw_sidebar(
        canvas,
        view=replace(VIEW, sidebar=(replace(row(), estimate=LATE_UNSURE),)),
    )
    assert '+12?' in canvas.drawn
    assert UNCERTAIN not in canvas.drawn


def test_sidebar_pair_yields_to_a_marked_countdown() -> None:
    """A BMT to CCN pair ends clear of '100 min +12?'."""
    top = ROW_TOPS[0]
    marked = replace(
        row(route='BMT', onto='CCN', countdown='100'), estimate=LATE_UNSURE,
    )
    frame = draw_map_view(view=replace(VIEW, sidebar=(marked,)), base=paper())
    group = text_width(text=WIDEST_COUNT, face=COUNTDOWN_FACE) + mark_room(
        row=marked, face=MARK_FACE,
    )
    start = int(SIDEBAR_RIGHT - group)
    band = frame[top - 8:top - 4, SIDEBAR_TEXT:start - 2] == INK
    assert SIDEBAR_TEXT + np.flatnonzero(band.any(axis=0)).max() < start - 4


FROM_ONLY: Final[MapView] = replace(VIEW, destination=None)


LONG: Final[str] = 'Oakington Manor Farm Close Westwick Hall Road'


def sidebar_texts(*rows: Row, view: MapView = FROM_ONLY) -> list[str]:
    """Draw a sidebar of these rows and list its texts."""
    canvas = Recording()
    draw_sidebar(canvas, view=replace(view, sidebar=rows))
    return canvas.drawn


def test_headsign_note_puts_the_headsign_before_its_arrival() -> None:
    """A short headsign is followed by its arrival."""
    assert f'Parramatta{DOT}arr 8:45' in sidebar_texts(row())


def test_headsign_note_drops_the_arrival_for_a_long_headsign() -> None:
    """A headsign that fills the line keeps it to itself."""
    drawn = sidebar_texts(replace(row(), destination=LONG))
    assert not any('arr ' in text for text in drawn)
    assert any(text.startswith('Oakington') for text in drawn)


def test_headsign_note_keeps_the_carry_on_tag() -> None:
    """A run-on tag follows the headsign; the arrival gives way."""
    carried = replace(
        row(), then=CarryOn(route='7', headsign='Saffron Walden'),
    )
    drawn = sidebar_texts(carried)
    assert {'Parramatta', '7 Saffron Walden'} <= set(drawn)
    assert not any('arr ' in text for text in drawn)


def test_note_slot_shows_the_platform() -> None:
    """A train's platform sits at the right of the small line."""
    assert 'platform 3' in sidebar_texts(replace(row(), note='platform 3'))


def test_note_slot_gives_the_platform_way_to_uncertain() -> None:
    """'uncertain' wins the slot when no mark carries it."""
    drawn = sidebar_texts(
        replace(row(), note='platform 3', estimate=UNSURE),
    )
    assert UNCERTAIN in drawn
    assert 'platform 3' not in drawn


def test_headsign_note_stops_short_of_faster() -> None:
    """A long headsign ends a gap before the FASTER block."""
    top = ROW_TOPS[0]
    frame = draw_map_view(
        view=replace(
            FROM_ONLY,
            sidebar=(replace(row(pill=Pill.FASTER), destination=LONG),),
        ),
        base=paper(),
    )
    gap = frame[top + 14:top + 30, FASTER_LEFT - NOTE_GAP + 1:FASTER_LEFT]
    assert (gap == PAPER).all()


def test_headsign_note_with_no_headsign_reads_its_arrival() -> None:
    """With no headsign the line is the arrival alone."""
    assert 'arr 8:45' in sidebar_texts(replace(row(), destination=''))


def test_sidebar_row_with_a_destination_keeps_the_arrival_line() -> None:
    """A pin with a destination draws today's line, without the platform."""
    drawn = sidebar_texts(replace(row(), note='platform 3'), view=VIEW)
    assert 'arr 8:45' in drawn
    assert 'platform 3' not in drawn


def test_badges_keep_clear_of_a_both_sides_capsule() -> None:
    """The capsule's box spans both kerbs; a badge on it slides off."""
    across = (CENTRE[0], CENTRE[1] + 160.0)
    trip = TripMarks(
        origin=CENTRE, second=(), walk=None, change=None, end=None,
        across=across,
    )
    map_base = replace(VIEW.base, trip=trip)
    x, y = screen_point(
        centre=CENTRE, mpp=MPP, point=CENTRE, projection=SYDNEY_PROJECTION,
    )
    _, low = screen_point(
        centre=CENTRE, mpp=MPP, point=across, projection=SYDNEY_PROJECTION,
    )
    boxes = end_boxes(base=map_base)
    assert boxes == [
        (x - STOP_HALF, low - STOP_HALF, x + STOP_HALF, y + STOP_HALF),
    ]
    middle = (x, (y + low) / 2)
    placed = sole(items=place(markers=[live(at=middle)], reserved=boxes))
    assert placed.centre != middle


def test_both_sides_from_is_drawn_as_a_capsule() -> None:
    """The overlay joins the From to the kerb across with paper between."""
    across = (CENTRE[0], CENTRE[1] + 160.0)
    trip = TripMarks(
        origin=CENTRE, second=(), walk=None, change=None, end=None,
        across=across,
    )
    frame = draw_map_view(
        view=replace(VIEW, base=replace(VIEW.base, trip=trip)), base=paper(),
    )
    x, y = stop_pixel()
    assert frame[y, x] == INK and frame[y - 20, x] == INK
    assert frame[y - 10, x] == PAPER
    assert frame[y - 10, x + 12] == INK
