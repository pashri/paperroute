"""Facts about the drawn Stop view (no golden images)."""

from dataclasses import replace

import numpy as np

from paperroute.core.model import CarryOn, Mode
from paperroute.core.stop_view import Pill, Row, StopView
from paperroute.render.canvas import INK, PAPER, Canvas, Face, text_width
from paperroute.render.chrome import HEADER, badge
from paperroute.render.stop_view import (
    BOARD_RIGHT,
    LIST_LEFT,
    PILL_LEFT,
    draw_stop_view,
)


def row(
    *,
    pill: Pill | None = None,
    destination: str = 'Parramatta',
    then: CarryOn | None = None,
) -> Row:
    """
    A row for drawing.

    Parameters
    ----------
    pill : Pill | None
        Marker.
    destination : str
        Destination text.
    then : CarryOn | None
        Where the vehicle runs on to.

    Returns
    -------
    Row
        Row.
    """
    return Row(
        route='606', mode=Mode.BUS, platform='B4', destination=destination,
        countdown='9', leaves_at='8:09', arrives_at='8:45', ride_minutes=36,
        live=True, pill=pill, then=then)


BASE = StopView(
    origin='Hollis St Opp Constitution Hill Library', destination='Parramatta Station',
    clock='8:00', hero=row(), rows=(row(), row(pill=Pill.FASTER)),
    banner=None, empty=None, scroll=0)


def test_frame_is_one_bit_800_by_480() -> None:
    """Frames are 480 by 800 and use only ink and paper."""
    frame = draw_stop_view(view=BASE)
    assert frame.shape == (480, 800)
    assert set(frame.flatten().tolist()) <= {0, 255}


def test_header_is_black() -> None:
    """The 40 px header bar is mostly ink."""
    assert draw_stop_view(view=BASE)[2:38, :].mean() < 60


def test_faster_pill_is_inked() -> None:
    """The FASTER pill fills its slot on the second list row."""
    frame = draw_stop_view(view=BASE)
    assert frame[70 + 50 + 32:70 + 50 + 44, 714:784].mean() < 128


def test_cancelled_row_has_block() -> None:
    """A cancelled row shows the wide black CANCELLED block."""
    view = replace(BASE, rows=(row(pill=Pill.CANCELLED),))
    frame = draw_stop_view(view=view)
    assert frame[70 + 12:70 + 36, 600:780].mean() < 128


def test_long_destination_stays_in_list_column() -> None:
    """A very long destination never reaches the minutes column."""
    long_name = (
        'Our Lady Of Mercy College Via Old Windsor Rd And Every Other Street')
    view = replace(BASE, rows=(row(destination=long_name),))
    frame = draw_stop_view(view=view)
    assert frame[70 + 34:70 + 46, 610:640].min() == 255


def test_empty_view_draws_message() -> None:
    """With no departures, a message is drawn mid-screen."""
    view = replace(
        BASE, hero=None, rows=(), empty='No direct services to X')
    frame = draw_stop_view(view=view)
    assert frame[200:300, 50:750].min() == 0


def test_rows_never_run_off_the_bottom() -> None:
    """Under a banner, only the rows that fit are drawn."""
    eight = replace(
        BASE, banner='No live data since 7:56', rows=(row(),) * 8)
    assert draw_stop_view(view=eight)[456:480, 452:790].min() == 255


def test_ferry_hero_says_ferry() -> None:
    """The hero names the vehicle and stop part by mode alone."""
    ferry = replace(BASE, hero=replace(row(), mode=Mode.FERRY))
    bus = draw_stop_view(view=BASE)
    drawn = draw_stop_view(view=ferry)
    assert (drawn[40:70, 0:438] != bus[40:70, 0:438]).any()
    assert (drawn[265:290, 0:438] != bus[265:290, 0:438]).any()


def test_long_empty_message_stays_on_screen() -> None:
    """A very long empty message is cut to fit and leaves the edges clear."""
    message = 'No direct services from here to anywhere ' * 4
    view = replace(BASE, hero=None, rows=(), empty=message)
    frame = draw_stop_view(view=view)
    assert frame[200:300, 0:32].min() == 255
    assert frame[200:300, 768:800].min() == 255
    assert frame[200:300, 32:768].min() == 0


def test_all_cancelled_view_draws_rows_only() -> None:
    """With no hero and no message, the cancelled rows still draw."""
    view = replace(
        BASE, hero=None, empty=None,
        rows=(row(pill=Pill.CANCELLED), row(pill=Pill.CANCELLED)))
    frame = draw_stop_view(view=view)
    assert frame.shape == (480, 800)
    assert frame[70 + 12:70 + 36, 600:780].mean() < 128
    assert frame[40:480, 0:438].min() == 255


def test_carry_on_tag_is_drawn_after_the_headsign() -> None:
    """Ink appears right of the fitted headsign, and the row still fits."""
    then = CarryOn(route='811', headsign='Parramatta Stn')
    top = HEADER + 30
    band = (slice(top + 34, top + 46), slice(0, 800))
    plain = draw_stop_view(view=replace(BASE, hero=None, rows=(row(),)))
    tagged = draw_stop_view(
        view=replace(BASE, hero=None, rows=(row(then=then),)))
    end = 456 + int(Canvas().length(
        text='Parramatta', face=Face(weight=400, px=13)))
    assert (plain[band][:, end + 4:700] == PAPER).all()
    assert (tagged[band][:, end + 4:700] == INK).any()
    long = CarryOn(route='T9', headsign='A very long headsign ' * 5)
    full = draw_stop_view(
        view=replace(BASE, hero=None, rows=(row(then=long),)))
    assert (full[band][:, 700:] == PAPER).all()


def test_carry_on_tag_fits_on_the_board() -> None:
    """On the full-width board the tag follows the headsign inside its width."""
    long = CarryOn(route='T9', headsign='A very long headsign ' * 5)
    view = replace(
        BASE,
        destination=None,
        hero=None,
        rows=(row(destination='Somewhere ' * 9, then=long),),
    )
    frame = draw_stop_view(view=view)
    band = frame[HEADER:HEADER + 48, 120:]
    assert (band[:, :460] == INK).any()
    assert (band[:, 464:580] == PAPER).all()


def test_no_destination_frame_draws_rows_under_the_header() -> None:
    """The first row's badge sits just under the header, with no hero."""
    view = replace(
        BASE, destination=None, hero=None, rows=(row(), row(then=None)))
    frame = draw_stop_view(view=view)
    assert (frame[HEADER:HEADER + 12, 90:140] == PAPER).all()
    assert (frame[HEADER + 12:HEADER + 40, 96:110] == INK).any()
    assert (frame[HEADER:HEADER + 30, 0:90] == INK).any()
    assert (frame[HEADER + 50 + 12:HEADER + 90, 96:110] == INK).any()
    assert (frame[HEADER + 100:, 90:140] == PAPER).all()


def test_no_destination_rows_draw_pills_and_every_countdown() -> None:
    """Board rows carry CANCELLED and FASTER; timetable rows count too."""
    rows = (
        row(pill=Pill.CANCELLED),
        row(pill=Pill.FASTER),
        replace(row(), live=False),
    )
    frame = draw_stop_view(
        view=replace(BASE, destination=None, hero=None, rows=rows))
    top = HEADER
    assert frame[top + 12:top + 36, 600:780].mean() < 128
    assert (frame[top + 50 + 18:top + 50 + 31, 602:672] == INK).any()
    assert (frame[top + 50 + 5:top + 50 + 38, 720:790] == INK).any()
    assert (frame[top + 100 + 5:top + 100 + 38, 720:790] == INK).any()


def test_no_destination_far_row_leaves_the_countdown_slot_empty() -> None:
    """A board row 100+ minutes away shows its time once, at the left."""
    far = replace(row(), countdown='9:51', leaves_at='9:51', far=True)
    frame = draw_stop_view(
        view=replace(BASE, destination=None, hero=None, rows=(far,)))
    top = HEADER
    assert (frame[top + 5:top + 38, 720:790] == PAPER).all()
    assert (frame[top + 5:top + 38, 18:80] == INK).any()


def test_a_real_tag_is_not_cut_under_a_short_headsign() -> None:
    """A short headsign leaves the tag room for its whole text."""
    then = CarryOn(route='811', headsign='Parramatta Stn')
    face = Face(weight=700, px=12)
    canvas = Canvas()
    tag = canvas.length(text='811 Parramatta Stn', face=face)
    head = canvas.length(text='Parramatta', face=Face(weight=400, px=13))
    frame = draw_stop_view(
        view=replace(BASE, hero=None, rows=(row(then=then),)))
    top = HEADER + 30
    ink = np.flatnonzero(
        (frame[top + 34:top + 46, 456:700] == INK).any(axis=0))
    assert ink.max() >= head + 8 + 22 + 4 + tag - 3


def test_minute_countdowns_fit_beside_badges_and_pills() -> None:
    """'99 min' clears a five-letter badge, the FASTER pill and headsigns."""
    wide = text_width(text='99 min', face=Face(weight=800, px=28))
    route = badge(Canvas(), x=LIST_LEFT, y=14, route='SH100', px=14)
    assert LIST_LEFT + route + 8 < 640 - wide
    assert max(PILL_LEFT + 74, BOARD_RIGHT) + 8 < 786 - wide
