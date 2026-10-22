"""Draw the Stop view in the round 5 layout."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.model import Mode
from paperroute.core.stop_view import DOT, Pill, Row, StopView, stops_text
from paperroute.render.canvas import HEIGHT, INK, WIDTH, Canvas, Face
from paperroute.render.chrome import (
    ARROW_GAP,
    ARROW_WIDTH,
    BANNER,
    FASTER,
    HEADER,
    arrow,
    banner,
    cancelled,
    countdown_slot,
    leaves,
    pill,
    route_pair,
    view_header,
)

SPLIT: Final[int] = 438
ROW_H: Final[int] = 50
LIST_TOP: Final[int] = 30
VEHICLE: Final[dict[Mode, str]] = {
    Mode.BUS: 'bus', Mode.TRAIN: 'train', Mode.METRO: 'metro',
    Mode.FERRY: 'ferry', Mode.LIGHT_RAIL: 'light rail', Mode.COACH: 'coach',
    Mode.REGIONAL: 'service', Mode.OTHER: 'service'}
LIST_LEFT: Final[int] = 456
LIST_WIDTH: Final[int] = 140
LIST_PAIR_ROOM: Final[int] = 92  # clear of a '99 min' countdown
FASTER_LEFT: Final[int] = 712
PILL_LEFT: Final[int] = 600
TAGGED_WIDTH: Final[int] = FASTER_LEFT - LIST_LEFT - 16
BOARD_BADGE_X: Final[int] = 96
BOARD_RIGHT: Final[int] = PILL_LEFT - 20
TAG_FACE: Final[Face] = Face(weight=700, px=12)
TAG_GAP: Final[int] = 8
TAG_FLOOR: Final[int] = 40
BAY: Final[dict[Mode, str]] = {Mode.BUS: 'stand', Mode.COACH: 'stand',
                               Mode.FERRY: 'wharf'}


def hero_headline(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw the vehicle label, route badge and destination of the hero.

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
    width = route_pair(
        canvas, x=18, y=top + 50, route=row.route, onto=row.onto, px=20,
    )
    dest = Face(weight=700, px=18)
    fitted = canvas.fit(
        text=row.destination, face=dest, width=SPLIT - 50 - width)
    canvas.text(
        at=(30 + width, top + 57), text=fitted, face=dest, grey=INK)


def hero_countdown(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw the big countdown or clock time of the hero.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Next service.
    top : int
        Top of the panel.
    """
    big = Face(weight=800, px=104 if row.live else 76)
    shown = row.countdown if row.live else row.leaves_at
    used = canvas.text(at=(18, top + 158), text=shown, face=big, grey=INK)
    if row.live and shown != 'now' and ':' not in shown:
        canvas.text(
            at=(26 + used, top + 158), text='min',
            face=Face(weight=800, px=28), grey=INK)


def hero_footer(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw the rule, arrival time and ride line of the hero.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Next service.
    top : int
        Top of the panel.
    """
    canvas.rect(box=(18, top + 184, 418, top + 186), grey=INK)
    canvas.text(
        at=(18, top + 216), text=f'arrives {row.arrives_at}',
        face=Face(weight=700, px=24), grey=INK)
    bay = BAY.get(row.mode, 'platform')
    where = f'{DOT}{bay} {row.platform}' if row.platform else ''
    ride = Face(weight=400, px=15)
    count = (
        '' if row.stops is None else f'{DOT}{stops_text(stops=row.stops)}'
    )
    text = (
        f'{row.ride_minutes} min on the {VEHICLE[row.mode]}{where}{count}'
    )
    canvas.text(
        at=(18, top + 240), text=canvas.fit(text=text, face=ride, width=400),
        face=ride, grey=INK)


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
    hero_headline(canvas, row=row, top=top)
    hero_countdown(canvas, row=row, top=top)
    hero_footer(canvas, row=row, top=top)


def headline(
    canvas: Canvas,
    *,
    row: Row,
    at: tuple[float, float],
    text: str,
    face: Face,
    width: float,
) -> None:
    """
    Draw a headsign and, after it, the tag for where the vehicle runs on.

    The headsign and tag are fitted together into `width`. The headsign
    keeps what it needs; the tag gets the rest, but never less than a
    floor.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    at : tuple[float, float]
        Left end and baseline.
    text : str
        Headsign text.
    face : Face
        Headsign face.
    width : float
        Width for the headsign and tag together.
    """
    then = row.then
    if then is None:
        fitted = canvas.fit(text=text, face=face, width=width)
        canvas.text(at=at, text=fitted, face=face, grey=INK)
        return
    lead = ARROW_WIDTH + ARROW_GAP
    tag = f'{then.route} {then.headsign}'
    room = width - TAG_GAP - canvas.length(text=text, face=face)
    tag_width = min(
        canvas.length(text=tag, face=TAG_FACE) + lead,
        max(room, lead + TAG_FLOOR),
    )
    fitted = canvas.fit(
        text=text, face=face, width=width - tag_width - TAG_GAP)
    x, baseline = at
    x += canvas.text(at=at, text=fitted, face=face, grey=INK) + TAG_GAP
    arrow(canvas, x=x, y=baseline - 4, grey=INK)
    canvas.text(
        at=(x + lead, baseline),
        text=canvas.fit(text=tag, face=TAG_FACE, width=tag_width - lead),
        face=TAG_FACE,
        grey=INK,
    )


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
    route_pair(
        canvas,
        x=LIST_LEFT,
        y=y + 14,
        route=row.route,
        onto=row.onto,
        px=14,
        room=LIST_PAIR_ROOM,
    )
    headline(
        canvas,
        row=row,
        at=(LIST_LEFT, y + 42),
        text=row.destination,
        face=Face(weight=400, px=13),
        width=LIST_WIDTH if row.then is None else TAGGED_WIDTH,
    )
    if row.pill is Pill.CANCELLED:
        pill(
            canvas,
            box=(PILL_LEFT, y + 10, 786, y + 38),
            text=cancelled(row=row),
            px=15,
        )
    else:
        canvas.text(
            at=(640, y + 30), text=leaves(row=row),
            face=Face(weight=800, px=28), grey=INK, anchor='rs')
        canvas.text(
            at=(786, y + 24), text=row.arrives_at,
            face=Face(weight=800, px=20), grey=INK, anchor='rs')
    if row.pill is Pill.FASTER:
        pill(
            canvas,
            box=(FASTER_LEFT, y + 30, 786, y + 45),
            text=FASTER,
            px=11,
        )
    canvas.rect(box=(452, y + 49, 786, y + 50), grey=INK)


def board_row(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw one full-width row of the board with no destination.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        Row top.
    """
    canvas.text(
        at=(18, y + 33), text=row.leaves_at,
        face=Face(weight=800, px=22), grey=INK)
    width = route_pair(
        canvas,
        x=BOARD_BADGE_X,
        y=y + 25,
        route=row.route,
        onto=row.onto,
        px=16,
    )
    left = BOARD_BADGE_X + width + 14
    headline(
        canvas,
        row=row,
        at=(left, y + 31),
        text=f'to {row.destination}',
        face=Face(weight=400, px=17),
        width=BOARD_RIGHT - left,
    )
    if row.pill is Pill.CANCELLED:
        pill(
            canvas,
            box=(PILL_LEFT, y + 10, 786, y + 38),
            text=cancelled(row=row),
            px=15,
        )
    else:
        canvas.text(
            at=(786, y + 33), text=countdown_slot(row=row),
            face=Face(weight=800, px=28), grey=INK, anchor='rs')
    if row.pill is Pill.FASTER:
        pill(
            canvas,
            box=(PILL_LEFT, y + 17, PILL_LEFT + 74, y + 32),
            text=FASTER,
            px=11,
        )
    canvas.rect(box=(0, y + 49, WIDTH, y + 50), grey=INK)


def finish(canvas: Canvas) -> NDArray[np.uint8]:
    """
    Threshold a canvas to a 1-bit frame.

    Parameters
    ----------
    canvas : Canvas
        Drawn canvas.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    grey = canvas.grey()
    return np.where(grey < 128, 0, 255).astype(np.uint8)


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
    view_header(canvas, view=view, active='Stop')
    top = HEADER
    if view.banner:
        banner(canvas, text=view.banner, warning=view.warning)
        top += BANNER
    if view.empty:
        face = Face(weight=700, px=22)
        message = canvas.fit(text=view.empty, face=face, width=WIDTH - 64)
        canvas.text(
            at=(WIDTH / 2, 250), text=message, face=face, grey=INK,
            anchor='mm')
    if view.hero:
        hero(canvas, row=view.hero, top=top)
        canvas.rect(box=(SPLIT, top, SPLIT + 3, HEIGHT), grey=INK)
        canvas.text(
            at=(640, top + 22), text='LEAVES', face=Face(weight=700, px=11),
            grey=INK, anchor='rs')
        canvas.text(
            at=(786, top + 22), text='ARRIVES',
            face=Face(weight=700, px=11), grey=INK, anchor='rs')
    if view.destination is None:
        for index, item in enumerate(view.rows[:(HEIGHT - top) // ROW_H]):
            board_row(canvas, row=item, y=top + index * ROW_H)
        return finish(canvas)
    fits = (HEIGHT - top - LIST_TOP) // ROW_H
    for index, item in enumerate(view.rows[:fits]):
        list_row(canvas, row=item, y=top + LIST_TOP + index * ROW_H)
    return finish(canvas)
