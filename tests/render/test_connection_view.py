"""Facts about the drawn connection Stop view (no golden images)."""

from dataclasses import replace
from typing import Final

from paperroute.core.connection_model import pairs_of
from paperroute.core.connection_view import (
    NO_PAIRS,
    ConnectionView,
    PairRow,
    build_connection_view,
)
from paperroute.core.stop_view import StopView
from paperroute.render.canvas import HEIGHT, Canvas
from paperroute.render.chrome import HEADER
from paperroute.render.connection_view import FOOTER_RULE, draw_connection_view
from paperroute.render.pair_list import draw_pair_list
from paperroute.render.stop_view import SPLIT, finish
from tests.core.connection_factories import LEG1, LEG2, WORK, at
from tests.core.factories import live

ROW: Final[PairRow] = PairRow(
    leaves_at='7:48',
    route='606',
    onto='664',
    detail='change 7:54, bus 8:01',
    arrives_at='8:12',
    minutes=24,
)
VIEW: Final[ConnectionView] = build_connection_view(
    stop_view=StopView(
        origin='Hollis St Opp Constitution Hill Library',
        destination='Westmead Station',
        clock='7:30',
        hero=None,
        rows=(),
        banner=None,
        empty=None,
        scroll=0,
    ),
    pin=WORK,
    pairs=pairs_of(
        first=tuple(live(item=item) for item in LEG1),
        second=tuple(live(item=item) for item in LEG2),
        direct=(),
        allow=7,
        now=at(7, 30),
    ),
    scroll=0,
    now=at(7, 30),
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
