"""Header, banner and route badge shared by every view."""

import math
from typing import Final, Protocol

from paperroute.core.stop_view import DOT, PinTag, Row
from paperroute.core.strip_model import PICK_FROM
from paperroute.render.canvas import (
    INK,
    PAPER,
    WIDTH,
    Box,
    Canvas,
    Face,
    text_width,
)

HEADER: Final[int] = 40
ARROW_WIDTH: Final[float] = 22.0
ARROW_GAP: Final[int] = 4
MIN_PAIR_PX: Final[int] = 9
BANNER: Final[int] = 34
WARNING_SIDE: Final[int] = 18  # warning triangle side, in pixels
FASTER: Final[str] = 'FASTER'
TAG_GAP: Final[int] = 8
TABS: Final[tuple[str, ...]] = ('Stop', 'Map', 'Strip', 'Strip + map')
ALERT_TABS: Final[tuple[str, ...]] = (*TABS, 'Alerts')
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


def header_tabs(
    canvas: Canvas,
    *,
    clock: str,
    active: str,
    tabs: tuple[str, ...] = TABS,
) -> float:
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
    tabs : tuple[str, ...]
        Tab names, left to right.

    Returns
    -------
    float
        Left edge of the tabs.
    """
    big = Face(weight=800, px=22)
    right = WIDTH - 12 - canvas.text(
        at=(WIDTH - 12, 20), text=clock, face=big, grey=PAPER,
        anchor='rm')
    for tab in reversed(tabs):
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

    A pinned tag is a paper box with ink text; an auto one is an ink box
    with a paper outline and paper text; an unpinned one is a dashed
    paper outline with paper text.

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
    boxed = tag.pinned and not tag.auto
    if boxed:
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
            dash=None if tag.auto else (3, 2),
        )
    canvas.text(
        at=(left + 5, 25),
        text=tag.text,
        face=face,
        grey=INK if boxed else PAPER,
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
    two_titles(canvas, first=origin, second=second, limit=limit)


def two_titles(
    canvas: Canvas, *, first: str, second: str, limit: float,
) -> None:
    """
    Draw two header titles with an arrow between, each fitted to half.

    Parameters
    ----------
    canvas : Canvas
        Target.
    first : str
        The left title.
    second : str
        The right title.
    limit : float
        Where the titles must end.
    """
    title = Face(weight=700, px=18)
    half = (limit - 60) / 2
    shown = canvas.fit(text=first, face=title, width=half)
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
    tabs: tuple[str, ...] = TABS,
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
    tabs : tuple[str, ...]
        Tab names, left to right.
    """
    canvas.rect(box=(0, 0, WIDTH, HEADER), grey=INK)
    right = header_tabs(canvas, clock=clock, active=active, tabs=tabs)
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
    tabs: tuple[str, ...] = TABS,
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
    tabs : tuple[str, ...]
        Tab names, left to right.
    """
    header(
        canvas,
        origin=view.origin,
        destination=view.destination,
        clock=view.clock,
        active=active,
        tag=view.tag,
        picking=picking,
        tabs=tabs,
    )


def banner(
    canvas: Canvas,
    *,
    text: str,
    warning: bool = False,
    more: int = 0,
    planned: bool = False,
) -> None:
    """
    Draw the banner under the header.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        Banner text.
    warning : bool
        Draw a device warning or active alert: a solid ink band with
        paper text after a paper warning triangle. Otherwise a bordered
        paper band.
    more : int
        Other alerts; when positive a `+N more` pill sits at the right
        end.
    planned : bool
        Put an outlined warning triangle before the text of a bordered
        band.
    """
    face = Face(weight=700, px=15)
    banner_band(canvas, warning=warning, planned=planned)
    left = 44 if warning or planned else 16
    right: float = WIDTH - 16
    if more > 0:
        right = more_pill(canvas, more=more, inverted=warning) - 8
    fitted = canvas.fit(text=text, face=face, width=right - left)
    canvas.text(
        at=(left, HEADER + 23),
        text=fitted,
        face=face,
        grey=PAPER if warning else INK,
    )


def banner_band(canvas: Canvas, *, warning: bool, planned: bool) -> None:
    """
    Draw the banner's band and its triangle, without the text.

    Parameters
    ----------
    canvas : Canvas
        Target.
    warning : bool
        Draw a solid ink band with a paper triangle; otherwise a paper
        band inside an ink border.
    planned : bool
        Put an outlined triangle on a bordered band.
    """
    canvas.rect(box=(0, HEADER, WIDTH, HEADER + BANNER), grey=INK)
    if warning:
        warning_triangle(canvas, x=16, y=HEADER + BANNER / 2, grey=PAPER)
        return
    canvas.rect(
        box=(3, HEADER + 3, WIDTH - 3, HEADER + BANNER - 3), grey=PAPER,
    )
    if planned:
        outlined_triangle(canvas, x=16, y=HEADER + BANNER / 2)


class Bannered(Protocol):
    """A view with the fields the banner shows."""

    @property
    def banner(self) -> str | None:
        """The banner text, or None."""

    @property
    def warning(self) -> bool:
        """Whether the banner is inverted."""

    @property
    def more(self) -> int:
        """Other alerts behind the banner."""

    @property
    def planned(self) -> bool:
        """Whether the banner is a coming alert."""


def view_banner(canvas: Canvas, *, view: Bannered) -> None:
    """
    Draw a view's banner, if it has one.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : Bannered
        View-model.
    """
    if view.banner:
        banner(
            canvas,
            text=view.banner,
            warning=view.warning,
            more=view.more,
            planned=view.planned,
        )


def more_pill(canvas: Canvas, *, more: int, inverted: bool) -> float:
    """
    Draw the `+N more` pill at the banner's right end.

    Parameters
    ----------
    canvas : Canvas
        Target.
    more : int
        How many other alerts.
    inverted : bool
        True on the ink band: paper pill, ink text. Otherwise an ink
        pill with paper text.

    Returns
    -------
    float
        The pill's left edge.
    """
    face = Face(weight=700, px=12)
    label = f'+{more} more'
    width = text_width(text=label, face=face) + 12
    left = WIDTH - 10 - width
    middle = HEADER + BANNER / 2
    canvas.rect(
        box=(left, middle - 9, WIDTH - 10, middle + 9),
        grey=PAPER if inverted else INK,
    )
    canvas.text(
        at=(left + width / 2, middle + 1),
        text=label,
        face=face,
        grey=INK if inverted else PAPER,
        anchor='mm',
    )
    return left


def outlined_triangle(canvas: Canvas, *, x: float, y: float) -> None:
    """
    Draw a warning triangle as an outline with a small mark inside.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left edge.
    y : float
        Vertical centre.
    """
    side = WARNING_SIDE
    height = side * 3 ** 0.5 / 2
    bottom = y + height / 2
    middle = x + side / 2
    canvas.polygon(
        points=[(x, bottom), (middle, y - height / 2), (x + side, bottom)],
        grey=INK,
    )
    inner = side - 9
    inner_height = inner * 3 ** 0.5 / 2
    hollow(canvas, middle=middle, bottom=bottom - 2.5, side=inner)
    exclamation(
        canvas, middle=middle, top=bottom - 2.5 - inner_height,
        bottom=bottom - 2.5,
    )


def hollow(
    canvas: Canvas, *, middle: float, bottom: float, side: float,
) -> None:
    """
    Fill an upright triangle with paper.

    Parameters
    ----------
    canvas : Canvas
        Target.
    middle : float
        Its centre line.
    bottom : float
        Its base.
    side : float
        Its side length.
    """
    height = side * 3 ** 0.5 / 2
    canvas.polygon(
        points=[
            (middle - side / 2, bottom),
            (middle, bottom - height),
            (middle + side / 2, bottom),
        ],
        grey=PAPER,
    )


def exclamation(
    canvas: Canvas, *, middle: float, top: float, bottom: float
) -> None:
    """
    Draw a small ink exclamation mark inside a triangle.

    Parameters
    ----------
    canvas : Canvas
        Target.
    middle : float
        Horizontal centre.
    top : float
        Top of the triangle's inside.
    bottom : float
        Bottom of the triangle's inside.
    """
    canvas.rect(box=(middle - 1, top + 3, middle + 1, bottom - 4), grey=INK)
    canvas.rect(
        box=(middle - 1, bottom - 2.5, middle + 1, bottom - 1), grey=INK,
    )


def warning_triangle(
    canvas: Canvas, *, x: float, y: float, grey: int
) -> None:
    """
    Draw a warning triangle with a cut-out exclamation mark.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left edge.
    y : float
        Vertical centre.
    grey : int
        Triangle grey; the mark is cut out in ink.
    """
    side = WARNING_SIDE
    height = side * 3 ** 0.5 / 2
    top, bottom = y - height / 2, y + height / 2
    canvas.polygon(
        points=[(x, bottom), (x + side / 2, top), (x + side, bottom)],
        grey=grey,
    )
    middle = x + side / 2
    canvas.rect(box=(middle - 1, top + 5, middle + 1, bottom - 5), grey=INK)
    canvas.rect(box=(middle - 1, bottom - 3, middle + 1, bottom - 1), grey=INK)


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
    width = badge_width(canvas, route=route, px=px)
    canvas.rect(box=(x, y - px * 0.65, x + width, y + px * 0.65), grey=INK)
    canvas.text(
        at=(x + width / 2, y + 1), text=route, face=face, grey=PAPER,
        anchor='mm')
    return width


def badge_width(canvas: Canvas, *, route: str, px: int) -> float:
    """
    Measure a route badge.

    Parameters
    ----------
    canvas : Canvas
        Target.
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
    return max(canvas.length(text=route, face=face) + 12, px * 2.4)


def pair_px(
    canvas: Canvas, *, route: str, onto: str, px: int, room: float,
) -> int:
    """
    Choose the largest text size, up to px, at which a pair fits a room.

    Parameters
    ----------
    canvas : Canvas
        Target.
    route : str
        Route label of the boarded trip.
    onto : str
        Route label of the trip it becomes.
    px : int
        Largest text size.
    room : float
        Width available.

    Returns
    -------
    int
        Text size, never below MIN_PAIR_PX.
    """
    while px > MIN_PAIR_PX:
        width = 2 * ARROW_GAP + ARROW_WIDTH + sum(
            badge_width(canvas, route=label, px=px)
            for label in (route, onto)
        )
        if width <= room:
            break
        px -= 1
    return px


def route_pair(
    canvas: Canvas,
    *,
    x: float,
    y: float,
    route: str,
    onto: str | None,
    px: int,
    room: float | None = None,
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
    room : float | None
        Width the pair must fit; the text shrinks to fit it, down to
        MIN_PAIR_PX. None means no limit.

    Returns
    -------
    float
        Total width.
    """
    if onto is not None and room is not None:
        px = pair_px(canvas, route=route, onto=onto, px=px, room=room)
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
        '<n> min' for a count; anything else as it is.
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
        The countdown ('<n> min' for a count of minutes) when live, else
        '', since the row already shows the clock time.
    """
    return with_minutes(text=row.countdown) if row.live else ''


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
