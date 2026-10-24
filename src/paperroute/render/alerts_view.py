"""Draw the Alerts view: a list of headlines and the selected text."""

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.alerts_view import AlertRow, AlertsView, AlertText
from paperroute.render.canvas import HEIGHT, INK, PAPER, Canvas, Face
from paperroute.render.chrome import ALERT_TABS, HEADER, view_header
from paperroute.render.map_view import wrap
from paperroute.render.stop_view import finish

ALERT_SPLIT: Final[int] = 330
ALERT_ROW_H: Final[int] = 70
HEADLINE_FACE: Final[Face] = Face(weight=700, px=15)
WHEN_FACE: Final[Face] = Face(weight=400, px=13)
TITLE_FACE: Final[Face] = Face(weight=700, px=18)
BODY_FACE: Final[Face] = Face(weight=400, px=16)
MARKER_FACE: Final[Face] = Face(weight=700, px=13)
BODY_PITCH: Final[int] = 21
TITLE_PITCH: Final[int] = 24
WHEN_PITCH: Final[int] = 19
PARAGRAPH_GAP: Final[int] = 6
RULE: Final[int] = 3
TEXT_LEFT: Final[int] = 345
TEXT_RIGHT: Final[int] = 784
TEXT_TOP: Final[int] = HEADER + 14
TEXT_HEIGHT: Final[int] = 392
MARK_X: Final[int] = 12
MARK_SIDE: Final[int] = 18
HEADLINE_LEFT: Final[int] = 40
HEADLINE_LINES: Final[int] = 2
HEADLINE_PITCH: Final[int] = 17


@dataclass(frozen=True)
class Line:
    """
    One line of the selected text.

    Attributes
    ----------
    text : str
        The words on the line.
    face : Face
        Its font.
    pitch : int
        Height of the line, including any gap after it.
    """

    text: str
    face: Face
    pitch: int


def lines_of(*, text: AlertText, width: float) -> tuple[Line, ...]:
    """
    Wrap the whole text into lines.

    Parameters
    ----------
    text : AlertText
        The selected alert's text.
    width : float
        Text column width in pixels.

    Returns
    -------
    tuple[Line, ...]
        Title, time line, body paragraphs and `also` lines; a gap
        follows the time line and each paragraph.
    """
    found: list[Line] = []
    blocks = [
        (text.title, TITLE_FACE, TITLE_PITCH, 0),
        (text.when, WHEN_FACE, WHEN_PITCH, PARAGRAPH_GAP * 2),
        *(
            (paragraph, BODY_FACE, BODY_PITCH, PARAGRAPH_GAP)
            for paragraph in text.body.split('\n')
        ),
        *((line, BODY_FACE, BODY_PITCH, 0) for line in text.also),
    ]
    for block, face, pitch, gap in blocks:
        wrapped = wrap(text=block, face=face, width=width)
        found.extend(
            Line(text=line, face=face, pitch=pitch) for line in wrapped
        )
        if found and gap:
            last = found[-1]
            found[-1] = Line(
                text=last.text, face=last.face, pitch=last.pitch + gap,
            )
    return tuple(found)


def screenfuls(
    *, lines: tuple[Line, ...], height: float
) -> tuple[tuple[Line, ...], ...]:
    """
    Cut lines into pages that each fit a height.

    Parameters
    ----------
    lines : tuple[Line, ...]
        All the lines.
    height : float
        Page height in pixels.

    Returns
    -------
    tuple[tuple[Line, ...], ...]
        The pages; at least one, and every page holds at least one line.
    """
    pages: list[list[Line]] = [[]]
    used = 0
    for line in lines:
        if pages[-1] and used + line.pitch > height:
            pages.append([])
            used = 0
        pages[-1].append(line)
        used += line.pitch
    return tuple(tuple(page) for page in pages)


def pages_of(
    *, text: AlertText, width: float, height: float
) -> tuple[tuple[str, ...], ...]:
    """
    Wrap an alert's text and cut it into screenfuls.

    Parameters
    ----------
    text : AlertText
        The selected alert's text.
    width : float
        Text column width in pixels.
    height : float
        Text column height in pixels.

    Returns
    -------
    tuple[tuple[str, ...], ...]
        The lines of each page; at least one page.
    """
    pages = screenfuls(lines=lines_of(text=text, width=width), height=height)
    return tuple(tuple(line.text for line in page) for page in pages)


def mark(canvas: Canvas, *, y: float, active: bool) -> None:
    """
    Draw an alert's triangle: solid when active, an outline when coming.

    Parameters
    ----------
    canvas : Canvas
        Target.
    y : float
        Vertical centre.
    active : bool
        True for a solid triangle.
    """
    height = MARK_SIDE * 3 ** 0.5 / 2
    top, bottom = y - height / 2, y + height / 2
    left, right = MARK_X, MARK_X + MARK_SIDE
    middle = MARK_X + MARK_SIDE / 2
    canvas.polygon(
        points=[(left, bottom), (middle, top), (right, bottom)], grey=INK,
    )
    if not active:
        canvas.polygon(
            points=[
                (left + 5, bottom - 2.5),
                (middle, top + 6),
                (right - 5, bottom - 2.5),
            ],
            grey=PAPER,
        )


def draw_row(canvas: Canvas, *, row: AlertRow, top: int) -> None:
    """
    Draw one list row: the mark, up to two headline lines, the time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : AlertRow
        The row.
    top : int
        Top of the row.
    """
    mark(canvas, y=top + 24, active=row.active)
    room = ALERT_SPLIT - HEADLINE_LEFT - 8
    lines = list(wrap(text=row.headline, face=HEADLINE_FACE, width=room))
    if len(lines) > HEADLINE_LINES:
        rest = ' '.join(lines[HEADLINE_LINES - 1:])
        lines = [*lines[:HEADLINE_LINES - 1], rest]
    for index, line in enumerate(lines):
        shown = canvas.fit(text=line, face=HEADLINE_FACE, width=room)
        canvas.text(
            at=(HEADLINE_LEFT, top + 20 + index * HEADLINE_PITCH),
            text=shown,
            face=HEADLINE_FACE,
            grey=INK,
        )
    when_y = top + 20 + (len(lines) - 1) * HEADLINE_PITCH + 20
    canvas.text(
        at=(HEADLINE_LEFT, when_y),
        text=canvas.fit(text=row.when, face=WHEN_FACE, width=room),
        face=WHEN_FACE,
        grey=INK,
    )


def draw_list(canvas: Canvas, *, view: AlertsView) -> None:
    """
    Draw the list column, inverting the selected row.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : AlertsView
        The view.
    """
    for index, row in enumerate(view.rows):
        top = HEADER + index * ALERT_ROW_H
        draw_row(canvas, row=row, top=top)
        if index == view.selected:
            canvas.invert(box=(0, top, ALERT_SPLIT, top + ALERT_ROW_H))
    canvas.rect(
        box=(ALERT_SPLIT, HEADER, ALERT_SPLIT + RULE, HEIGHT), grey=INK,
    )


def draw_text(canvas: Canvas, *, view: AlertsView) -> None:
    """
    Draw the selected page of the text, and the page marker.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : AlertsView
        The view.
    """
    lines = lines_of(text=view.text, width=TEXT_RIGHT - TEXT_LEFT)
    pages = screenfuls(lines=lines, height=TEXT_HEIGHT)
    top = TEXT_TOP
    for line in pages[view.page % len(pages)]:
        canvas.text(
            at=(TEXT_LEFT, top + line.face.px),
            text=line.text,
            face=line.face,
            grey=INK,
        )
        top += line.pitch
    if len(pages) > 1:
        canvas.text(
            at=(TEXT_RIGHT, HEIGHT - 10),
            text=f'{view.page % len(pages) + 1}/{len(pages)}',
            face=MARKER_FACE,
            grey=INK,
            anchor='rs',
        )


def draw_alerts_view(*, view: AlertsView) -> NDArray[np.uint8]:
    """
    Draw the Alerts view as a 1-bit frame.

    Parameters
    ----------
    view : AlertsView
        The view-model.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    view_header(canvas, view=view, active='Alerts', tabs=ALERT_TABS)
    draw_list(canvas, view=view)
    draw_text(canvas, view=view)
    return finish(canvas)
