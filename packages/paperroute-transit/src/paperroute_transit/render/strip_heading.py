"""Draw a street heading and the bracket closing its run."""

from collections.abc import Sequence
from typing import Final

from paperroute_device.render.canvas import INK, Canvas, Face
from paperroute_transit.core.strip_model import ROW_H, StripRow
from paperroute_transit.core.strip_streets import HEADING_ROOM

HEADING_SIZE: Final[int] = 11  # px, bold caps
HEADING_FACE: Final[Face] = Face(weight=700, px=HEADING_SIZE)
BRACKET: Final[float] = 2.0  # px bar
FOOT: Final[float] = 6.0  # px
INDENT: Final[float] = 8.0  # px names in a run move right
TAB_GAP: Final[float] = 3.0  # px before the first letter
FOOT_DROP: Final[float] = 7.0  # px from the last row's centre to its foot


def heading_mid(*, top: float) -> float:
    """
    Find the middle of the room above a run.

    Parameters
    ----------
    top : float
        The centre of the run's first row.

    Returns
    -------
    float
        The y the street name and the tab are centred on.
    """
    return top - (ROW_H + HEADING_ROOM) / 2


def run_bottom(*, rows: Sequence[StripRow], start: int) -> float:
    """
    Find the centre of a run's last row.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Rows, top to bottom.
    start : int
        The run's first row.

    Returns
    -------
    float
        The centre of the last row with its own part, before the next
        heading.
    """
    bottom = rows[start].y
    for row in rows[start + 1:]:
        if row.heading or not row.short:
            break
        bottom = row.y
    return bottom


def draw_headings(
    canvas: Canvas, *, rows: Sequence[StripRow], name_x: int,
) -> None:
    """
    Draw each run's street and the bracket down to its last row.

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : Sequence[StripRow]
        Rows, top to bottom.
    name_x : int
        Name x at depth 0, where the bracket's bar sits.
    """
    for start, row in enumerate(rows):
        if not row.heading:
            continue
        mid = heading_mid(top=row.y)
        bottom = run_bottom(rows=rows, start=start) + FOOT_DROP
        canvas.rect(
            box=(name_x, mid - 1, name_x + BRACKET, bottom + BRACKET),
            grey=INK,
        )
        canvas.rect(box=(name_x, mid - 1, name_x + FOOT, mid + 1), grey=INK)
        canvas.rect(
            box=(name_x, bottom, name_x + FOOT, bottom + BRACKET), grey=INK,
        )
        canvas.text(
            at=(name_x + FOOT + TAB_GAP, mid),
            text=row.heading.upper(),
            face=HEADING_FACE,
            grey=INK,
            anchor='lm',
        )
