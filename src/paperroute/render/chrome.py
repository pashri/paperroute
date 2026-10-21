"""Header, banner and route badge shared by every view."""

import math
from typing import Final, Protocol

from paperroute.core.stop_view import DOT, PinTag, Row
from paperroute.core.strip_model import PICK_FROM
from paperroute.render.canvas import INK, PAPER, WIDTH, Box, Canvas, Face

HEADER: Final[int] = 40
ARROW_WIDTH: Final[float] = 22.0
ARROW_GAP: Final[int] = 4
BANNER: Final[int] = 34
FASTER: Final[str] = 'FASTER'
TAG_GAP: Final[int] = 8
TABS: Final[tuple[str, ...]] = ('Stop', 'Map', 'Strip', 'Strip + map')
ONE_TITLE_PICKS: Final[frozenset[str]] = frozenset({PICK_FROM})


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
    return ARROW_WIDTH


def header_tabs(canvas: Canvas, *, clock: str, active: str) -> float:
    """
    Draw the clock and the tabs on the right of the header.

    Parameters
    ----------
    canvas : Canvas
        Target.
    clock : str
        Clock text.
    active : str
        Name of the highlighted tab.

    Returns
    -------
    float
        Left edge of the tabs.
    """
    big = Face(weight=800, px=22)
    right = WIDTH - 12 - canvas.text(
        at=(WIDTH - 12, 20), text=clock, face=big, grey=PAPER,
        anchor='rm')
    for tab in reversed(TABS):
        width = canvas.length(text=tab, face=Face(weight=700, px=12)) + 10
        right -= width + 4
        if tab == active:
            canvas.rect(box=(right, 11, right + width, 30), grey=PAPER)
        canvas.text(
            at=(right + 5, 25), text=tab, face=Face(weight=700, px=12),
            grey=INK if tab == active else PAPER)
    return right


def pin_tag(canvas: Canvas, *, right: float, tag: PinTag) -> float:
    """
    Draw the pin tag 8 px left of the tabs.

    A pinned tag is a paper box with ink text; an unpinned one is a
    dashed paper outline with paper text.

    Parameters
    ----------
    canvas : Canvas
        Target.
    right : float
        Left edge of the tabs.
    tag : PinTag
        Text and whether the place is pinned.

    Returns
    -------
    float
        Left edge of the tag.
    """
    face = Face(weight=700, px=12)
    width = canvas.length(text=tag.text, face=face) + 10
    left = right - TAG_GAP - width
    if tag.pinned:
        canvas.rect(box=(left, 11, left + width, 30), grey=PAPER)
    else:
        canvas.line(
            points=[
                (left, 11.5),
                (left + width, 11.5),
                (left + width, 29.5),
                (left, 29.5),
                (left, 11.5),
            ],
            width=1,
            grey=PAPER,
            dash=(3, 2),
        )
    canvas.text(
        at=(left + 5, 25),
        text=tag.text,
        face=face,
        grey=INK if tag.pinned else PAPER,
    )
    return left


def header_titles(
    canvas: Canvas,
    *,
    origin: str,
    destination: str | None,
    right: float,
    picking: str | None = None,
) -> None:
    """
    Draw the origin and, when there is one, an arrow and a second title.

    The second title is 'pick <picking>' while picking, else the
    destination. With neither, only the origin is drawn. Picking a From
    reads as one title, '<origin> · pick FROM', as the origin is then the
    route being picked on.

    Parameters
    ----------
    canvas : Canvas
        Target.
    origin : str
        Origin name.
    destination : str | None
        Destination name, or None when the place has none.
    right : float
        Left edge of the pin tag, which the titles must stay 8 px short of.
    picking : str | None
        What is being picked, such as 'To'.
    """
    title = Face(weight=700, px=18)
    second = destination if picking is None else f'pick {picking}'
    if picking in ONE_TITLE_PICKS:
        origin, second = f'{origin}{DOT}{second}', None
    limit = right - TAG_GAP
    if second is None:
        shown = canvas.fit(text=origin, face=title, width=limit - 12)
        canvas.text(at=(12, 27), text=shown, face=title, grey=PAPER)
        return
    half = (limit - 60) / 2
    shown = canvas.fit(text=origin, face=title, width=half)
    x = 12 + canvas.text(
        at=(12, 27), text=shown, face=title, grey=PAPER) + 8
    x += arrow(canvas, x=x, y=20, grey=PAPER)
    name = canvas.fit(text=second, face=title, width=half)
    canvas.text(at=(x, 27), text=name, face=title, grey=PAPER)


def header(
    canvas: Canvas,
    *,
    origin: str,
    destination: str | None,
    clock: str,
    active: str,
    tag: PinTag,
    picking: str | None = None,
) -> None:
    """
    Draw the black header: titles, pin tag, tabs, clock.

    Parameters
    ----------
    canvas : Canvas
        Target.
    origin : str
        Origin name.
    destination : str | None
        Destination name, or None when the place has none.
    clock : str
        Clock text.
    active : str
        Name of the highlighted tab.
    tag : PinTag
        The pin tag.
    picking : str | None
        What is being picked; the title reads '<origin> -> pick <it>'.
    """
    canvas.rect(box=(0, 0, WIDTH, HEADER), grey=INK)
    right = header_tabs(canvas, clock=clock, active=active)
    left = pin_tag(canvas, right=right, tag=tag)
    header_titles(
        canvas,
        origin=origin,
        destination=destination,
        right=left,
        picking=picking,
    )


class Titled(Protocol):
    """A view with the texts the header shows."""

    @property
    def origin(self) -> str:
        """The From stop's name."""

    @property
    def destination(self) -> str | None:
        """The destination's name, or None when the place has none."""

    @property
    def tag(self) -> PinTag:
        """The pin tag."""

    @property
    def clock(self) -> str:
        """The clock text."""


def view_header(
    canvas: Canvas,
    *,
    view: Titled,
    active: str,
    picking: str | None = None,
) -> None:
    """
    Draw the header for a view.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : Titled
        The view whose origin, destination and clock are shown.
    active : str
        Name of the highlighted tab.
    picking : str | None
        What is being picked; the title reads '<origin> -> pick <it>'.
    """
    header(
        canvas,
        origin=view.origin,
        destination=view.destination,
        clock=view.clock,
        active=active,
        tag=view.tag,
        picking=picking,
    )


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
    route = canvas.fit(text=route, face=face, width=px * 4)
    width = max(canvas.length(text=route, face=face) + 12, px * 2.4)
    canvas.rect(box=(x, y - px * 0.65, x + width, y + px * 0.65), grey=INK)
    canvas.text(
        at=(x + width / 2, y + 1), text=route, face=face, grey=PAPER,
        anchor='mm')
    return width


def route_pair(
    canvas: Canvas,
    *,
    x: float,
    y: float,
    route: str,
    onto: str | None,
    px: int,
) -> float:
    """
    Draw a route badge, and for a carry-on an arrow and the onto badge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left edge.
    y : float
        Vertical centre.
    route : str
        Route label of the boarded trip.
    onto : str | None
        Route label of the trip it becomes, or None.
    px : int
        Text size.

    Returns
    -------
    float
        Total width.
    """
    width = badge(canvas, x=x, y=y, route=route, px=px)
    if onto is None:
        return width
    width += ARROW_GAP
    width += arrow(canvas, x=x + width, y=y, grey=INK) + ARROW_GAP
    return width + badge(canvas, x=x + width, y=y, route=onto, px=px)


def pill(canvas: Canvas, *, box: Box, text: str, px: int) -> None:
    """
    Draw an ink block with bold paper text centred in it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    box : Box
        Left, top, right, bottom.
    text : str
        Pill text.
    px : int
        Text size.
    """
    canvas.rect(box=box, grey=INK)
    canvas.text(
        at=((box[0] + box[2]) / 2, math.ceil((box[1] + box[3]) / 2)),
        text=text,
        face=Face(weight=700, px=px),
        grey=PAPER,
        anchor='mm',
    )


def with_minutes(*, text: str) -> str:
    """
    Name the unit of a bare count of minutes.

    Parameters
    ----------
    text : str
        Countdown text.

    Returns
    -------
    str
        '<n> min' for a count; 'now' and clock times as they are.
    """
    return f'{text} min' if text.isdigit() else text


def leaves(*, row: Row) -> str:
    """
    Say when a departure leaves.

    Parameters
    ----------
    row : Row
        Departure.

    Returns
    -------
    str
        The countdown when live ('<n> min' for a count of minutes), else
        the timetabled clock time.
    """
    return with_minutes(text=row.countdown) if row.live else row.leaves_at


def countdown_slot(*, row: Row) -> str:
    """
    Say what goes in a countdown slot beside the departure's clock time.

    Parameters
    ----------
    row : Row
        Departure.

    Returns
    -------
    str
        The countdown ('<n> min' for a count of minutes), or '' for a far
        departure, whose countdown is the clock time the row already
        shows.
    """
    return '' if row.far else with_minutes(text=row.countdown)


def cancelled(*, row: Row) -> str:
    """
    Label a cancelled departure.

    Parameters
    ----------
    row : Row
        Departure.

    Returns
    -------
    str
        The timetabled time, then CANCELLED.
    """
    return f'{row.leaves_at}  CANCELLED'
