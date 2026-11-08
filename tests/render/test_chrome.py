"""Tests for the shared header, banner and badge."""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

from paperroute.render.chrome import (
    BANNER,
    FROM_FLOOR,
    LINK_ARROW,
    LINK_WIDTH,
    MIN_SQUEEZE,
    TABS,
    badge,
    badge_face,
    badge_width,
    banner,
    countdown_slot,
    header,
    header_tabs,
    leaves,
    link_arrow,
    pin_tag,
    route_pair,
    row_px,
    shared_width,
    two_titles,
    view_header,
)
from paperroute.render.pair_list import CHIP_PX
from paperroute.render.stop_view import LIST_PAIR_ROOM
from paperroute_device.render.canvas import (
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
    Point,
    text_width,
)
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.stop_view import NOT_PINNED, PinTag, Row
from paperroute_transit.core.stop_view import pin_tag as tag_of

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'
LONG_LABELS: Final[tuple[str, ...]] = ('SH100', '283S2', '772V8')
PINNED = PinTag(text='pin 2/3', pinned=True)


class Recording(Canvas):
    """A canvas that keeps the texts and polygons it is asked to draw."""

    def __init__(self) -> None:
        super().__init__()
        self.fitted: list[str] = []
        self.drawn: list[str] = []
        self.shapes: list[Sequence[Point]] = []

    def fit(self, *, text: str, face: Face, width: float) -> str:
        """Record the text and fit it as usual."""
        self.fitted.append(text)
        return super().fit(text=text, face=face, width=width)

    def text(self, **kwargs: Any) -> float:
        """Record the text and draw it as usual."""
        self.drawn.append(kwargs['text'])
        return super().text(**kwargs)

    def polygon(self, *, points: Sequence[Point], grey: int) -> None:
        """Record the polygon and draw it as usual."""
        self.shapes.append(points)
        super().polygon(points=points, grey=grey)


def _tab_x(*, tab: str) -> int:
    """Return an x inside the box of a tab in the header."""
    right = WIDTH - 12 - text_width(
        text='8:41', face=Face(weight=800, px=22))
    for name in reversed(TABS):
        width = text_width(text=name, face=Face(weight=700, px=12)) + 10
        right -= width + 4
        if name == tab:
            return int(right + 2)
    raise ValueError(tab)


def test_header_marks_active_tab() -> None:
    """The active tab is a paper box; the others stay ink."""
    canvas = Canvas()
    header(
        canvas,
        origin='A',
        destination='B',
        clock='8:41',
        active='Map',
        tag=NOT_PINNED,
    )
    grey = canvas.grey()
    assert int(grey[13, _tab_x(tab='Map')]) == PAPER
    assert int(grey[13, _tab_x(tab='Stop')]) == INK
    x = _tab_x(tab='Map')
    assert int(grey[12:30, x:x + 30].min()) == INK


def _tag_box(*, tag: PinTag) -> tuple[Canvas, float]:
    """Draw a tag to the left of the tabs on an ink header."""
    canvas = Canvas()
    canvas.rect(box=(0, 0, WIDTH, 40), grey=INK)
    right = header_tabs(canvas, clock='8:41', active='Map')
    return canvas, pin_tag(canvas, right=right, tag=tag)


def test_pin_tag_text() -> None:
    """A pinned place reads pin n/total; an unpinned one not pinned."""
    assert tag_of(index=1, total=3).text == 'pin 2/3'
    assert tag_of(index=1, total=3).pinned
    assert tag_of(index=None, total=3) == NOT_PINNED
    assert NOT_PINNED.text == 'not pinned'


def test_pinned_tag_is_paper_box() -> None:
    """A pinned tag is a paper box 19 px tall with ink text."""
    canvas, left = _tag_box(tag=PINNED)
    grey = canvas.grey()
    x = int(left) + 1
    assert int(grey[11, x]) == PAPER
    assert int(grey[29, x]) == PAPER
    assert int(grey[10, x]) == INK
    assert int(grey[30, x]) == INK
    assert int(grey[11:30, x:x + 60].min()) == INK
    assert int(grey[12, x]) == PAPER


def test_pin_tag_sits_8px_left_of_the_tabs() -> None:
    """The tag's right edge is 8 px left of the edge given."""
    canvas = Canvas()
    width = text_width(text='pin 2/3', face=Face(weight=700, px=12)) + 10
    left = pin_tag(canvas, right=300, tag=PINNED)
    assert left == 300 - 8 - width


def test_unpinned_tag_is_dashed() -> None:
    """An unpinned tag's outline row alternates paper and ink."""
    canvas, left = _tag_box(tag=NOT_PINNED)
    row = canvas.grey()[11, int(left):int(left) + 40]
    assert PAPER in row
    assert INK in row
    inside = canvas.grey()[20, int(left) + 1]
    assert int(inside) == INK


def test_auto_tag_is_inverted() -> None:
    """An auto tag is an ink box with a paper edge and paper text."""
    auto = replace(PINNED, auto=True)
    canvas, left = _tag_box(tag=auto)
    plain_canvas, plain_left = _tag_box(tag=PINNED)
    assert left == plain_left
    x = int(left)
    grey = canvas.grey()
    inside = grey[13:28, x + 2:x + 40]
    assert float((inside == INK).mean()) > 0.5
    assert int(inside.max()) == PAPER
    assert int(grey[20, x]) == PAPER
    assert int(grey[11, x + 10]) == PAPER
    plain = plain_canvas.grey()[13:28, x + 2:x + 40]
    assert float((plain == PAPER).mean()) > 0.5


def test_unpinned_tag_text_is_paper() -> None:
    """An unpinned tag's text is paper on the ink header."""
    canvas, left = _tag_box(tag=NOT_PINNED)
    text = canvas.grey()[14:27, int(left) + 3:int(left) + 50]
    assert int(text.max()) == PAPER


def test_no_destination_title() -> None:
    """With no destination the header draws no arrow and one name."""
    canvas = Recording()
    header(
        canvas,
        origin='Town Hall',
        destination=None,
        clock='8:41',
        active='Stop',
        tag=NOT_PINNED,
    )
    assert canvas.fitted == ['Town Hall']
    assert not any(len(points) == 3 for points in canvas.shapes)
    assert 'None' not in canvas.drawn


def test_picking_title() -> None:
    """Picking shows the origin, an arrow and the pick target."""
    canvas = Recording()
    header(
        canvas,
        origin='Town Hall',
        destination=None,
        clock='8:41',
        active='Stop',
        tag=NOT_PINNED,
        picking='To',
    )
    assert canvas.fitted == ['Town Hall', 'pick To']
    assert 'pick To' in canvas.drawn
    assert len(canvas.shapes) == 1


def test_picking_from_title() -> None:
    """Picking a From reads as one title, the route then 'pick FROM'."""
    canvas = Recording()
    header(
        canvas,
        origin='606 to Parramatta',
        destination=None,
        clock='8:41',
        active='Strip',
        tag=NOT_PINNED,
        picking='FROM',
    )
    assert canvas.fitted == ['606 to Parramatta · pick FROM']
    assert not canvas.shapes


def test_titles_stop_before_the_tag() -> None:
    """A long origin and destination leave the tag's box untouched."""
    canvas = Canvas()
    header(
        canvas,
        origin='A very long origin stop name indeed' * 3,
        destination='An equally long destination stop name' * 3,
        clock='8:41',
        active='Map',
        tag=PINNED,
    )
    left = (
        WIDTH - 12
        - text_width(text='8:41', face=Face(weight=800, px=22))
    )
    for tab in TABS:
        left -= text_width(text=tab, face=Face(weight=700, px=12)) + 14
    box_left = left - 8 - (
        text_width(text='pin 2/3', face=Face(weight=700, px=12)) + 10
    )
    grey = canvas.grey()
    assert int(grey[:40, int(box_left) - 8:int(box_left)].max()) == INK
    ink_columns = np.flatnonzero(grey[:40, :int(box_left)].max(axis=0))
    limit = box_left - 8
    slack = text_width(text='W…', face=Face(weight=700, px=18))
    assert limit - 18 - slack <= ink_columns.max() <= limit - 18


LONG_FROM: str = 'Bogalara Rd Opp Tabooba St'
TITLE_FACE: Face = Face(weight=700, px=18)


def test_two_titles_keep_the_to_whole() -> None:
    """A From too long for its room is cut with '…'; the To stays whole."""
    canvas = Recording()
    two_titles(
        canvas, first=LONG_FROM, second='Parramatta Station', limit=400,
    )
    first, second = canvas.drawn
    assert first.endswith('…')
    assert first.startswith('Bogalara Rd Opp')
    assert LONG_FROM.startswith(first.removesuffix('…'))
    assert second == 'Parramatta Station'


@pytest.mark.parametrize('tag', ['pin 3/3', 'Home 3/12'])
@pytest.mark.parametrize(
    ('origin', 'destination'),
    [
        (LONG_FROM, 'Parramatta Station'),
        ('Constitution Hill Library, Hollis St', 'Wentworthville Station'),
    ],
)
def test_header_cuts_the_from_without_dropping_words(
    origin: str, destination: str, tag: str,
) -> None:
    """A real pin's From is whole or cut with '…'; its To stays whole."""
    canvas = Recording()
    header(
        canvas,
        origin=origin,
        destination=destination,
        clock='9:20',
        active='Stop',
        tag=PinTag(text=tag, pinned=True),
    )
    first, second = canvas.drawn[-2:]
    assert first == origin or (
        first.endswith('…') and origin.startswith(first.removesuffix('…'))
    )
    assert len(first) > len(origin.split(maxsplit=2)[0])
    assert second == destination


def test_two_titles_leave_a_fitting_from_alone() -> None:
    """A From that fits its room keeps its full name."""
    canvas = Recording()
    two_titles(
        canvas, first=LONG_FROM, second='Parramatta Station', limit=500,
    )
    assert canvas.drawn == [LONG_FROM, 'Parramatta Station']


def test_long_to_leaves_the_from_its_floor() -> None:
    """A To wider than the room still leaves the From its floor."""
    canvas = Recording()
    two_titles(
        canvas,
        first=LONG_FROM,
        second="Westmead Children's Hospital, Hawkesbury Rd",
        limit=400,
    )
    first, second = canvas.drawn
    assert first.startswith('Boga')
    assert first.endswith('\u2026')
    assert text_width(text=first, face=TITLE_FACE) <= FROM_FLOOR
    assert second.startswith('Westmead')
    assert second.endswith('\u2026')
    assert text_width(text=second, face=TITLE_FACE) > 240


@dataclass(frozen=True)
class Titles:
    """The texts a header shows."""

    origin: str
    destination: str | None
    tag: PinTag
    clock: str


def test_view_header_passes_picking() -> None:
    """A view's header shows the pick target while picking."""
    view = Titles(
        origin='Town Hall', destination='Parramatta', tag=NOT_PINNED,
        clock='8:41',
    )
    picking = Recording()
    view_header(picking, view=view, active='Strip', picking='TO')
    plain = Recording()
    view_header(plain, view=view, active='Strip')
    assert picking.fitted == ['Town Hall', 'pick TO']
    assert plain.fitted == ['Town Hall', 'Parramatta']


def test_countdown_slot_shows_only_live_counts() -> None:
    """A live row fills the slot at any distance; a timetable row doesn't."""
    near = Row(
        route='T1', vehicle='train', bay='platform', platform='3',
        destination='Emu Plains',
        countdown='9', leaves_at='8:09', arrives_at='8:45', ride_minutes=36,
        live=True, pill=None,
    )
    assert countdown_slot(row=near) == '9 min'
    assert countdown_slot(row=replace(near, countdown='now')) == 'now'
    assert countdown_slot(row=replace(near, countdown='130')) == '130 min'
    assert countdown_slot(row=replace(near, live=False)) == ''


def test_leaves_names_its_minutes() -> None:
    """A live count reads '<n> min'; 'now' and clock times stay as they are."""
    near = Row(
        route='T1', vehicle='train', bay='platform', platform='3',
        destination='Emu Plains',
        countdown='12', leaves_at='8:17', arrives_at='8:45', ride_minutes=28,
        live=True, pill=None,
    )
    assert leaves(row=near) == '12 min'
    assert leaves(row=replace(near, countdown='now')) == 'now'
    assert leaves(row=replace(near, live=False)) == '8:17'


def test_route_pair_wider_with_onto() -> None:
    """A carry-on pair is over twice a lone badge; with None it is one."""
    canvas = Canvas()
    alone = badge(canvas, x=10, y=20, route='T8', px=14)
    plain = route_pair(canvas, x=10, y=20, route='T8', onto=None, px=14)
    pair = route_pair(canvas, x=10, y=20, route='T8', onto='T2', px=14)
    assert plain == alone
    assert pair > 2 * alone


def test_route_pair_second_badge_starts_after_the_arrow() -> None:
    """The onto badge's block of ink begins past the arrow's gap."""
    canvas = Canvas()
    first = badge(Canvas(), x=10, y=20, route='T8', px=14)
    pair = route_pair(canvas, x=10, y=20, route='T8', onto='T2', px=14)
    full = (canvas.grey()[:, :] == INK).sum(axis=0) >= 15
    blocks = np.flatnonzero(full)
    second = blocks[blocks > 10 + first + 4][0]
    assert second >= 10 + first + LINK_WIDTH
    assert blocks[-1] < 10 + pair


def test_route_pair_shrinks_to_its_room() -> None:
    """A pair given less room than it needs shrinks to fit it."""
    canvas = Canvas()
    natural = route_pair(canvas, x=0, y=20, route='BMT', onto='CCN', px=14)
    fitted = route_pair(
        Canvas(), x=0, y=20, route='BMT', onto='CCN', px=14, room=92,
    )
    assert natural > 92
    assert fitted <= 92


def test_warning_banner_is_inverted() -> None:
    """A warning band is mostly ink; the plain band stays mostly paper."""
    warned = Canvas()
    banner(warned, text='No API key', warning=True)
    plain = Canvas()
    banner(plain, text='No API key')
    band = (slice(HEADER, HEADER + BANNER), slice(0, WIDTH))
    assert (warned.grey()[band] == INK).mean() > 0.8
    assert (plain.grey()[band] == PAPER).mean() > 0.8


def banner_grey(**kwargs: Any) -> Any:
    """
    Draw a banner on a fresh canvas.

    Parameters
    ----------
    **kwargs : Any
        Arguments for `banner`.

    Returns
    -------
    Any
        The canvas's greys.
    """
    canvas = Canvas()
    banner(canvas, **kwargs)
    return canvas.grey()


def test_planned_banner_has_outlined_mark() -> None:
    """A coming alert is a paper band with an outlined triangle."""
    middle = slice(HEADER + 4, HEADER + BANNER - 4)
    mark = (middle, slice(16, 34))
    planned = banner_grey(text=' ', planned=True)
    band = (slice(HEADER + 4, HEADER + BANNER - 4), slice(4, WIDTH - 4))
    assert (planned[band] == PAPER).mean() > 0.8
    assert (planned[mark] == INK).any()
    plain = banner_grey(text=' ')
    assert (plain[mark] == PAPER).all()


def test_more_pill_drawn() -> None:
    """The pill shows at the band's right end in the opposite grey."""
    rows = slice(HEADER + 5, HEADER + BANNER - 5)
    end = (rows, slice(WIDTH - 90, WIDTH - 5))
    inverted = banner_grey(text='Alert', warning=True, more=2)
    assert (inverted[end] == PAPER).any()
    bare = banner_grey(text='Alert', warning=True)
    assert not (bare[end] == PAPER).any()
    outlined = banner_grey(text='Alert', planned=True, more=2)
    assert (outlined[end] == INK).any()
    assert not (banner_grey(text='Alert', planned=True)[end] == INK).any()


def test_more_pill_leaves_room_for_text() -> None:
    """Long text is fitted left of the pill."""
    canvas = Recording()
    banner(canvas, text='Trains run to a changed timetable ' * 4, more=12)
    face = Face(weight=700, px=15)
    shown = max(canvas.drawn, key=len)
    assert text_width(text=shown, face=face) < WIDTH - 16 - 60 - 10


def test_long_nickname_tag_keeps_titles() -> None:
    """A 24-character tag still leaves the origin title drawn."""
    canvas = Canvas()
    header(
        canvas,
        origin='Hollis St',
        destination=None,
        clock='8:41',
        active='Map',
        tag=PinTag(text=f'{"W" * 20} 2/3', pinned=True),
    )
    grey = canvas.grey()
    left = int(np.flatnonzero(grey[11] == PAPER).min())
    assert int(grey[12:30, 12:left].max()) == PAPER
    assert int(grey[12:30, left - 8:left].max()) == INK


def real_labels() -> list[str]:
    """
    List every route label in the real timetable, if there is one.

    Returns
    -------
    list[str]
        Route labels; none without data/timetable.db.
    """
    if not REAL_DB.exists():
        return []
    with closing(sqlite3.connect(f'file:{REAL_DB}?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT DISTINCT short_name FROM routes').fetchall()
    return [row[0] for row in rows]


def test_badges_share_one_width() -> None:
    """Every route's badge is as wide as four eights."""
    width = badge_width(px=14)
    for route in ('T1', '711', 'BMT', 'SH100'):
        assert badge(Canvas(), x=10, y=20, route=route, px=14) == width
    assert width == pytest.approx(
        text_width(text='8888', face=Face(weight=800, px=14)) + 12,
    )


def test_short_labels_keep_their_shape() -> None:
    """A label no wider than four eights is not condensed."""
    for route in ('T1', '711', '894', 'BMT', '894X', '8888'):
        assert badge_face(route=route, px=14).squeeze == 1.0


@pytest.mark.parametrize('px', [14, 20])
@pytest.mark.parametrize('route', LONG_LABELS)
def test_long_labels_squeeze_just_enough(route: str, px: int) -> None:
    """A long label condenses to fit its badge, and no further."""
    face = badge_face(route=route, px=px)
    room = badge_width(px=px) - 8
    looser = replace(face, squeeze=face.squeeze + 0.02)
    assert MIN_SQUEEZE <= face.squeeze < 1
    assert text_width(text=route, face=face) <= room
    assert text_width(text=route, face=looser) > room


def test_label_past_the_floor_is_cut() -> None:
    """A label too long even at the floor is cut with one mark."""
    canvas = Recording()
    badge(canvas, x=10, y=20, route='WWWWWW', px=14)
    assert canvas.drawn == ['WWW\u2026']
    assert badge_face(route='WWWWWW', px=14).squeeze == MIN_SQUEEZE


def test_every_timetable_label_fits_above_the_floor() -> None:
    """Every real route label fits its badge without a cut."""
    room = badge_width(px=14) - 8
    floor = Face(weight=800, px=14, squeeze=MIN_SQUEEZE)
    for route in real_labels():
        assert text_width(text=route, face=floor) <= room, route


def test_link_arrow_is_short_and_thin() -> None:
    """The link between badges inks a small box beside its point."""
    canvas = Canvas()
    assert link_arrow(canvas, x=100, y=50, grey=INK) == LINK_ARROW
    ink = canvas.grey() == INK
    assert ink.any()
    assert ink[46:55, 99:109].sum() == ink.sum()
    assert LINK_WIDTH == 12


def test_shared_width_narrows_only_at_the_floor() -> None:
    """Badges share a narrower width only when the floor overflows."""
    full = badge_width(px=14)
    assert shared_width(px=14, count=2, room=None) == full
    assert shared_width(px=14, count=2, room=200) == full
    assert shared_width(px=9, count=2, room=70) == pytest.approx(29.0)
    pair = row_px(count=2, px=14, room=LIST_PAIR_ROOM)
    chips = row_px(count=3, px=CHIP_PX, room=143)
    assert (pair, chips) == (11, 10)
    assert shared_width(
        px=pair, count=2, room=LIST_PAIR_ROOM,
    ) == badge_width(px=pair)
    assert shared_width(px=chips, count=3, room=143) == badge_width(
        px=chips,
    )


@pytest.mark.parametrize('px', [14, 18])
@pytest.mark.parametrize('pair', [('BMT', 'CCN'), ('SH100', '283S2')])
def test_list_pair_keeps_full_labels(px: int, pair: tuple[str, str]) -> None:
    """A carry-on pair fits the list's room with full-width badges."""
    route, onto = pair
    width = route_pair(
        Canvas(), x=0, y=20, route=route, onto=onto, px=px,
        room=LIST_PAIR_ROOM,
    )
    assert width == pytest.approx(2 * badge_width(px=11) + LINK_WIDTH)
    assert width <= LIST_PAIR_ROOM
