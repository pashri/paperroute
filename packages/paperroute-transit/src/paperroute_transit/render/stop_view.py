"""Draw the Stop view in the round 5 layout."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

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
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.stop_under import NextCall, UnderNumber
from paperroute_transit.core.stop_view import (
    HORIZON_TEXT,
    UNCERTAIN,
    Pill,
    Row,
    StopView,
    due_line,
    stops_text,
)
from paperroute_transit.render.chrome import (
    ARROW_GAP,
    ARROW_WIDTH,
    BANNER,
    FASTER,
    NAME_GAP,
    arrow,
    cancelled,
    leaves,
    note_room,
    pill,
    route_pair,
    uncertain_row,
    view_banner,
    view_header,
)
from paperroute_transit.render.late_marks import mark_room, marked_countdown
from paperroute_transit.render.names import Naming, fit_headsign, fit_name

SPLIT: Final[int] = 438
ROW_H: Final[int] = 50
LARGE_ROW_H: Final[int] = 64
LARGE_SCALE: Final[float] = 1.25
LIST_TOP: Final[int] = 30
LIST_LEFT: Final[int] = 456
LATER: Final[str] = 'LATER'
LIST_WIDTH: Final[int] = 140
LIST_PAIR_ROOM: Final[int] = 92  # clear of a '100 min' at LIST_LEAVE_RIGHT
LIST_LEAVE_RIGHT: Final[int] = 688
WIDEST_COUNT: Final[str] = '100 min'  # widest live count, Ruling 2
MARK_PX: Final[int] = 14  # size of the lateness mark
FASTER_LEFT: Final[int] = 712
PILL_LEFT: Final[int] = 596
TAGGED_WIDTH: Final[int] = FASTER_LEFT - LIST_LEFT - 16
TAG_FACE: Final[Face] = Face(weight=700, px=12)
TAG_GAP: Final[int] = 8
TAG_FLOOR: Final[int] = 40
DUE_FACE: Final[Face] = Face(weight=700, px=15)
DUE_DROP: Final[int] = 264  # due line baseline below the content top
NOTE_DROP: Final[int] = 44  # 'uncertain' baseline below a row's top
NOTE_PX: Final[int] = 11
STATUS_FACE: Final[Face] = Face(weight=700, px=24)
SMALL_FACE: Final[Face] = Face(weight=400, px=15)
CALLS_HEAD: Final[str] = 'CALLS NEXT AT'
CALLS_HEAD_FACE: Final[Face] = Face(weight=700, px=11)
CALL_TIME_FACE: Final[Face] = Face(weight=700, px=15)
UNDER_DROPS: Final[tuple[int, int]] = (216, 240)  # status, small line
CALLS_HEAD_DROP: Final[int] = 270  # baseline below the content top
CALLS_DROP: Final[int] = 290  # first call's baseline below the top
CALL_STEP: Final[int] = 24  # between calls' baselines
CALL_TIME_X: Final[int] = 46
CALL_NAME_X: Final[int] = 96
CALL_NAME_WIDTH: Final[int] = 320
HERO_FACE: Final[Face] = Face(weight=700, px=18)
HERO_DROP: Final[int] = 57  # baseline below the top
HERO_DROPS: Final[tuple[int, int]] = (50, 71)  # two-line baselines
OPEN_RIGHT: Final[int] = 786  # right edge of an open row's times


def hero_headline(
    canvas: Canvas, *, row: Row, top: int, naming: Naming
) -> None:
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
    naming : Naming
        How names are shortened.
    """
    canvas.text(
        at=(18, top + 24), text=f'NEXT {row.vehicle.upper()}',
        face=Face(weight=700, px=12), grey=INK)
    width = route_pair(
        canvas, x=18, y=top + 50, route=row.route, onto=row.onto, px=20,
    )
    lines = fit_headsign(
        text=row.destination,
        face=HERO_FACE,
        width=SPLIT - 50 - width,
        naming=naming,
        two_lines=True,
    )
    drops = HERO_DROPS if len(lines) == 2 else (HERO_DROP,)
    for line, drop in zip(lines, drops):
        canvas.text(
            at=(30 + width, top + drop), text=line, face=HERO_FACE,
            grey=INK)


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
    where = f'{DOT}{row.bay} {row.platform}' if row.platform else ''
    ride = Face(weight=400, px=15)
    count = (
        '' if row.stops is None else f'{DOT}{stops_text(stops=row.stops)}'
    )
    text = (
        f'{row.ride_minutes} min on the {row.vehicle}{where}{count}'
    )
    canvas.text(
        at=(18, top + 240), text=canvas.fit(text=text, face=ride, width=400),
        face=ride, grey=INK)


def hero_due(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw the due line under the ride line, for an estimated row.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Next service.
    top : int
        Top of the panel.
    """
    if row.estimate is None:
        return
    text = due_line(estimate=row.estimate)
    canvas.text(
        at=(18, top + DUE_DROP),
        text=canvas.fit(text=text, face=DUE_FACE, width=400),
        face=DUE_FACE,
        grey=INK,
    )


def note_text(*, row: Row) -> str:
    """
    Say what a row's note reads.

    Parameters
    ----------
    row : Row
        Departure.

    Returns
    -------
    str
        'uncertain' for an uncertain row, else the row's own note.
    """
    return UNCERTAIN if uncertain_row(row=row) else row.note


def row_note(
    canvas: Canvas, *, row: Row, right: float, y: int, k: Scale
) -> None:
    """
    Draw a row's note under its time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    right : float
        Right end of the note.
    y : int
        Row top.
    k : Scale
        The row's scale.
    """
    text = note_text(row=row)
    if not text:
        return
    canvas.text(
        at=(right, y + k.y(NOTE_DROP)), text=text,
        face=k.face(weight=700, px=NOTE_PX), grey=INK, anchor='rs')


def headline_width(*, row: Row, k: Scale) -> float:
    """
    Say how wide a list row's headsign and tag may be together.

    Parameters
    ----------
    row : Row
        Departure.
    k : Scale
        The row's scale.

    Returns
    -------
    float
        ``LIST_WIDTH`` without a run-on tag; with one, ``TAGGED_WIDTH``,
        or less for a row with a note so the headline clears it.
    """
    widest = LIST_WIDTH if row.then is None else TAGGED_WIDTH
    text = note_text(row=row)
    if not text:
        return widest
    room = note_room(face=k.face(weight=700, px=NOTE_PX), text=text)
    return min(widest, LIST_LEAVE_RIGHT - LIST_LEFT - room)


def small_parts(*, under: UnderNumber, row: Row) -> tuple[str, ...]:
    """
    List the parts of the small line under the status.

    Parameters
    ----------
    under : UnderNumber
        The block under the number.
    row : Row
        The hero departure.

    Returns
    -------
    tuple[str, ...]
        The place, platform and next departures, each when not empty;
        else a line saying there are no other services.
    """
    parts = tuple(
        part
        for part in (
            under.place,
            under.platform,
            f'then {", ".join(under.then)}' if under.then else '',
        )
        if part
    )
    if parts:
        return parts
    return (
        f'no other {row.route} to {row.destination} in {HORIZON_TEXT}',
    )


def small_text(*, parts: Sequence[str], face: Face, width: float) -> str:
    """
    Join the most leading parts that fit, always keeping the first.

    Parameters
    ----------
    parts : Sequence[str]
        The parts, most important first.
    face : Face
        The face the line is drawn in.
    width : float
        Room for the line.

    Returns
    -------
    str
        The parts joined with a dot.
    """
    shown = parts[0]
    for part in parts[1:]:
        more = f'{shown}{DOT}{part}'
        if text_width(text=more, face=face) > width:
            break
        shown = more
    return shown


def call_row(
    canvas: Canvas,
    *,
    calls: Sequence[NextCall],
    index: int,
    top: int,
    naming: Naming,
) -> None:
    """
    Draw one call: its dot, the link to the next call, time and name.

    Parameters
    ----------
    canvas : Canvas
        Target.
    calls : Sequence[NextCall]
        All the calls.
    index : int
        Which call to draw.
    top : int
        Top of the content.
    naming : Naming
        How names are shortened.
    """
    y = top + CALLS_DROP + index * CALL_STEP
    canvas.ellipse(box=(25, y - 10, 35, y), grey=INK)
    if index < len(calls) - 1:
        canvas.rect(box=(29, y, 31, y + 18), grey=INK)
    call = calls[index]
    canvas.text(
        at=(CALL_TIME_X, y), text=call.at, face=CALL_TIME_FACE, grey=INK)
    canvas.text(
        at=(CALL_NAME_X, y),
        text=fit_name(
            text=call.name,
            face=SMALL_FACE,
            width=CALL_NAME_WIDTH,
            naming=naming,
        )[0],
        face=SMALL_FACE,
        grey=INK,
    )


def hero_under(
    canvas: Canvas,
    *,
    under: UnderNumber,
    row: Row,
    top: int,
    rules: NameRules,
) -> None:
    """
    Draw the block under the hero's number.

    Parameters
    ----------
    canvas : Canvas
        Target.
    under : UnderNumber
        The block under the number.
    row : Row
        The hero departure.
    top : int
        Top of the content.
    rules : NameRules
        Word lists and options for shortening call names.
    """
    status, small = UNDER_DROPS
    canvas.rect(box=(18, top + 184, 418, top + 186), grey=INK)
    canvas.text(
        at=(18, top + status),
        text=canvas.fit(text=under.status, face=STATUS_FACE, width=400),
        face=STATUS_FACE,
        grey=INK,
    )
    line = small_text(
        parts=small_parts(under=under, row=row), face=SMALL_FACE, width=400)
    canvas.text(
        at=(18, top + small),
        text=canvas.fit(text=line, face=SMALL_FACE, width=400),
        face=SMALL_FACE,
        grey=INK,
    )
    if not under.calls:
        return
    canvas.text(
        at=(18, top + CALLS_HEAD_DROP), text=CALLS_HEAD,
        face=CALLS_HEAD_FACE, grey=INK)
    naming = Naming(
        rules=rules, siblings=tuple(call.name for call in under.calls),
    )
    for index in range(len(under.calls)):
        call_row(
            canvas, calls=under.calls, index=index, top=top, naming=naming,
        )


def hero(
    canvas: Canvas,
    *,
    row: Row,
    top: int,
    naming: Naming,
    under: UnderNumber | None = None,
) -> None:
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
    naming : Naming
        How names are shortened.
    under : UnderNumber | None
        The block to draw under the number, in place of the arrival
        footer and due line.
    """
    hero_headline(canvas, row=row, top=top, naming=naming)
    hero_countdown(canvas, row=row, top=top)
    if under is not None:
        hero_under(
            canvas, under=under, row=row, top=top, rules=naming.rules,
        )
        return
    hero_footer(canvas, row=row, top=top)
    hero_due(canvas, row=row, top=top)


def headline(
    canvas: Canvas,
    *,
    row: Row,
    at: tuple[float, float],
    text: str,
    face: Face,
    width: float,
    naming: Naming,
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
    naming : Naming
        How names are shortened.
    """
    then = row.then
    if then is None:
        fitted = fit_headsign(
            text=text, face=face, width=width, naming=naming,
        )[0]
        canvas.text(at=at, text=fitted, face=face, grey=INK)
        return
    tagged_headline(
        canvas,
        tag=f'{then.route} {then.headsign}',
        at=at,
        text=text,
        face=face,
        width=width,
        naming=naming,
    )


def tagged_headline(
    canvas: Canvas,
    *,
    tag: str,
    at: tuple[float, float],
    text: str,
    face: Face,
    width: float,
    naming: Naming,
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
    naming : Naming
        How names are shortened.
    """
    lead = ARROW_WIDTH + ARROW_GAP
    room = width - TAG_GAP - canvas.length(text=text, face=face)
    tag_width = min(
        canvas.length(text=tag, face=TAG_FACE) + lead,
        max(room, lead + TAG_FLOOR),
    )
    fitted = fit_headsign(
        text=text,
        face=face,
        width=width - tag_width - TAG_GAP,
        naming=naming,
    )[0]
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


def open_width(*, row: Row, k: Scale) -> float:
    """
    Say how wide an open row's name may be, from `LIST_LEFT`.

    Parameters
    ----------
    row : Row
        Departure.
    k : Scale
        The row's scale.

    Returns
    -------
    float
        The room up to the cancelled pill, the note, or the right edge.
    """
    if row.pill is Pill.CANCELLED:
        return PILL_LEFT - NAME_GAP - LIST_LEFT
    text = note_text(row=row)
    if not text:
        return OPEN_RIGHT - LIST_LEFT
    room = note_room(face=k.face(weight=700, px=NOTE_PX), text=text)
    return OPEN_RIGHT - room - LIST_LEFT


def open_times(canvas: Canvas, *, row: Row, y: int, k: Scale) -> None:
    """
    Draw an open row's cancelled pill, or its leave time and note.

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
        return
    marked_countdown(
        canvas, row=row, text=leaves(row=row),
        at=(OPEN_RIGHT, y + k.y(30)), face=k.face(weight=800, px=28),
        mark_face=k.face(weight=700, px=MARK_PX))
    row_note(canvas, row=row, right=OPEN_RIGHT, y=y, k=k)


def list_row(
    canvas: Canvas,
    *,
    row: Row,
    y: int,
    naming: Naming,
    large: bool = False,
    arrives: bool = True,
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
    naming : Naming
        How names are shortened.
    large : bool
        Whether to draw larger text in a taller row.
    arrives : bool
        False draws the open layout: no arrival column, no run-on tag,
        and the name runs to the time.
    """
    k = Scale(large=large)
    route_pair(
        canvas,
        x=LIST_LEFT,
        y=y + k.y(14),
        route=row.route,
        onto=row.onto,
        px=k.px(14),
        room=LIST_PAIR_ROOM
        - mark_room(row=row, face=k.face(weight=700, px=MARK_PX)),
    )
    face = k.face(weight=400, px=13)
    at = (LIST_LEFT, y + k.y(42))
    if arrives:
        headline(
            canvas, row=row, at=at, text=row.destination, face=face,
            width=headline_width(row=row, k=k), naming=naming)
        list_times(canvas, row=row, y=y, k=k)
    else:
        canvas.text(
            at=at,
            text=fit_headsign(
                text=row.destination,
                face=face,
                width=open_width(row=row, k=k),
                naming=naming,
            )[0],
            face=face,
            grey=INK,
        )
        open_times(canvas, row=row, y=y, k=k)
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
        marked_countdown(
            canvas, row=row, text=leaves(row=row),
            at=(LIST_LEAVE_RIGHT, y + k.y(30)),
            face=k.face(weight=800, px=28),
            mark_face=k.face(weight=700, px=MARK_PX))
        canvas.text(
            at=(786, y + k.y(24)), text=row.arrives_at,
            face=k.face(weight=800, px=20), grey=INK, anchor='rs')
        row_note(canvas, row=row, right=LIST_LEAVE_RIGHT, y=y, k=k)
    if row.pill is Pill.FASTER:
        pill(
            canvas,
            box=(FASTER_LEFT, y + k.y(30), 786, y + k.y(45)),
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
    names = [item.destination for item in view.rows]
    if view.hero:
        names.append(view.hero.destination)
    naming = Naming(rules=view.name_rules, siblings=tuple(names))
    if view.hero:
        hero_split(
            canvas, row=view.hero, top=top, under=view.under, naming=naming,
        )
    return finish_view(canvas, view=view, top=top, naming=naming)


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


def hero_split(
    canvas: Canvas,
    *,
    row: Row,
    top: int,
    under: UnderNumber | None,
    naming: Naming,
) -> None:
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
    under : UnderNumber | None
        The block under the hero's number; when given, the list is
        headed LEAVES alone, at the right edge.
    naming : Naming
        How names are shortened.
    """
    hero(canvas, row=row, top=top, under=under, naming=naming)
    canvas.rect(box=(SPLIT, top, SPLIT + 3, HEIGHT), grey=INK)
    head = Face(weight=700, px=11)
    if under is not None:
        canvas.text(
            at=(OPEN_RIGHT, top + 22), text='LEAVES', face=head,
            grey=INK, anchor='rs')
        return
    canvas.text(
        at=(LIST_LEFT, top + 22), text=LATER, face=head, grey=INK)
    canvas.text(
        at=(LIST_LEAVE_RIGHT, top + 22), text='LEAVES', face=head,
        grey=INK, anchor='rs')
    canvas.text(
        at=(OPEN_RIGHT, top + 22), text='ARRIVES', face=head, grey=INK,
        anchor='rs')


def finish_view(
    canvas: Canvas, *, view: StopView, top: int, naming: Naming
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
    naming : Naming
        How names are shortened.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    height = LARGE_ROW_H if view.large else ROW_H
    first = top + LIST_TOP
    for index, item in enumerate(view.rows[:(HEIGHT - first) // height]):
        list_row(
            canvas,
            row=item,
            y=first + index * height,
            naming=naming,
            large=view.large,
            arrives=view.destination is not None,
        )
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
