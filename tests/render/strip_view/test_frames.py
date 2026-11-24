"""Tests for strip view: frames."""

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_device.render.frame import HEADER
from paperroute_testkit.render.factories import STRIP_PANEL as PANEL
from paperroute_testkit.render.factories import STRIP_ROWS as ROWS
from paperroute_testkit.render.factories import STRIP_VIEW as VIEW
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.stop_view import Pill
from paperroute_transit.core.strip_model import (
    HINTS,
    OPEN_HINT,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    Band,
)
from paperroute_transit.render.chrome import BANNER
from paperroute_transit.render.strip_view import (
    draw_strip_view,
    paint_strip_view,
)
from tests.render.strip_view.helpers import PANEL_STEP, PANEL_TOP


def test_strip_frame_is_two_level() -> None:
    """Drawing makes only ink and paper, before any threshold."""
    live = Marker(route='T1', x=TRACK_X, y=150.0, bearing=180.0, live=True,
                  pinned=True)
    stale = replace(live, y=300.0, bearing=None, live=False, age=240.0)
    rows = tuple(
        replace(row, cursor=index == 6, tag='TO?' if index == 6 else row.tag)
        for index, row in enumerate(ROWS[1:], start=1)
    )
    view = replace(
        VIEW,
        picking='TO',
        rows=rows,
        band=Band(
            text='FROM Town Hall · 3 stops away', top=True, y=STRIP_TOP,
        ),
        markers=(live, stale),
        panel=replace(PANEL, rows=(panel_row(),) * 3, hint=HINTS['TO']),
        banner='Live data unavailable',
    )
    canvas = Canvas()
    paint_strip_view(canvas, view=view)
    drawn = canvas.grey()
    assert set(np.unique(drawn).tolist()) == {INK, PAPER}
    assert (draw_strip_view(view=view) == drawn).all()


def test_strip_frame_has_rule_and_header() -> None:
    """The header is ink and a 3 px rule splits the strip from the panel."""
    frame = draw_strip_view(view=VIEW)
    assert frame[2:38, :400].mean() < 80
    assert (frame[HEADER:, 440:443] == INK).all()
    assert (frame[HEADER + 2:, 443:447] == PAPER).all()


def test_strip_frame_draws_rows_on_the_track() -> None:
    """The track runs down x 30 between the rows."""
    frame = draw_strip_view(view=VIEW)
    between = int(STRIP_TOP + 4 * ROW_H)
    assert frame[between, TRACK_X] == INK


def test_panel_draws_carry_on_second_line() -> None:
    """A departure that runs on gets a second line under it."""
    then = CarryOn(route='T9', headsign='Hornsby')
    plain = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),)),
    ))
    carried = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(then=then),)),
    ))
    line = (slice(PANEL_TOP + 52, PANEL_TOP + 66), slice(660, 786))
    assert (plain[line] == PAPER).all()
    assert (carried[line] == INK).any()


def test_no_destination_panel_has_no_destination_line() -> None:
    """With no destination the line under the title stays empty."""
    view = replace(
        VIEW,
        destination=None,
        panel=replace(PANEL, destination=None, no_destination=True),
    )
    line = (slice(88, 106), slice(456, 786))
    assert (draw_strip_view(view=VIEW)[line] == INK).any()
    assert (draw_strip_view(view=view)[line] == PAPER).all()


def test_no_destination_panel_rows_show_badges() -> None:
    """A whole-stop panel row has an ink route badge after the time."""
    view = replace(
        VIEW,
        destination=None,
        panel=replace(PANEL, destination=None, no_destination=True),
    )
    frame = draw_strip_view(view=view)
    badge = frame[PANEL_TOP + 26:PANEL_TOP + 42, 556:590]
    assert badge.mean() < 128


def test_panel_rows_are_ruled_and_spaced() -> None:
    """Each departure starts under a rule, 70 px apart."""
    frame = draw_strip_view(view=VIEW)
    for index in range(4):
        y = PANEL_TOP + index * PANEL_STEP
        assert (frame[y - 2, 460:780] == INK).all()


def test_hint_box_replaces_the_fourth_departure() -> None:
    """The hint is an ink box at the panel's foot with paper text."""
    view = replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),) * 3, hint=HINTS['TO']),
    )
    box = draw_strip_view(view=view)[420:464, 456:786]
    assert box.mean() < 128
    assert (box == PAPER).any()


def test_open_hint_draws_in_the_hint_box() -> None:
    """The fold hint fills the same box with its own paper text."""
    def box(hint: str) -> NDArray[np.uint8]:
        view = replace(
            VIEW, panel=replace(PANEL, rows=(panel_row(),) * 3, hint=hint),
        )
        return draw_strip_view(view=view)[420:464, 456:786]
    opens = box(OPEN_HINT)
    assert opens.mean() < 128
    assert (opens == PAPER).any()
    assert (opens != box(HINTS['TO'])).any()


def test_panel_draws_empty_text() -> None:
    """With no departures, the panel says why."""
    empty = replace(VIEW, panel=replace(PANEL, rows=()))
    said = replace(
        VIEW, panel=replace(PANEL, rows=(), empty='No more services today'),
    )
    area = (slice(PANEL_TOP, PANEL_TOP + 60), slice(456, 786))
    assert (draw_strip_view(view=empty)[area] == PAPER).all()
    assert (draw_strip_view(view=said)[area] == INK).any()


def test_cancelled_panel_row_has_block() -> None:
    """A cancelled departure shows a block where the countdown goes."""
    view = replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(pill=Pill.CANCELLED),)),
    )
    block = draw_strip_view(view=view)[PANEL_TOP + 22:PANEL_TOP + 42, 700:780]
    assert block.mean() < 128


def test_timetable_departure_leaves_the_countdown_slot_empty() -> None:
    """A departure without live data doesn't repeat its time as a count."""
    timetabled = replace(panel_row(), live=False)
    view = replace(VIEW, panel=replace(PANEL, rows=(timetabled,)))
    slot = draw_strip_view(view=view)[PANEL_TOP + 20:PANEL_TOP + 48, 700:786]
    assert (slot == PAPER).all()
    near = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),)),
    ))
    assert (near[PANEL_TOP + 20:PANEL_TOP + 48, 700:786] == INK).any()


def test_live_departure_counts_down_past_100() -> None:
    """A live departure 130 minutes away still fills the slot."""
    later = replace(panel_row(), countdown='130')
    view = replace(VIEW, panel=replace(PANEL, rows=(later,)))
    slot = draw_strip_view(view=view)[PANEL_TOP + 20:PANEL_TOP + 48, 690:786]
    assert (slot == INK).any()


def test_empty_strip_says_why() -> None:
    """With no strip, its column shows the reason and no track."""
    view = replace(VIEW, rows=(), empty='Loading the line…')
    frame = draw_strip_view(view=view)
    assert (frame[200:300, 40:400] == INK).any()
    assert (frame[HEADER:, TRACK_X - 2:TRACK_X + 2] == PAPER).all()


def test_banner_is_drawn_over_the_strip() -> None:
    """A live data banner sits under the header."""
    frame = draw_strip_view(view=replace(VIEW, banner='Live data unavailable'))
    assert (frame[HEADER:HEADER + 3, :] == INK).all()


def test_panel_moves_below_the_banner() -> None:
    """Under a banner the whole panel is drawn a banner's height lower."""
    plain = draw_strip_view(view=VIEW)
    bannered = draw_strip_view(view=replace(VIEW, banner='No live data'))
    panel = slice(447, 800)
    assert (
        bannered[HEADER + BANNER:, panel] == plain[HEADER:-BANNER, panel]
    ).all()


def test_band_shows_below_the_banner() -> None:
    """A top band in the first slot under the banner stays in view."""
    y = STRIP_TOP + ROW_H
    view = replace(
        VIEW,
        rows=(),
        band=Band(text='FROM Town Hall · 3 stops away', top=True, y=y),
        banner='No live data',
    )
    frame = draw_strip_view(view=view)
    box = frame[y + 4:y + ROW_H - 4, 12:400]
    assert box.mean() < 128
    assert (box[:, 40:] == PAPER).any()
