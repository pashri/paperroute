"""Facts about the two-way mark and the direction box."""

from typing import Final

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.strip_model import KerbMenu
from paperroute_transit.render.kerb_menu import (
    BORDER,
    MARK_SPLIT,
    MARK_WIDTH,
    MENU_BOX,
    MENU_ROW,
    TITLE_H,
    draw_kerb_menu,
    two_way_mark,
)

MENU: Final[KerbMenu] = KerbMenu(
    title='Orchard Close',
    labels=('Both directions', 'Towards Emmanuel Street', 'Towards Oakington'),
    cursor=1,
)


def test_two_way_mark_draws_an_arrow_each_way() -> None:
    """Draw an arrow above and one below the centre, inside the width."""
    canvas = Canvas()
    left = two_way_mark(canvas, right=353.0, y=100.0, grey=INK)
    frame = canvas.grey()
    assert left == 353.0 - MARK_WIDTH
    assert frame[100 - MARK_SPLIT, 340] == INK
    assert frame[100 + MARK_SPLIT, 340] == INK
    assert frame[100, 340] == PAPER
    assert (frame[:, :int(left) - 1] == PAPER).all()
    assert (frame[:, 355:] == PAPER).all()


def test_draw_kerb_menu_boxes_the_rows_with_the_cursor_filled() -> None:
    """Fill the cursor row and draw nothing outside the box."""
    canvas = Canvas()
    draw_kerb_menu(canvas, menu=MENU, rules=NameRules())
    frame = canvas.grey()
    left, top, right, bottom = MENU_BOX
    middle = (top + bottom) // 2
    assert frame[middle, left + 1] == INK and frame[middle, right - 2] == INK
    first = top + BORDER + TITLE_H
    assert frame[first + MENU_ROW + 2, left + BORDER + 1] == INK
    assert frame[first + 2, left + BORDER + 1] == PAPER
    assert (frame[top + BORDER:first - 2, left + BORDER:right - BORDER]
            == INK).any()
    assert (frame[:top] == PAPER).all() and (frame[bottom:] == PAPER).all()
