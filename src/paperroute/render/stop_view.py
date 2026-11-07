"""Draw the Stop view in the round 5 layout."""

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.stop_view import Pill, Row, StopView, stops_text
from paperroute.render.chrome import (
    ARROW_GAP,
    ARROW_WIDTH,
    BANNER,
    FASTER,
    arrow,
    cancelled,
    countdown_slot,
    leaves,
    pill,
    route_pair,
    view_banner,
    view_header,
)
from paperroute_device.core.text import DOT
from paperroute_device.render.canvas import (
    HEIGHT,
    INK,
    WIDTH,
    Canvas,
    Face,
    text_width,
)
from paperroute_device.render.frame import HEADER, finish
from paperroute_transit.core.model import Mode

SPLIT: Final[int] = 438
ROW_H: Final[int] = 50
LARGE_ROW_H: Final[int] = 64
LARGE_SCALE: Final[float] = 1.25
LIST_TOP: Final[int] = 30
VEHICLE: Final[dict[Mode, str]] = {
    Mode.BUS: 'bus', Mode.TRAIN: 'train', Mode.METRO: 'metro',
    Mode.FERRY: 'ferry', Mode.LIGHT_RAIL: 'light rail', Mode.COACH: 'coach',
    Mode.REGIONAL: 'service', Mode.OTHER: 'service'}
LIST_LEFT: Final[int] = 456
LATER: Final[str] = 'LATER'
LIST_WIDTH: Final[int] = 140
LIST_PAIR_ROOM: Final[int] = 92  # clear of a '100 min' at LIST_LEAVE_RIGHT
LIST_LEAVE_RIGHT: Final[int] = 688
WIDEST_COUNT: Final[str] = '100 min'  # widest live count, Ruling 2
FASTER_LEFT: Final[int] = 712
PILL_LEFT: Final[int] = 596
TAGGED_WIDTH: Final[int] = FASTER_LEFT - LIST_LEFT - 16
BOARD_BADGE_X: Final[int] = 96
FASTER_WIDTH: Final[int] = 74
FASTER_GAP: Final[int] = 10
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
    tagged_headline(
        canvas,
        tag=f'{then.route} {then.headsign}',
        at=at,
        text=text,
        face=face,
        width=width,
    )


def tagged_headline(
    canvas: Canvas,
    *,
    tag: str,
    at: tuple[float, float],
    text: str,
    face: Face,
    width: float,
) -> None:
    """
    Draw a headsign, an arrow and the run-on tag, fitted together.

    Parameters
    ----------
    canvas : Canvas
        Target.
    tag : str
        The route and headsign the vehicle runs on as.
    at : tuple[float, float]
        Left end and baseline.
    text : str
        Headsign text.
    face : Face
        Headsign face.
    width : float
        Width for the headsign and tag together.
    """
    lead = ARROW_WIDTH + ARROW_GAP
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


@dataclass(frozen=True)
class Scale:
    """
    Scales a row's offsets and type sizes for larger text.

    Attributes
    ----------
    large : bool
        Whether larger text is on; if not, every value is unchanged.
    """

    large: bool = False

    def y(self, offset: int) -> int:
        """
        Scale a vertical offset within a row.

        Parameters
        ----------
        offset : int
            Pixels from the row top.

        Returns
        -------
        int
            The offset for this row height.
        """
        if not self.large:
            return offset
        return round(offset * LARGE_ROW_H / ROW_H)

    def face(self, *, weight: int, px: int) -> Face:
        """
        A face at the scaled size.

        Parameters
        ----------
        weight : int
            Font weight.
        px : int
            Size in pixels before scaling.

        Returns
        -------
        Face
            The face.
        """
        return Face(weight=weight, px=self.px(px))

    def px(self, size: int) -> int:
        """
        Scale a type size.

        Parameters
        ----------
        size : int
            Size in pixels before scaling.

        Returns
        -------
        int
            The size for this row height.
        """
        return round(size * LARGE_SCALE) if self.large else size


def faster_width(*, k: Scale) -> int:
    """
    Width of the board's FASTER pill.

    Parameters
    ----------
    k : Scale
        The row's scale.

    Returns
    -------
    int
        ``FASTER_WIDTH`` scaled with the row.
    """
    return round(FASTER_WIDTH * k.y(100) / 100)


def faster_left(*, k: Scale) -> int:
    """
    Left edge of the board's FASTER pill.

    The pill ends ``FASTER_GAP`` left of the widest live count.

    Parameters
    ----------
    k : Scale
        The row's scale.

    Returns
    -------
    int
        The pill's left x.
    """
    count = text_width(text=WIDEST_COUNT, face=k.face(weight=800, px=28))
    return round(786 - count - FASTER_GAP - faster_width(k=k))


def board_right(*, k: Scale) -> int:
    """
    Where a board row's headsign must end, 20 px before the FASTER pill.

    Parameters
    ----------
    k : Scale
        The row's scale.

    Returns
    -------
    int
        The headsign's right limit.
    """
    return faster_left(k=k) - 20


def list_row(
    canvas: Canvas, *, row: Row, y: int, large: bool = False
) -> None:
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
    large : bool
        Whether to draw larger text in a taller row.
    """
    k = Scale(large=large)
    route_pair(
        canvas,
        x=LIST_LEFT,
        y=y + k.y(14),
        route=row.route,
        onto=row.onto,
        px=k.px(14),
        room=LIST_PAIR_ROOM,
    )
    headline(
        canvas,
        row=row,
        at=(LIST_LEFT, y + k.y(42)),
        text=row.destination,
        face=k.face(weight=400, px=13),
        width=LIST_WIDTH if row.then is None else TAGGED_WIDTH,
    )
    list_times(canvas, row=row, y=y, k=k)
    canvas.rect(box=(452, y + k.y(49), 786, y + k.y(50)), grey=INK)


def cancelled_pill(canvas: Canvas, *, row: Row, y: int, k: Scale) -> None:
    """
    Draw a row's cancelled pill where its times would be.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure, cancelled.
    y : int
        Row top.
    k : Scale
        The row's scale.
    """
    pill(
        canvas,
        box=(PILL_LEFT, y + k.y(10), 786, y + k.y(38)),
        text=cancelled(row=row),
        px=k.px(15),
    )


def list_times(canvas: Canvas, *, row: Row, y: int, k: Scale) -> None:
    """
    Draw a list row's leave and arrive times, or its cancelled pill.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        Row top.
    k : Scale
        The row's scale.
    """
    if row.pill is Pill.CANCELLED:
        cancelled_pill(canvas, row=row, y=y, k=k)
    else:
        canvas.text(
            at=(LIST_LEAVE_RIGHT, y + k.y(30)), text=leaves(row=row),
            face=k.face(weight=800, px=28), grey=INK, anchor='rs')
        canvas.text(
            at=(786, y + k.y(24)), text=row.arrives_at,
            face=k.face(weight=800, px=20), grey=INK, anchor='rs')
    if row.pill is Pill.FASTER:
        pill(
            canvas,
            box=(FASTER_LEFT, y + k.y(30), 786, y + k.y(45)),
            text=FASTER,
            px=k.px(11),
        )


def board_row(
    canvas: Canvas, *, row: Row, y: int, large: bool = False
) -> None:
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
    large : bool
        Whether to draw larger text in a taller row.
    """
    k = Scale(large=large)
    canvas.text(
        at=(18, y + k.y(33)), text=row.leaves_at,
        face=k.face(weight=800, px=22), grey=INK)
    width = route_pair(
        canvas,
        x=BOARD_BADGE_X,
        y=y + k.y(25),
        route=row.route,
        onto=row.onto,
        px=k.px(16),
    )
    left = BOARD_BADGE_X + width + 14
    headline(
        canvas,
        row=row,
        at=(left, y + k.y(31)),
        text=f'to {row.destination}',
        face=k.face(weight=400, px=17),
        width=board_right(k=k) - left,
    )
    board_times(canvas, row=row, y=y, k=k)
    canvas.rect(box=(0, y + k.y(49), WIDTH, y + k.y(50)), grey=INK)


def board_times(canvas: Canvas, *, row: Row, y: int, k: Scale) -> None:
    """
    Draw a board row's countdown, or its cancelled pill.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        Row top.
    k : Scale
        The row's scale.
    """
    if row.pill is Pill.CANCELLED:
        cancelled_pill(canvas, row=row, y=y, k=k)
    else:
        canvas.text(
            at=(786, y + k.y(33)), text=countdown_slot(row=row),
            face=k.face(weight=800, px=28), grey=INK, anchor='rs')
    if row.pill is Pill.FASTER:
        left = faster_left(k=k)
        pill(
            canvas,
            box=(left, y + k.y(17), left + faster_width(k=k), y + k.y(32)),
            text=FASTER,
            px=k.px(11),
        )


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
    top = HEADER + (BANNER if view.banner else 0)
    view_banner(canvas, view=view)
    if view.empty:
        empty_message(canvas, text=view.empty)
    if view.hero:
        hero_split(canvas, row=view.hero, top=top)
    return finish_view(canvas, view=view, top=top)


def empty_message(canvas: Canvas, *, text: str) -> None:
    """
    Draw why the list is empty, centred.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The message.
    """
    face = Face(weight=700, px=22)
    message = canvas.fit(text=text, face=face, width=WIDTH - 64)
    canvas.text(
        at=(WIDTH / 2, 250), text=message, face=face, grey=INK,
        anchor='mm')


def hero_split(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw the hero, the rule beside it and the list's column heads.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        The hero departure.
    top : int
        Top of the content below the header and any banner.
    """
    hero(canvas, row=row, top=top)
    canvas.rect(box=(SPLIT, top, SPLIT + 3, HEIGHT), grey=INK)
    canvas.text(
        at=(LIST_LEFT, top + 22), text=LATER,
        face=Face(weight=700, px=11), grey=INK,
    )
    canvas.text(
        at=(LIST_LEAVE_RIGHT, top + 22), text='LEAVES',
        face=Face(weight=700, px=11),
        grey=INK, anchor='rs')
    canvas.text(
        at=(786, top + 22), text='ARRIVES',
        face=Face(weight=700, px=11), grey=INK, anchor='rs')


def finish_view(
    canvas: Canvas, *, view: StopView, top: int
) -> NDArray[np.uint8]:
    """
    Draw the departure rows, then threshold and invert the list if asked.

    Parameters
    ----------
    canvas : Canvas
        Canvas with the header, banner and hero drawn.
    view : StopView
        View-model.
    top : int
        Top of the content below the header and any banner.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    height = LARGE_ROW_H if view.large else ROW_H
    board = view.destination is None
    first = top if board else top + LIST_TOP
    for index, item in enumerate(view.rows[:(HEIGHT - first) // height]):
        draw = board_row if board else list_row
        draw(canvas, row=item, y=first + index * height, large=view.large)
    frame = finish(canvas)
    if not view.inverted:
        return frame
    left = SPLIT + 3 if view.hero else 0
    return inverted_area(frame=frame, box=(left, top, WIDTH, HEIGHT))


def inverted_area(
    *, frame: NDArray[np.uint8], box: tuple[int, int, int, int]
) -> NDArray[np.uint8]:
    """
    Turn the pixels inside a box white on black.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A 1-bit frame.
    box : tuple[int, int, int, int]
        Left, top, right and bottom of the area to invert.

    Returns
    -------
    NDArray[np.uint8]
        A copy of the frame with the box inverted.
    """
    left, top, right, bottom = box
    turned = frame.copy()
    turned[top:bottom, left:right] = 255 - frame[top:bottom, left:right]
    return turned
