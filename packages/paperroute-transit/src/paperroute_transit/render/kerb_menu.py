"""Draw the two-way mark and the box offering one direction or both."""

from typing import Final

from paperroute_device.render.canvas import INK, PAPER, Canvas, Face
from paperroute_device.render.panel import HEADER_FACE
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.strip_model import KerbMenu
from paperroute_transit.render.names import Naming, fit_name

MARK_RIGHT_GAP: Final[int] = 71  # mark's right end to the times' edge
MARK_WIDTH: Final[int] = 26
MARK_SPLIT: Final[int] = 5  # each arrow above or below the row's centre
MARK_HEAD: Final[int] = 7  # arrowhead length
MARK_STROKE: Final[int] = 2
MENU_BOX: Final[tuple[int, int, int, int]] = (20, 152, 440, 332)
BORDER: Final[int] = 3
MENU_PAD: Final[int] = 4
TITLE_H: Final[int] = 42
MENU_ROW: Final[int] = 44
ROW_FACE: Final[Face] = Face(weight=400, px=20)
CHOSEN_FACE: Final[Face] = Face(weight=700, px=20)


def two_way_mark(
    canvas: Canvas, *, right: float, y: float, grey: int,
) -> float:
    """
    Draw an arrow pointing right over one pointing left.

    Parameters
    ----------
    canvas : Canvas
        Target.
    right : float
        Right end of the mark.
    y : float
        Row centre.
    grey : int
        Stroke level.

    Returns
    -------
    float
        The mark's left end.
    """
    for sign, dy in ((1, -MARK_SPLIT), (-1, MARK_SPLIT)):
        tail = right - MARK_WIDTH if sign > 0 else right
        tip = right if sign > 0 else right - MARK_WIDTH
        line_y = y + dy
        canvas.line(
            points=((tail, line_y), (tip, line_y)),
            width=MARK_STROKE,
            grey=grey,
        )
        base = tip - sign * MARK_HEAD
        canvas.polygon(
            points=(
                (tip, line_y),
                (base, line_y - MARK_SPLIT),
                (base, line_y + MARK_SPLIT),
            ),
            grey=grey,
        )
    return right - MARK_WIDTH


def menu_row(
    canvas: Canvas, *, label: str, top: float, chosen: bool,
) -> None:
    """
    Draw one row of the box, filled when the cursor is on it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    label : str
        Row text.
    top : float
        Row top edge.
    chosen : bool
        True when the cursor is on the row.
    """
    left, _, right, _ = MENU_BOX
    if chosen:
        canvas.rect(
            box=(left + BORDER, top, right - BORDER, top + MENU_ROW),
            grey=INK,
        )
    face = CHOSEN_FACE if chosen else ROW_FACE
    canvas.text(
        at=(left + BORDER + 2 * MENU_PAD, top + MENU_ROW / 2),
        text=canvas.fit(
            text=label,
            face=face,
            width=right - left - 2 * BORDER - 4 * MENU_PAD,
        ),
        face=face,
        grey=PAPER if chosen else INK,
        anchor='lm',
    )


def draw_kerb_menu(
    canvas: Canvas, *, menu: KerbMenu, rules: NameRules,
) -> None:
    """
    Draw the bordered box with the station's name and the choices.

    Parameters
    ----------
    canvas : Canvas
        Target.
    menu : KerbMenu
        The box.
    rules : NameRules
        Word lists and options for shortening the title.
    """
    left, top, right, bottom = MENU_BOX
    canvas.rect(box=MENU_BOX, grey=INK)
    canvas.rect(
        box=(left + BORDER, top + BORDER, right - BORDER, bottom - BORDER),
        grey=PAPER,
    )
    title = fit_name(
        text=menu.title,
        face=HEADER_FACE,
        width=right - left - 2 * (BORDER + 2 * MENU_PAD),
        naming=Naming(rules=rules),
    )[0]
    canvas.text(
        at=(left + BORDER + 2 * MENU_PAD, top + BORDER + TITLE_H / 2 + 2),
        text=title,
        face=HEADER_FACE,
        grey=INK,
        anchor='lm',
    )
    first = top + BORDER + TITLE_H
    canvas.rect(
        box=(left + BORDER, first - 2, right - BORDER, first), grey=INK,
    )
    for slot, label in enumerate(menu.labels):
        menu_row(
            canvas,
            label=label,
            top=first + slot * MENU_ROW,
            chosen=slot == menu.cursor,
        )
