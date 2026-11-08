"""Facts about the drawn connection Stop view (no golden images)."""

from dataclasses import replace
from typing import Final

from paperroute_device.render.canvas import HEIGHT, Canvas
from paperroute_device.render.frame import HEADER, finish
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.connection_factories import WORK, at, work_chains
from paperroute_transit.core.connection_view import (
    NO_PAIRS,
    ConnectionView,
    PairRow,
    build_connection_view,
)
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.render.connection_view import (
    FOOTER_RULE,
    draw_connection_view,
)
from paperroute_transit.render.pair_list import draw_pair_list
from paperroute_transit.render.stop_view import SPLIT
from tests.core.test_connection_view import TWO, TWO_CHANGES

ROW: Final[PairRow] = PairRow(
    leaves_at='7:48',
    chips=('606', '664'),
    detail='at Constitution Hill, North West Twy',
    arrives_at='8:12',
    minutes=24,
)
VIEW_STOP: Final[StopView] = StopView(
    origin='Hollis St Opp Constitution Hill Library',
    destination='Westmead Station',
    clock='7:30',
    hero=None,
    rows=(),
    banner=None,
    empty=None,
    scroll=0,
)
VIEW: Final[ConnectionView] = build_connection_view(
    stop_view=VIEW_STOP,
    pin=WORK,
    chains=work_chains(),
    scroll=0,
    now=at(7, 30),
    zone=SYDNEY_ZONE,
    city=SYDNEY,
)


def test_frame_is_one_bit_800_by_480() -> None:
    """Frames are 480 by 800 and use only ink and paper."""
    frame = draw_connection_view(view=VIEW)
    assert frame.shape == (480, 800)
    assert set(frame.flatten().tolist()) <= {0, 255}


def test_split_rule_below_header() -> None:
    """The column at the split is ink from the header down."""
    frame = draw_connection_view(view=VIEW)
    assert frame[HEADER + 2:HEIGHT, SPLIT + 1].max() == 0


def test_hero_footer_rule() -> None:
    """The hero's footer rule is a solid row across the hero."""
    frame = draw_connection_view(view=VIEW)
    assert frame[HEADER + FOOTER_RULE, 18:418].max() == 0


def test_empty_text_drawn_left() -> None:
    """With no hero, the empty text is drawn in the left half."""
    view = replace(VIEW, hero=None, rows=(), empty=NO_PAIRS)
    frame = draw_connection_view(view=view)
    assert frame[HEADER + 20:HEIGHT, :SPLIT].min() == 0
    assert frame[HEADER + 2:HEIGHT, SPLIT + 3:].min() == 255


def test_selected_row_inverted() -> None:
    """A selected row's band is mostly ink."""
    canvas = Canvas()
    draw_pair_list(
        canvas, rows=(ROW, ROW), top=100, left=450, right=790, step=70,
        selected=0,
    )
    frame = finish(canvas)
    assert frame[101:169, 450:790].mean() < 128
    assert frame[171:239, 450:790].mean() > 128


def test_first_detail_replaces_line() -> None:
    """The first row's detail can be swapped; the drawing changes."""
    def drawn(first_detail: str | None) -> bytes:
        canvas = Canvas()
        draw_pair_list(
            canvas, rows=(ROW,), top=100, left=450, right=790, step=70,
            first_detail=first_detail,
        )
        return finish(canvas).tobytes()

    assert drawn(None) != drawn('24 min, change at Constitution Hill')


def test_pair_detail_uses_full_row() -> None:
    """A long detail runs under the arrive time, not up to it."""
    canvas = Canvas()
    draw_pair_list(
        canvas,
        rows=(replace(ROW, detail='24 min · change at Constitution Hill'),),
        top=0,
        left=0,
        right=330,
        step=72,
    )
    grey = canvas.grey()
    assert grey[38:56, 270:324].min() < 128


def test_two_change_hero_fits_above_footer() -> None:
    """Three badge lines and two change lines stay above the footer."""
    view = build_connection_view(
        stop_view=replace(VIEW_STOP, destination='Parramatta Station'),
        pin=TWO,
        chains=(TWO_CHANGES,),
        scroll=0,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    frame = draw_connection_view(view=view)
    rule = HEADER + FOOTER_RULE
    assert frame[rule, 18:418].max() == 0
    assert frame[rule - 6:rule, 18:418].min() == 255
    assert frame[HEADER + 60:rule - 8, 18:60].min() == 0
