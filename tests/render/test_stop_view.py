"""Facts about the drawn Stop view (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np
import pytest

from paperroute_device.core.text import DOT
from paperroute_device.render.canvas import (
    ELLIPSIS,
    INK,
    PAPER,
    Canvas,
    Face,
    text_width,
)
from paperroute_device.render.frame import HEADER, finish
from paperroute_testkit.render.factories import STOP_VIEW as BASE
from paperroute_testkit.render.factories import stop_row as row
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.stop_under import NextCall, UnderNumber
from paperroute_transit.core.stop_view import (
    HORIZON_TEXT,
    UNCERTAIN,
    Estimate,
    Pill,
    Row,
    StopView,
)
from paperroute_transit.render.chrome import (
    BANNER,
    NAME_GAP,
    NOTE_GAP,
    badge,
    badge_width,
    note_room,
    route_pair,
)
from paperroute_transit.render.late_marks import mark_room
from paperroute_transit.render.names import Naming
from paperroute_transit.render.stop_view import (
    FASTER_LEFT,
    LARGE_ROW_H,
    LIST_LEAVE_RIGHT,
    LIST_LEFT,
    LIST_PAIR_ROOM,
    LIST_TOP,
    LIST_WIDTH,
    MARK_PX,
    NOTE_PX,
    PILL_LEFT,
    ROW_H,
    SPLIT,
    TAGGED_WIDTH,
    WIDEST_COUNT,
    Scale,
    draw_stop_view,
    headline_width,
    headsign_arrival,
    hero_headline,
    hero_under,
    list_row,
    open_width,
    row_note,
    small_parts,
    small_text,
)
from tests.render.test_chrome import Recording


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
    ferry = replace(
        BASE, hero=replace(row(), vehicle='ferry', bay='wharf'),
    )
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


def test_widest_count_clears_list_pairs() -> None:
    """'100 min' clears pairs, badges and the arrivals."""
    wide = text_width(text=WIDEST_COUNT, face=Face(weight=800, px=28))
    large = text_width(text=WIDEST_COUNT, face=Face(weight=800, px=35))
    arrive = text_width(text='88:88', face=Face(weight=800, px=25))
    route = badge_width(px=14)
    assert LIST_LEFT + LIST_PAIR_ROOM + 8 <= LIST_LEAVE_RIGHT - wide
    assert LIST_LEFT + LIST_PAIR_ROOM + 8 <= LIST_LEAVE_RIGHT - large
    assert LIST_LEAVE_RIGHT + 16 <= min(FASTER_LEFT, 786 - arrive)
    assert LIST_LEFT + route + 8 < LIST_LEAVE_RIGHT - wide


@pytest.mark.parametrize(
    'pair', [('BMT', 'CCN'), ('SH100', '283S2')], ids=['BMT', 'SH100'],
)
@pytest.mark.parametrize('large', [False, True], ids=['normal', 'large'])
def test_list_widest_count_clears_a_long_pair(
    large: bool, pair: tuple[str, str],
) -> None:
    """A '100 min' count starts clear of a long pair, in real ink."""
    canvas = Canvas()
    y = 100
    route, onto = pair
    long = replace(
        row(pill=Pill.FASTER, onto=onto),
        route=route,
        countdown='100',
        arrives_at='23:58',
    )
    list_row(
        canvas, row=long, y=y, large=large, naming=PLAIN,
    )
    frame = finish(canvas)
    rows = slice(y, y + Scale(large=large).y(31))
    ink = np.flatnonzero((frame[rows] == INK).any(axis=0))
    room = LIST_LEFT + LIST_PAIR_ROOM
    pair_right = ink[(ink >= LIST_LEFT) & (ink <= room)].max()
    count = ink[(ink > room) & (ink < FASTER_LEFT)]
    assert count.min() - pair_right >= 8
    assert count.max() <= FASTER_LEFT - 16


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
    open_frame = draw_stop_view(
        view=replace(BASE, destination=None, hero=None, rows=(long,)),
    )
    top = HEADER + LIST_TOP
    band = (slice(top + 6, top + 9), slice(440, 786))
    ink = np.flatnonzero((open_frame[band] == INK).any(axis=0))
    assert ink[-1] + 440 < 786 - count


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


LATE: Final[Estimate] = Estimate(due='8:05', late=4, uncertain=False)
ON_TIME: Final[Estimate] = Estimate(due='8:05', late=1, uncertain=False)
UNSURE: Final[Estimate] = Estimate(due='8:05', late=1, uncertain=True)


def changed(*, before: StopView, after: StopView) -> np.ndarray:
    """The rows and columns where two Stop views' frames differ."""
    return np.argwhere(
        draw_stop_view(view=before) != draw_stop_view(view=after),
    )


def test_estimated_hero_draws_its_due_line_under_the_ride() -> None:
    """The due line sits under the ride line, left of the split."""
    found = changed(
        before=BASE, after=replace(BASE, hero=replace(row(), estimate=LATE)),
    )
    assert found.size
    assert found[:, 0].min() >= 40 + 248 and found[:, 0].max() <= 40 + 270
    assert found[:, 1].min() >= 18 and found[:, 1].max() < SPLIT


def test_uncertain_later_row_says_so_under_its_time() -> None:
    """'uncertain' sits under the leave time, right-aligned to it."""
    found = changed(
        before=replace(BASE, rows=(row(),)),
        after=replace(BASE, rows=(replace(row(), estimate=UNSURE),)),
    )
    assert found.size
    assert found[:, 0].min() >= 70 + 33 and found[:, 0].max() <= 70 + 47
    assert found[:, 1].min() >= LIST_LEAVE_RIGHT - 60
    assert found[:, 1].max() <= LIST_LEAVE_RIGHT


def test_certain_later_row_has_no_note() -> None:
    """A certain estimate draws just its count."""
    before = replace(BASE, rows=(row(),))
    after = replace(BASE, rows=(replace(row(), estimate=ON_TIME),))
    assert not changed(before=before, after=after).size


def test_an_uncertain_open_row_says_so_under_its_time() -> None:
    """With no destination, 'uncertain' sits under the leave time."""
    board = replace(BASE, destination=None, hero=None, rows=(row(),))
    found = changed(
        before=board,
        after=replace(board, rows=(replace(row(), estimate=UNSURE),)),
    )
    assert found.size
    assert found[:, 0].min() >= HEADER + LIST_TOP + 33
    assert found[:, 0].max() <= HEADER + LIST_TOP + 47
    assert found[:, 1].min() >= 786 - 60


def test_tagged_uncertain_headline_stops_short_of_the_note() -> None:
    """A run-on tag gives way to the note; untagged rows keep their width."""
    k = Scale(large=False)
    tagged = replace(
        row(then=CarryOn(route='T2', headsign='Leppington')),
        estimate=UNSURE,
    )
    room = note_room(face=k.face(weight=700, px=NOTE_PX))
    assert headline_width(row=tagged, k=k) == (
        LIST_LEAVE_RIGHT - LIST_LEFT - room
    )
    assert headline_width(
        row=replace(tagged, estimate=LATE), k=k,
    ) == TAGGED_WIDTH
    assert headline_width(
        row=replace(row(), estimate=UNSURE), k=k,
    ) == LIST_WIDTH


def test_a_later_row_notes_its_platform_under_its_time() -> None:
    """A row's note sits under the leave time, right-aligned to it."""
    found = changed(
        before=replace(BASE, rows=(row(),)),
        after=replace(BASE, rows=(replace(row(), note='platform 2B'),)),
    )
    assert found.size
    assert found[:, 0].min() >= 70 + 33 and found[:, 0].max() <= 70 + 47
    assert found[:, 1].min() >= LIST_LEAVE_RIGHT - 62
    assert found[:, 1].max() <= LIST_LEAVE_RIGHT


def test_a_later_rows_note_reads_as_the_note_drawn_alone() -> None:
    """The note's pixels match the same text drawn on a blank canvas."""
    noted = replace(row(), note='platform 2B')
    frame = draw_stop_view(view=replace(BASE, hero=None, rows=(noted,)))
    alone = Canvas()
    k = Scale(large=False)
    y = HEADER + 30
    row_note(alone, row=noted, right=LIST_LEAVE_RIGHT, y=y, k=k)
    box = (slice(y + 33, y + 47), slice(LIST_LEAVE_RIGHT - 62, 786))
    assert (finish(alone)[box] == INK).any()
    assert (frame[box] == finish(alone)[box]).all()


def test_a_long_bay_word_keeps_clear_of_the_headline() -> None:
    """A long platform word makes the name end a gap short of it."""
    text = 'platform Concourse 12'
    long = replace(row(destination='Somewhere ' * 9), note=text)
    room = note_room(face=Face(weight=700, px=NOTE_PX), text=text)
    frame = draw_stop_view(view=replace(BASE, hero=None, rows=(long,)))
    y = HEADER + 30
    left = LIST_LEAVE_RIGHT - int(room)
    assert (
        frame[y + 33:y + 47, left + 1:left + NOTE_GAP - 1] == PAPER
    ).all()


def test_uncertain_wins_over_the_platform_note() -> None:
    """An uncertain row draws 'uncertain', whatever its note."""
    unsure = replace(row(), estimate=UNSURE)
    assert not changed(
        before=replace(BASE, rows=(unsure,)),
        after=replace(BASE, rows=(replace(unsure, note='platform 2B'),)),
    ).size


def test_an_open_row_notes_its_platform_under_its_time() -> None:
    """With no destination, the note sits under the leave time."""
    board = replace(BASE, destination=None, hero=None, rows=(row(),))
    found = changed(
        before=board,
        after=replace(board, rows=(replace(row(), note='platform 2B'),)),
    )
    assert found.size
    assert found[:, 0].min() >= HEADER + LIST_TOP + 33
    assert found[:, 0].max() <= HEADER + LIST_TOP + 47
    assert found[:, 1].min() >= 786 - 62


def test_a_tagged_noted_headline_stops_short_of_the_note() -> None:
    """A run-on tag gives way to a platform note too."""
    k = Scale(large=False)
    tagged = replace(
        row(then=CarryOn(route='T2', headsign='Leppington')),
        note='platform 2B',
    )
    room = note_room(face=k.face(weight=700, px=NOTE_PX), text='platform 2B')
    assert headline_width(row=tagged, k=k) == (
        LIST_LEAVE_RIGHT - LIST_LEFT - room
    )
    assert headline_width(row=replace(tagged, note=''), k=k) == TAGGED_WIDTH


UNDER: Final[UnderNumber] = UnderNumber(
    status='2 min late',
    platform='stand B',
    then=('8:06', '8:09'),
    delay=120,
    place='1 stop away, last seen at Mays Hill',
    calls=(
        NextCall(at='8:08', name='Harris Park'),
        NextCall(at='8:11', name='Rosehill'),
        NextCall(at='8:14', name='Parramatta'),
    ),
)
OPEN: Final[StopView] = replace(BASE, destination=None, under=UNDER)
OPEN_TOP: Final[int] = HEADER + LIST_TOP


def open_rows(*, item: Row) -> StopView:
    """An open view whose list holds one row."""
    return replace(OPEN, rows=(item,))


def test_small_parts_list_what_the_hero_has() -> None:
    """Place, platform, then the next two; a fallback when none exist."""
    hero = row()
    assert small_parts(under=UNDER, row=hero) == (
        '1 stop away, last seen at Mays Hill', 'stand B', 'then 8:06, 8:09',
    )
    bare = replace(UNDER, place='', platform='', then=())
    assert small_parts(under=bare, row=hero) == (
        f'no other {hero.route} to {hero.destination} in {HORIZON_TEXT}',
    )


def test_small_text_keeps_whole_parts() -> None:
    """Only whole parts are joined, and the first always shows."""
    face = Face(weight=400, px=15)
    two = text_width(text=f'aaa{DOT}bbb', face=face)
    parts = ('aaa', 'bbb', 'ccc')
    assert small_text(parts=parts, face=face, width=two) == f'aaa{DOT}bbb'
    assert small_text(parts=parts, face=face, width=999.0) == (
        f'aaa{DOT}bbb{DOT}ccc'
    )
    assert small_text(parts=parts, face=face, width=1.0) == 'aaa'


def test_under_the_number_fills_its_lines() -> None:
    """Status, small line, heading and calls each hold ink."""
    frame = draw_stop_view(view=OPEN)
    for low, high in ((190, 222), (228, 244), (258, 272), (276, 342)):
        band = frame[HEADER + low:HEADER + high, 18:418]
        assert (band == INK).any(), (low, high)
    assert (frame[HEADER + 348:, 0:SPLIT] == PAPER).all()


def test_under_the_number_replaces_the_footer_and_due_line() -> None:
    """The footer and due line never draw when `under` is given."""
    assert OPEN.hero is not None
    other = replace(
        OPEN.hero, estimate=UNSURE, arrives_at='23:59', ride_minutes=99,
    )
    assert (
        draw_stop_view(view=OPEN)
        == draw_stop_view(view=replace(OPEN, hero=other))
    ).all()


def test_without_calls_there_is_no_calls_heading() -> None:
    """With no calls, nothing is drawn below the small line."""
    frame = draw_stop_view(view=replace(OPEN, under=replace(UNDER, calls=())))
    assert (frame[HEADER + 256:, 0:SPLIT] == PAPER).all()


def test_hero_under_draws_only_below_the_top() -> None:
    """Drawing the block touches nothing above the rule."""
    canvas = Canvas()
    hero_under(
        canvas, under=UNDER, row=row(), top=100, rules=NameRules(),
    )
    frame = finish(canvas)
    assert (frame[:100 + 183] == PAPER).all()
    assert (frame[100 + 184:100 + 186, 18:418] == INK).all()


def test_an_open_list_heads_leaves_at_the_right() -> None:
    """LEAVES heads the right edge, with no ARRIVES head beside it."""
    frame = draw_stop_view(view=OPEN)
    assert (frame[HEADER + 12:HEADER + 24, 740:786] == INK).any()
    far = slice(LIST_LEAVE_RIGHT - 40, LIST_LEAVE_RIGHT)
    assert (frame[HEADER + 12:HEADER + 24, far] == PAPER).all()


def test_an_open_row_has_no_arrival_column() -> None:
    """The arrival time is not drawn; the leave time is at the right."""
    first = draw_stop_view(view=open_rows(item=row()))
    other = draw_stop_view(
        view=open_rows(item=replace(row(), arrives_at='23:59')),
    )
    assert (first == other).all()
    y = OPEN_TOP
    assert (first[y + 5:y + 31, 740:786] == INK).any()


def test_an_open_row_has_no_run_on_tag() -> None:
    """A run-on tag is not drawn on an open row."""
    then = CarryOn(route='T9', headsign='Hornsby')
    plain = draw_stop_view(view=open_rows(item=row()))
    tagged = draw_stop_view(view=open_rows(item=row(then=then)))
    assert (plain == tagged).all()


def test_an_open_row_runs_its_name_to_the_edge() -> None:
    """A long name has ink past where a list row's name would stop."""
    long = row(destination='Drummer St Bus Station Bay 10')
    frame = draw_stop_view(view=open_rows(item=replace(long, live=False)))
    y = OPEN_TOP
    assert (frame[y + 33:y + 47, 600:786] == INK).any()


def test_an_open_row_name_stops_before_its_note() -> None:
    """A long name ends a gap short of the note under the time."""
    long = replace(
        row(destination='Somewhere ' * 9), note='platform 2B',
    )
    room = note_room(face=Face(weight=700, px=NOTE_PX), text='platform 2B')
    frame = draw_stop_view(view=open_rows(item=long))
    y = OPEN_TOP
    left = 786 - int(room)
    assert (frame[y + 33:y + 47, left + 1:left + NOTE_GAP - 1] == PAPER).all()


def test_an_open_cancelled_row_name_stops_before_the_block() -> None:
    """A long name ends a gap short of the CANCELLED pill."""
    long = row(destination='Somewhere ' * 9, pill=Pill.CANCELLED)
    frame = draw_stop_view(view=open_rows(item=long))
    y = OPEN_TOP
    ink = np.flatnonzero(
        (frame[y + 33:y + 47, LIST_LEFT:PILL_LEFT] == INK).any(axis=0),
    )
    assert ink.size
    assert ink[-1] + LIST_LEFT <= PILL_LEFT - NAME_GAP


def test_an_open_row_counts_down_past_100() -> None:
    """A live open row 130 minutes away still fills its time slot."""
    frame = draw_stop_view(
        view=open_rows(item=replace(row(), countdown='130')),
    )
    y = OPEN_TOP
    assert (frame[y + 5:y + 31, 680:786] == INK).any()


def test_open_width_has_three_edges() -> None:
    """A row's room ends at the pill, the note, or the right edge."""
    k = Scale()
    plain = row()
    noted = replace(plain, note='platform 2B')
    room = note_room(face=k.face(weight=700, px=NOTE_PX), text='platform 2B')
    assert open_width(row=plain, k=k) == 786 - LIST_LEFT
    assert open_width(row=noted, k=k) == 786 - room - LIST_LEFT
    cancelled = row(pill=Pill.CANCELLED)
    assert open_width(row=cancelled, k=k) == (
        PILL_LEFT - NAME_GAP - LIST_LEFT
    )


PLAIN: Final[Naming] = Naming(rules=NameRules())


def test_hero_headline_wraps_a_long_headsign_whole() -> None:
    """A headsign too long for one line wraps onto two, losing nothing."""
    long = 'Cottenham Rampton Road via Histon Impington Village College'
    canvas = Recording()
    hero_headline(canvas, row=row(destination=long), top=100, naming=PLAIN)
    first, second = canvas.drawn[-2:]
    assert f'{first} {second}' == long


def test_hero_headline_keeps_one_line_when_it_fits() -> None:
    """A short headsign is drawn on one line."""
    canvas = Recording()
    hero_headline(canvas, row=row(), top=100, naming=PLAIN)
    assert canvas.drawn[-1] == 'Parramatta'


def test_list_row_cuts_via_when_the_headsign_does_not_fit() -> None:
    """A headsign that overflows loses its via part."""
    canvas = Recording()
    list_row(
        canvas, row=row(destination='Mount Druitt via Rooty Hill'), y=100,
        naming=PLAIN,
    )
    assert 'Mount Druitt' in canvas.drawn


def test_list_row_keeps_via_when_another_row_would_read_alike() -> None:
    """A sibling with the same start keeps the via part, cut by ellipsis."""
    canvas = Recording()
    list_row(
        canvas, row=row(destination='Mount Druitt via Rooty Hill'), y=100,
        naming=Naming(
            rules=NameRules(), siblings=('Mount Druitt via Plumpton',),
        ),
    )
    assert any(
        text.startswith('Mount Druitt via') and text.endswith(ELLIPSIS)
        for text in canvas.drawn
    )


def test_hero_under_shortens_call_names_by_the_rules() -> None:
    """Call names are shortened by the joining-word rules."""
    name = 'Great Western Highway Opp Rooty Hill Road North'
    under = replace(UNDER, calls=(NextCall(at='8:12', name=name),))
    canvas = Recording()
    hero_under(
        canvas, under=under, row=row(), top=100,
        rules=NameRules(joining=JOINING_WORDS),
    )
    assert 'Great Western Hwy Opp Rooty Hill Road North' in canvas.drawn


LATE_UNSURE: Final[Estimate] = Estimate(due='7:58', late=12, uncertain=True)


def test_list_row_marks_its_lateness_after_the_countdown() -> None:
    """A row four minutes late reads +4."""
    canvas = Recording()
    list_row(canvas, row=replace(row(), estimate=LATE), y=100, naming=PLAIN)
    assert '+4' in canvas.drawn


def test_list_row_open_layout_marks_an_early_departure() -> None:
    """An open row two minutes early reads −2."""
    early = Estimate(due='8:11', late=-2, uncertain=False)
    canvas = Recording()
    list_row(
        canvas, row=replace(row(), estimate=early), y=100, naming=PLAIN,
        arrives=False,
    )
    assert '−2' in canvas.drawn


def test_list_row_folds_uncertainty_into_its_mark() -> None:
    """+12? replaces the word, and the platform keeps its slot."""
    noted = replace(row(), estimate=LATE_UNSURE, note='platform 2B')
    canvas = Recording()
    list_row(canvas, row=noted, y=100, naming=PLAIN)
    assert '+12?' in canvas.drawn
    assert 'platform 2B' in canvas.drawn
    assert UNCERTAIN not in canvas.drawn


def test_list_row_under_the_floor_keeps_the_word() -> None:
    """A row a minute off has no mark and still says uncertain."""
    canvas = Recording()
    list_row(canvas, row=replace(row(), estimate=UNSURE), y=100, naming=PLAIN)
    assert UNCERTAIN in canvas.drawn
    assert not any(text.startswith(('+', '−')) for text in canvas.drawn)


def test_list_row_pair_yields_to_a_marked_countdown() -> None:
    """A long pair ends clear of '100 min +12?'."""
    marked = replace(
        row(onto='CCN'), route='BMT', countdown='100', estimate=LATE_UNSURE,
    )
    canvas = Canvas()
    list_row(canvas, row=marked, y=100, naming=PLAIN)
    group = text_width(
        text=WIDEST_COUNT, face=Face(weight=800, px=28),
    ) + mark_room(row=marked, face=Face(weight=700, px=MARK_PX))
    start = int(LIST_LEAVE_RIGHT - group)
    band = canvas.grey()[109:113, LIST_LEFT:start - 2] < 128
    assert LIST_LEFT + np.flatnonzero(band.any(axis=0)).max() < start - 4


def test_headsign_arrival_joins_the_headsign_and_the_arrival() -> None:
    """The headsign, a dot and the arrival; the arrival alone without one."""
    assert headsign_arrival(row=row()) == f'Parramatta{DOT}arr 8:45'
    assert headsign_arrival(row=row(destination='')) == 'arr 8:45'
