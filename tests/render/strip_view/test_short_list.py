"""Tests for strip view: short list."""

from dataclasses import replace
from typing import Final

import numpy as np

from paperroute_device.core.text import DOT
from paperroute_device.render.canvas import INK, PAPER
from paperroute_device.render.frame import HEADER
from paperroute_testkit.render.factories import STRIP_ROWS as ROWS
from paperroute_testkit.render.factories import STRIP_VIEW as VIEW
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.map_view import MAP_HEIGHT, STRIP_MAP_WIDTH
from paperroute_transit.core.stop_view import Estimate, Pill, Row
from paperroute_transit.core.strip_model import (
    PANE_LIST_ROWS,
    PANE_TOP,
    ROW_H,
    KerbMenu,
    StripMapView,
    StripPane,
)
from paperroute_transit.render.kerb_menu import MENU_BOX
from paperroute_transit.render.strip_view import (
    SHORT_RULE,
    SHORT_RULE_DROP,
    SHORT_TOP,
    draw_short_list,
    draw_strip_map_view,
    draw_strip_view,
    short_rows,
    short_text,
)
from tests.render.strip_view.helpers import LATE, NAMING, map_view
from tests.render.test_chrome import Recording

PANE_LEFT: Final[int] = STRIP_MAP_WIDTH + 3


def short_view(
    *,
    rows: tuple[Row, ...],
    destination: str | None = 'Parramatta',
    banner: str | None = None,
) -> StripMapView:
    """Make a Strip + map with these departures and an empty pane."""
    return StripMapView(
        map=replace(
            map_view(), destination=destination, sidebar=rows, banner=banner,
        ),
        pane=StripPane(rows=(), markers=()),
    )


def short_texts(view: StripMapView) -> list[str]:
    """Draw a short list and list its texts."""
    canvas = Recording()
    draw_short_list(canvas, view=view)
    return canvas.drawn


def test_short_rows_skip_cancelled_and_keep_three() -> None:
    """Cancelled rows are left out, and at most three are kept."""
    rows = tuple(
        replace(panel_row(), countdown=str(minutes)) for minutes in range(5)
    )
    gone = replace(rows[1], pill=Pill.CANCELLED)
    assert short_rows(rows=(rows[0], gone, *rows[2:])) == (
        rows[0], rows[2], rows[3],
    )
    assert short_rows(rows=rows[:2]) == rows[:2]


def test_draw_short_list_reads_headsigns_without_a_destination() -> None:
    """Three rows, each its headsign and countdown."""
    drawn = short_texts(short_view(rows=(panel_row(),) * 5, destination=None))
    assert drawn.count('Emu Plains') == 3
    assert drawn.count('6 min') == 3


def test_short_text_leads_with_the_arrival_beside_a_destination() -> None:
    """With a destination the arrival comes first."""
    drawn = short_texts(short_view(rows=(panel_row(),)))
    assert f'arr 8:47{DOT}Emu Plains' in drawn


def test_short_text_with_no_headsign_is_the_arrival() -> None:
    """No headsign leaves the arrival alone, with no dot."""
    assert short_text(
        row=replace(panel_row(), destination=''), width=200.0,
        naming=NAMING, destination=True,
    ) == 'arr 8:47'


def test_draw_short_list_marks_lateness() -> None:
    """A row four minutes late reads +4."""
    assert '+4' in short_texts(
        short_view(rows=(replace(panel_row(), estimate=LATE),)),
    )


def test_draw_short_list_leads_with_uncertain_when_unmarked() -> None:
    """An uncertain row with no mark reads 'uncertain' before its text."""
    row = replace(
        panel_row(), estimate=Estimate(due='8:07', late=0, uncertain=True),
    )
    assert any(
        text.startswith('uncertain') for text in short_texts(
            short_view(rows=(row,)),
        )
    )


def test_short_text_fits_the_headsign_by_the_rules() -> None:
    """A long headsign loses its via tail on the one line."""
    long = 'Penrith via Parramatta Blacktown Mount Druitt and St Marys'
    drawn = short_texts(short_view(
        rows=(replace(panel_row(), destination=long),), destination=None,
    ))
    assert 'Penrith' in drawn


def test_draw_strip_map_view_puts_the_list_above_a_rule() -> None:
    """The rows sit above a 2 px rule; below it the empty pane is paper."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    frame = draw_strip_map_view(
        view=short_view(rows=(panel_row(),) * 3), base=base,
    )
    rule = SHORT_TOP + PANE_LIST_ROWS * ROW_H + SHORT_RULE_DROP
    assert (frame[rule:rule + SHORT_RULE, PANE_LEFT:] == INK).all()
    assert (frame[HEADER:rule, PANE_LEFT:] == INK).any()
    assert (frame[rule + SHORT_RULE:, PANE_LEFT + 1:] == PAPER).all()


def test_draw_short_list_moves_below_the_banner() -> None:
    """Under a banner the list and its rule drop one slot."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    frame = draw_strip_map_view(
        view=short_view(rows=(panel_row(),), banner='No live data'),
        base=base,
    )
    rule = SHORT_TOP + (PANE_LIST_ROWS + 1) * ROW_H + SHORT_RULE_DROP
    assert (frame[rule:rule + SHORT_RULE, PANE_LEFT:] == INK).all()


def test_draw_short_list_draws_nothing_without_departures() -> None:
    """No rows, or only cancelled ones: no text and no rule."""
    for rows in ((), (panel_row(pill=Pill.CANCELLED),)):
        canvas = Recording()
        draw_short_list(canvas, view=short_view(rows=rows))
        assert not canvas.drawn
        assert (canvas.grey() == PAPER).all()


def test_draw_pane_keeps_the_track_out_of_the_list() -> None:
    """A first row's line up to a parent off the top stops at the pane."""
    first = replace(
        ROWS[1], y=PANE_TOP + ROW_H / 2, parent_y=PANE_TOP - ROW_H / 2,
    )
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    frame = draw_strip_map_view(
        view=StripMapView(
            map=map_view(), pane=StripPane(rows=(first,), markers=()),
        ),
        base=base,
    )
    assert (frame[HEADER:PANE_TOP - 2, PANE_LEFT + 1:] == PAPER).all()
    assert (frame[PANE_TOP:PANE_TOP + ROW_H, PANE_LEFT:] == INK).any()


def test_paint_strip_view_draws_the_box_over_the_list() -> None:
    """Draw the box inside its bounds and leave the rest alone."""
    menu = KerbMenu(title='Orchard Close', labels=('Both directions',),
                    cursor=0)
    boxed = draw_strip_view(view=replace(VIEW, menu=menu))
    plain = draw_strip_view(view=VIEW)
    left, top, _, bottom = MENU_BOX
    assert boxed[(top + bottom) // 2, left + 1] == INK
    assert np.array_equal(boxed[:top], plain[:top])
    assert np.array_equal(boxed[bottom:], plain[bottom:])
