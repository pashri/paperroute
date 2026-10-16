"""Draw the Stop view in the round 5 layout."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.model import Mode
from paperroute.core.stop_view import DOT, Pill, Row, StopView
from paperroute.render.canvas import HEIGHT, INK, PAPER, WIDTH, Canvas, Face

HEADER: Final[int] = 40
BANNER: Final[int] = 34
SPLIT: Final[int] = 438
ROW_H: Final[int] = 50
TABS: Final[tuple[str, ...]] = ('Stop', 'Map', 'Strip', 'Strip + map')
LIST_TOP: Final[int] = 30
VEHICLE: Final[dict[Mode, str]] = {
    Mode.BUS: 'bus', Mode.TRAIN: 'train', Mode.METRO: 'metro',
    Mode.FERRY: 'ferry', Mode.LIGHT_RAIL: 'light rail', Mode.COACH: 'coach',
    Mode.REGIONAL: 'service', Mode.OTHER: 'service'}
BAY: Final[dict[Mode, str]] = {Mode.BUS: 'stand', Mode.COACH: 'stand',
                               Mode.FERRY: 'wharf'}


def arrow(canvas: Canvas, *, x: float, y: float, grey: int) -> float:
    """
    Draw a right arrow as shapes and return its width.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left end.
    y : float
        Centre line.
    grey : int
        Colour.

    Returns
    -------
    float
        Width used.
    """
    canvas.line(points=[(x, y), (x + 12, y)], width=2, grey=grey)
    canvas.polygon(
        points=[(x + 16, y), (x + 10, y - 5), (x + 10, y + 5)], grey=grey)
    return 22.0


def header(canvas: Canvas, *, view: StopView) -> None:
    """
    Draw the black header: From, arrow, Destination, tabs, clock.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : StopView
        View.
    """
    canvas.rect(box=(0, 0, WIDTH, HEADER), grey=INK)
    clock = Face(weight=800, px=22)
    right = WIDTH - 12 - canvas.text(
        at=(WIDTH - 12, 20), text=view.clock, face=clock, grey=PAPER,
        anchor='rm')
    for tab in reversed(TABS):
        width = canvas.length(text=tab, face=Face(weight=700, px=12)) + 10
        right -= width + 4
        if tab == 'Stop':
            canvas.rect(box=(right, 11, right + width, 30), grey=PAPER)
        canvas.text(
            at=(right + 5, 25), text=tab, face=Face(weight=700, px=12),
            grey=INK if tab == 'Stop' else PAPER)
    title = Face(weight=700, px=18)
    half = (right - 60) / 2
    origin = canvas.fit(text=view.origin, face=title, width=half)
    x = 12 + canvas.text(
        at=(12, 27), text=origin, face=title, grey=PAPER) + 8
    x += arrow(canvas, x=x, y=20, grey=PAPER)
    destination = canvas.fit(text=view.destination, face=title, width=half)
    canvas.text(at=(x, 27), text=destination, face=title, grey=PAPER)


def banner(canvas: Canvas, *, text: str) -> None:
    """
    Draw the bordered banner under the header.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        Banner text.
    """
    canvas.rect(box=(0, HEADER, WIDTH, HEADER + BANNER), grey=INK)
    canvas.rect(
        box=(3, HEADER + 3, WIDTH - 3, HEADER + BANNER - 3), grey=PAPER)
    face = Face(weight=700, px=15)
    fitted = canvas.fit(text=text, face=face, width=WIDTH - 32)
    canvas.text(at=(16, HEADER + 23), text=fitted, face=face, grey=INK)


def badge(canvas: Canvas, *, x: float, y: float, route: str,
          px: int) -> float:
    """
    Draw a route badge (white on black) and return its width.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left edge.
    y : float
        Vertical centre.
    route : str
        Route label.
    px : int
        Text size.

    Returns
    -------
    float
        Badge width.
    """
    face = Face(weight=800, px=px)
    width = max(canvas.length(text=route, face=face) + 12, px * 2.4)
    canvas.rect(box=(x, y - px * 0.65, x + width, y + px * 0.65), grey=INK)
    canvas.text(
        at=(x + width / 2, y + 1), text=route, face=face, grey=PAPER,
        anchor='mm')
    return width


def hero(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw the next service large on the left.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Next service.
    top : int
        Top of the panel.
    """
    unit = VEHICLE[row.mode]
    canvas.text(
        at=(18, top + 24), text=f'NEXT {unit.upper()}',
        face=Face(weight=700, px=12), grey=INK)
    width = badge(canvas, x=18, y=top + 50, route=row.route, px=20)
    dest = Face(weight=700, px=18)
    fitted = canvas.fit(
        text=row.destination, face=dest, width=SPLIT - 50 - width)
    canvas.text(
        at=(30 + width, top + 57), text=fitted, face=dest, grey=INK)
    big = Face(weight=800, px=104 if row.live else 76)
    shown = row.countdown if row.live else row.leaves_at
    used = canvas.text(at=(18, top + 158), text=shown, face=big, grey=INK)
    if row.live and shown != 'now' and ':' not in shown:
        canvas.text(
            at=(26 + used, top + 158), text='min',
            face=Face(weight=800, px=28), grey=INK)
    canvas.rect(box=(18, top + 184, 418, top + 186), grey=INK)
    canvas.text(
        at=(18, top + 216), text=f'arrives {row.arrives_at}',
        face=Face(weight=700, px=24), grey=INK)
    bay = BAY.get(row.mode, 'platform')
    where = f'{DOT}{bay} {row.platform}' if row.platform else ''
    canvas.text(
        at=(18, top + 240),
        text=f'{row.ride_minutes} min on the {unit}{where}',
        face=Face(weight=400, px=15), grey=INK)


def list_row(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw one list row.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        Row top.
    """
    badge(canvas, x=456, y=y + 14, route=row.route, px=14)
    small = Face(weight=400, px=13)
    fitted = canvas.fit(text=row.destination, face=small, width=140)
    canvas.text(at=(456, y + 42), text=fitted, face=small, grey=INK)
    if row.pill is Pill.CANCELLED:
        canvas.rect(box=(600, y + 10, 786, y + 38), grey=INK)
        canvas.text(
            at=(693, y + 24), text=f'{row.leaves_at}  CANCELLED',
            face=Face(weight=700, px=15), grey=PAPER, anchor='mm')
    else:
        shown = row.countdown if row.live else row.leaves_at
        canvas.text(
            at=(640, y + 30), text=shown, face=Face(weight=800, px=28),
            grey=INK, anchor='rs')
        canvas.text(
            at=(786, y + 24), text=row.arrives_at,
            face=Face(weight=800, px=20), grey=INK, anchor='rs')
    if row.pill is Pill.FASTER:
        canvas.rect(box=(712, y + 30, 786, y + 45), grey=INK)
        canvas.text(
            at=(749, y + 38), text='FASTER', face=Face(weight=700, px=11),
            grey=PAPER, anchor='mm')
    canvas.rect(box=(452, y + 49, 786, y + 50), grey=INK)


def draw_stop_view(*, view: StopView) -> NDArray[np.uint8]:
    """
    Draw a Stop view as a 1-bit frame.

    Parameters
    ----------
    view : StopView
        View-model.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    header(canvas, view=view)
    top = HEADER
    if view.banner:
        banner(canvas, text=view.banner)
        top += BANNER
    if view.empty:
        canvas.text(
            at=(WIDTH / 2, 250), text=view.empty,
            face=Face(weight=700, px=22), grey=INK, anchor='mm')
    if view.hero:
        hero(canvas, row=view.hero, top=top)
        canvas.rect(box=(SPLIT, top, SPLIT + 3, 480), grey=INK)
        canvas.text(
            at=(640, top + 22), text='LEAVES', face=Face(weight=700, px=11),
            grey=INK, anchor='rs')
        canvas.text(
            at=(786, top + 22), text='ARRIVES',
            face=Face(weight=700, px=11), grey=INK, anchor='rs')
    fits = (HEIGHT - top - LIST_TOP) // ROW_H
    for index, item in enumerate(view.rows[:fits]):
        list_row(canvas, row=item, y=top + LIST_TOP + index * ROW_H)
    grey = canvas.grey()
    return np.where(grey < 128, 0, 255).astype(np.uint8)
