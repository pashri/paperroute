"""Drawing a countdown with its lateness mark."""

from typing import Final

from paperroute_device.render.canvas import (
    INK,
    Canvas,
    Face,
    Point,
    font,
    text_width,
)
from paperroute_transit.core.late_marks import row_mark
from paperroute_transit.core.stop_view import Row

MARK_GAP: Final[int] = 3  # pixels between a countdown and its mark
SMALL_MARK: Final[Face] = Face(weight=700, px=14)


def cap_lift(*, face: Face, mark_face: Face) -> float:
    """
    Measure how far a mark is raised to meet a text's capitals.

    Parameters
    ----------
    face : Face
        Font of the text the mark follows.
    mark_face : Face
        Font of the mark.

    Returns
    -------
    float
        Pixels between the two fonts' cap heights.
    """
    return float(
        font(face).getMetrics().fCapHeight
        - font(mark_face).getMetrics().fCapHeight,
    )


def mark_width(*, mark: str, face: Face) -> float:
    """
    Measure the room a mark takes after its text.

    Parameters
    ----------
    mark : str
        The mark; empty for none.
    face : Face
        Font of the mark.

    Returns
    -------
    float
        Mark width plus the gap before it; zero with no mark.
    """
    if not mark:
        return 0.0
    return text_width(text=mark, face=face) + MARK_GAP


def raised_mark(
    canvas: Canvas, *, mark: str, at: Point, face: Face, mark_face: Face,
) -> float:
    """
    Draw a mark after a text, its top level with the text's capitals.

    Parameters
    ----------
    canvas : Canvas
        Canvas to draw on.
    mark : str
        The mark; nothing is drawn when empty.
    at : Point
        Right end of the text the mark follows, on its baseline.
    face : Face
        Font of that text.
    mark_face : Face
        Font of the mark.

    Returns
    -------
    float
        The room the mark takes; see `mark_width`.
    """
    if not mark:
        return 0.0
    right, baseline = at
    lift = cap_lift(face=face, mark_face=mark_face)
    canvas.text(
        at=(right + MARK_GAP, baseline - lift), text=mark, face=mark_face,
        grey=INK,
    )
    return mark_width(mark=mark, face=mark_face)


def mark_room(*, row: Row, face: Face) -> float:
    """
    Measure the room a row's mark takes after its countdown.

    Parameters
    ----------
    row : Row
        The stop row.
    face : Face
        Font of the mark.

    Returns
    -------
    float
        Mark width plus the gap before it; zero with no mark.
    """
    return mark_width(mark=row_mark(row=row), face=face)


def marked_countdown(
    canvas: Canvas,
    *,
    row: Row,
    text: str,
    at: Point,
    face: Face,
    mark_face: Face,
) -> float:
    """
    Draw a countdown right-anchored, with its mark after it.

    The mark is raised so its top is level with the countdown's capitals.

    Parameters
    ----------
    canvas : Canvas
        Canvas to draw on.
    row : Row
        The stop row.
    text : str
        The countdown.
    at : Point
        Right end of the group, on the baseline.
    face : Face
        Font of the countdown.
    mark_face : Face
        Font of the mark.

    Returns
    -------
    float
        Width of the countdown and the mark room.
    """
    right, baseline = at
    mark = row_mark(row=row)
    if mark:
        lift = cap_lift(face=face, mark_face=mark_face)
        canvas.text(
            at=(right, baseline - lift), text=mark, face=mark_face,
            grey=INK, anchor='rs',
        )
    room = mark_room(row=row, face=mark_face)
    width = canvas.text(
        at=(right - room, baseline), text=text, face=face, grey=INK,
        anchor='rs',
    )
    return width + room
