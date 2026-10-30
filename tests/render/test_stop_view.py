"""Facts about the drawn Stop view (no golden images)."""

from dataclasses import replace

import numpy as np
import pytest

from paperroute.core.model import CarryOn, Mode
from paperroute.core.stop_view import Pill
from paperroute.render.canvas import INK, PAPER, Canvas, Face, text_width
from paperroute.render.chrome import BANNER, HEADER, badge, route_pair
from paperroute.render.stop_view import (
    FASTER_LEFT,
    LARGE_ROW_H,
    LIST_LEAVE_RIGHT,
    LIST_LEFT,
    LIST_PAIR_ROOM,
    PILL_LEFT,
    ROW_H,
    SPLIT,
    WIDEST_COUNT,
    Scale,
    board_right,
    board_row,
    draw_stop_view,
    faster_left,
    faster_width,
    finish,
    list_row,
)
from tests.render.factories import STOP_VIEW as BASE
from tests.render.factories import stop_row as row


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


def test_no_destination_rows_draw_pills_and_live_countdowns() -> None:
    """Board rows carry CANCELLED and FASTER; only live rows count down."""
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
    assert (frame[top + 100 + 5:top + 100 + 38, 720:790] == PAPER).all()


def test_board_timetable_row_shows_its_time_once() -> None:
    """A board row without live data shows its time once, at the left."""
    timetabled = replace(row(), live=False)
    frame = draw_stop_view(
        view=replace(BASE, destination=None, hero=None, rows=(timetabled,)))
    top = HEADER
    assert (frame[top + 5:top + 38, 720:790] == PAPER).all()
    assert (frame[top + 5:top + 38, 18:80] == INK).any()


def test_board_live_row_counts_down_past_100() -> None:
    """A live board row 130 minutes away still fills its countdown slot."""
    later = replace(row(), countdown='130')
    frame = draw_stop_view(
        view=replace(BASE, destination=None, hero=None, rows=(later,)))
    top = HEADER
    assert (frame[top + 5:top + 38, 680:790] == INK).any()


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


def test_widest_count_clears_list_pairs_and_board_pills() -> None:
    """'100 min' clears pairs, badges, the arrivals and the board pill."""
    wide = text_width(text=WIDEST_COUNT, face=Face(weight=800, px=28))
    large = text_width(text=WIDEST_COUNT, face=Face(weight=800, px=35))
    arrive = text_width(text='88:88', face=Face(weight=800, px=25))
    route = badge(Canvas(), x=LIST_LEFT, y=14, route='SH100', px=14)
    assert LIST_LEFT + LIST_PAIR_ROOM + 8 <= LIST_LEAVE_RIGHT - wide
    assert LIST_LEFT + LIST_PAIR_ROOM + 8 <= LIST_LEAVE_RIGHT - large
    assert LIST_LEAVE_RIGHT + 16 <= min(FASTER_LEFT, 786 - arrive)
    assert LIST_LEFT + route + 8 < LIST_LEAVE_RIGHT - wide
    for k, count in ((Scale(), wide), (Scale(large=True), large)):
        assert faster_left(k=k) + faster_width(k=k) + 8 < 786 - count


@pytest.mark.parametrize('large', [False, True], ids=['normal', 'large'])
def test_list_widest_count_clears_a_long_pair(large: bool) -> None:
    """A '100 min' count starts clear of a BMT to CCN pair, in real ink."""
    canvas = Canvas()
    y = 100
    long = replace(
        row(pill=Pill.FASTER, onto='CCN'),
        route='BMT',
        countdown='100',
        arrives_at='23:58',
    )
    list_row(canvas, row=long, y=y, large=large)
    frame = finish(canvas)
    rows = slice(y, y + Scale(large=large).y(31))
    ink = np.flatnonzero((frame[rows] == INK).any(axis=0))
    room = LIST_LEFT + LIST_PAIR_ROOM
    pair_right = ink[(ink >= LIST_LEFT) & (ink <= room)].max()
    count = ink[(ink > room) & (ink < FASTER_LEFT)]
    assert count.min() - pair_right >= 8
    assert count.max() <= FASTER_LEFT - 16


def test_faster_geometry_follows_the_text_size() -> None:
    """The board's FASTER pill grows and moves left with larger text."""
    normal, large = Scale(), Scale(large=True)
    assert (faster_width(k=normal), faster_width(k=large)) == (74, 95)
    assert (faster_left(k=normal), faster_left(k=large)) == (596, 549)
    assert (board_right(k=normal), board_right(k=large)) == (576, 529)


@pytest.mark.parametrize('large', [False, True], ids=['normal', 'large'])
def test_faster_pill_clears_the_widest_count(large: bool) -> None:
    """The FASTER pill ends clear of a '100 min' count, in real ink."""
    k = Scale(large=large)
    canvas = Canvas()
    y = 100
    board_row(
        canvas,
        row=replace(row(pill=Pill.FASTER), countdown='100', live=True),
        y=y,
        large=large,
    )
    frame = finish(canvas)
    band = frame[y + k.y(17):y + k.y(32)]
    ink = np.flatnonzero((band == INK).any(axis=0))
    count = text_width(text=WIDEST_COUNT, face=k.face(weight=800, px=28))
    pill_right = ink[ink < 786 - count - 4].max()
    count_left = ink[ink > pill_right + 1].min()
    assert count_left - pill_right >= 8


@pytest.mark.parametrize('large', [False, True], ids=['normal', 'large'])
def test_board_headsign_stops_before_the_pill(large: bool) -> None:
    """A long headsign ends before the FASTER pill at both sizes."""
    k = Scale(large=large)
    canvas = Canvas()
    y = 100
    board_row(
        canvas,
        row=row(pill=Pill.FASTER, destination='Somewhere ' * 9),
        y=y,
        large=large,
    )
    frame = finish(canvas)
    gap = frame[
        y:y + k.y(49),
        board_right(k=k) + 4:faster_left(k=k) - 2,
    ]
    assert (gap == PAPER).all()


def test_live_hero_widest_count_fits_left_of_the_split() -> None:
    """The hero's '100 min' ends before the rule beside it."""
    widest = draw_stop_view(
        view=replace(BASE, hero=replace(row(), countdown='100')),
    )
    top = HEADER
    assert (widest[top + 60:top + 170, SPLIT - 12:SPLIT] == PAPER).all()


def _ink_extent(*, frame: np.ndarray, band: tuple[slice, slice]) -> int:
    """Return the rightmost x holding ink in a band, or -1."""
    columns = np.flatnonzero((frame[band] == INK).any(axis=0))
    return int(columns[-1]) + band[1].start if columns.size else -1


def test_list_row_draws_both_badges() -> None:
    """A carry-on row's badges reach further right than a plain row's."""
    top = HEADER + 30
    band = (slice(top + 4, top + 9), slice(440, 640))
    views = [
        replace(BASE, hero=None, rows=(row(onto=onto),))
        for onto in (None, 'T2')
    ]
    plain, pair = (draw_stop_view(view=view) for view in views)
    assert _ink_extent(frame=pair, band=band) > _ink_extent(
        frame=plain, band=band,
    ) + 30


def _first_ink(
    *, frame: np.ndarray, rows: slice, start: int, stop: int,
) -> int | None:
    """Return the first column in [start, stop) with ink in the rows."""
    ink = np.flatnonzero((frame[rows, start:stop] == INK).any(axis=0))
    return int(ink[0]) + start if ink.size else None


def test_hero_destination_starts_after_both_badges() -> None:
    """The hero's destination starts past the pair's right edge."""
    rows = slice(HEADER + 40, HEADER + 60)
    pair_width = route_pair(
        Canvas(), x=18, y=0, route='606', onto='T2', px=20,
    )
    plain_width = badge(Canvas(), x=18, y=0, route='606', px=20)
    pair = draw_stop_view(view=replace(BASE, hero=row(onto='T2')))
    plain = draw_stop_view(view=replace(BASE, hero=row()))
    edge = int(18 + pair_width)
    start = _first_ink(frame=pair, rows=rows, start=edge + 1, stop=438)
    assert start is not None
    assert start >= edge + 12
    assert (pair[rows, edge + 1:start] == PAPER).all()
    plain_start = _first_ink(
        frame=plain, rows=rows, start=int(18 + plain_width) + 1, stop=438,
    )
    assert plain_start is not None
    assert plain_start < start


def test_long_route_pair_fits_every_row_style() -> None:
    """A BMT to CCN pair stays clear of the pill and the countdown."""
    long = replace(
        row(onto='CCN', destination='Somewhere ' * 9),
        route='BMT',
        countdown='99',
    )
    top = HEADER + 30
    list_view = replace(BASE, hero=None, rows=(long,))
    frame = draw_stop_view(view=list_view)
    badges = (slice(top + 6, top + 9), slice(440, 640))
    count = Canvas().length(text='99 min', face=Face(weight=800, px=28))
    end = _first_ink(frame=frame, rows=badges[0], start=440, stop=640)
    assert end is not None
    ink = np.flatnonzero((frame[badges] == INK).any(axis=0))
    assert ink[-1] + 440 < min(PILL_LEFT, 640 - count)
    hero = draw_stop_view(view=replace(BASE, hero=long))
    assert (hero[HEADER + 40:HEADER + 60, 426:437] == PAPER).all()
    board = draw_stop_view(
        view=replace(BASE, destination=None, hero=None, rows=(long,)),
    )
    normal = Scale()
    gap = np.s_[
        HEADER:HEADER + 48,
        board_right(k=normal) + 4:faster_left(k=normal) - 2,
    ]
    assert (board[gap] == PAPER).all()


def test_carry_on_pill_follows_boarded_trip() -> None:
    """A cancelled boarded trip draws its pill on the carry-on row."""
    top = HEADER + 30
    band = (slice(top + 10, top + 38), slice(600, 786))
    shown = replace(BASE, hero=None, rows=(row(onto='T2'),))
    cancelled = replace(
        BASE, hero=None, rows=(row(onto='T2', pill=Pill.CANCELLED),),
    )
    assert (draw_stop_view(view=shown)[band] == INK).mean() < 0.2
    assert (draw_stop_view(view=cancelled)[band] == INK).mean() > 0.5


def test_hero_ride_line_adds_the_stop_count() -> None:
    """The hero's ride line gains the stop count and stays on screen."""
    counted = replace(BASE, hero=replace(row(), stops=12))
    plain = draw_stop_view(view=BASE)
    drawn = draw_stop_view(view=counted)
    band = (slice(HEADER + 226, HEADER + 244), slice(0, 438))
    assert (drawn[band] != plain[band]).any()
    assert (drawn[band][:, 419:] == PAPER).all()


def _last_rule(*, frame: np.ndarray) -> int:
    """
    The lowest y whose pixels are inked across the whole list column.

    Parameters
    ----------
    frame : np.ndarray
        Frame.

    Returns
    -------
    int
        Row index.
    """
    ruled = (frame[:, 460:780] == INK).all(axis=1)
    return int(np.flatnonzero(ruled).max())


def test_large_rows_are_taller() -> None:
    """Larger rows put the last rule lower and the text taller."""
    rows = (row(),) * 5
    small = draw_stop_view(view=replace(BASE, rows=rows))
    large = draw_stop_view(view=replace(BASE, rows=rows, large=True))
    assert _last_rule(frame=large) > _last_rule(frame=small)
    assert LARGE_ROW_H == 64
    top = HEADER + 30
    band = slice(456, 786)
    ink_small = (small[top:top + ROW_H - 2, band] == INK).sum()
    ink_large = (large[top:top + LARGE_ROW_H - 2, band] == INK).sum()
    assert ink_large > ink_small


def test_inverted_list_is_white_on_black() -> None:
    """The list turns mostly ink; the hero keeps its pixels."""
    rows = (row(),) * 5
    plain = draw_stop_view(view=replace(BASE, rows=rows))
    turned = draw_stop_view(view=replace(BASE, rows=rows, inverted=True))
    area = turned[HEADER + 30:, SPLIT + 3:]
    assert (area == INK).mean() > 0.8
    assert (turned[HEADER:, :SPLIT] == plain[HEADER:, :SPLIT]).all()
    assert (turned[:HEADER] == plain[:HEADER]).all()


def test_inverted_board_whole_width() -> None:
    """With no destination the whole area under the header turns."""
    view = replace(
        BASE, destination=None, hero=None, rows=(row(), row(then=None)))
    plain = draw_stop_view(view=view)
    turned = draw_stop_view(view=replace(view, inverted=True))
    assert (turned[HEADER:] == INK).mean() > 0.8
    assert (turned[:HEADER] == plain[:HEADER]).all()


def test_inverted_empty_message_is_fully_inverted() -> None:
    """With a destination but no hero, the whole area turns, left too."""
    view = replace(
        BASE, hero=None, rows=(), empty='No direct services to X')
    plain = draw_stop_view(view=view)
    turned = draw_stop_view(view=replace(view, inverted=True))
    assert (turned[HEADER:] == 255 - plain[HEADER:]).all()
    assert (turned[:HEADER] == plain[:HEADER]).all()


def test_inverted_without_a_hero_starts_at_the_left_edge() -> None:
    """Rows with a destination but no hero invert from x=0."""
    view = replace(BASE, hero=None, rows=(row(),) * 3)
    plain = draw_stop_view(view=view)
    turned = draw_stop_view(view=replace(view, inverted=True))
    assert (turned[HEADER:, :SPLIT] == 255 - plain[HEADER:, :SPLIT]).all()


def test_large_and_inverted_together() -> None:
    """Both flags apply: taller rows, and the list area inverted."""
    rows = (row(),) * 5
    large = draw_stop_view(view=replace(BASE, rows=rows, large=True))
    both = draw_stop_view(
        view=replace(BASE, rows=rows, large=True, inverted=True))
    area = (slice(HEADER, None), slice(SPLIT + 3, None))
    assert (both[area] == 255 - large[area]).all()
    assert (both[HEADER:, :SPLIT] == large[HEADER:, :SPLIT]).all()


def test_inverted_box_sits_below_the_banner() -> None:
    """A banner keeps its pixels; the list under it turns."""
    view = replace(BASE, banner='No live data since 7:56', rows=(row(),) * 3)
    plain = draw_stop_view(view=view)
    turned = draw_stop_view(view=replace(view, inverted=True))
    top = HEADER + BANNER
    assert (turned[:top] == plain[:top]).all()
    assert (turned[top:, SPLIT + 3:] == 255 - plain[top:, SPLIT + 3:]).all()


def later_band(*, top: int) -> tuple[slice, slice]:
    """
    Find where the LATER head goes.

    Parameters
    ----------
    top : int
        Top of the content below the header and any banner.

    Returns
    -------
    tuple[slice, slice]
        Rows and columns of the head.
    """
    return slice(top + 12, top + 24), slice(LIST_LEFT, LIST_LEFT + 40)


def test_hero_list_is_headed_later() -> None:
    """With a hero, the list is headed LATER."""
    assert BASE.hero is not None
    frame = draw_stop_view(view=BASE)
    assert frame[later_band(top=HEADER)].min() == INK


def test_board_has_no_later_head() -> None:
    """The board has no hero and no LATER head."""
    view = replace(BASE, destination=None, hero=None, rows=())
    frame = draw_stop_view(view=view)
    assert frame[later_band(top=HEADER)].min() == PAPER


def test_later_head_moves_under_a_banner() -> None:
    """Under a banner, the head's ink starts below the banner."""
    view = replace(BASE, banner='No live data since 7:56')
    frame = draw_stop_view(view=view)
    top = HEADER + BANNER
    assert frame[later_band(top=top)].min() == INK
    assert frame[top + 1 : top + 10, LIST_LEFT : LIST_LEFT + 40].min() == (
        PAPER
    )
