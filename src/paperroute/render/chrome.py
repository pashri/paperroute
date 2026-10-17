"""Header, banner and route badge shared by every view."""

import math
from typing import Final, Protocol

from paperroute.core.stop_view import Row
from paperroute.render.canvas import INK, PAPER, WIDTH, Box, Canvas, Face

HEADER: Final[int] = 40
BANNER: Final[int] = 34
FASTER: Final[str] = 'FASTER'
TABS: Final[tuple[str, ...]] = ('Stop', 'Map', 'Strip', 'Strip + map')


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


def header_titles(canvas: Canvas, *, origin: str, destination: str,
                  right: float) -> None:
    """
    Draw the origin, an arrow and the destination in the header.

    Parameters
    ----------
    canvas : Canvas
        Target.
    origin : str
        Origin name.
    destination : str
        Destination name.
    right : float
        Left edge of the tabs, which the titles must not reach.
    """
    title = Face(weight=700, px=18)
    half = (right - 60) / 2
    shown = canvas.fit(text=origin, face=title, width=half)
    x = 12 + canvas.text(
        at=(12, 27), text=shown, face=title, grey=PAPER) + 8
    x += arrow(canvas, x=x, y=20, grey=PAPER)
    name = canvas.fit(text=destination, face=title, width=half)
    canvas.text(at=(x, 27), text=name, face=title, grey=PAPER)


def header(canvas: Canvas, *, origin: str, destination: str, clock: str,
           active: str) -> None:
    """
    Draw the black header: From, arrow, Destination, tabs, clock.

    Parameters
    ----------
    canvas : Canvas
        Target.
    origin : str
        Origin name.
    destination : str
        Destination name.
    clock : str
        Clock text.
    active : str
        Name of the highlighted tab.
    """
    canvas.rect(box=(0, 0, WIDTH, HEADER), grey=INK)
    right = header_tabs(canvas, clock=clock, active=active)
    header_titles(
        canvas,
        origin=origin,
        destination=destination,
        right=right,
    )


class Titled(Protocol):
    """A view with the texts the header shows."""

    @property
    def origin(self) -> str:
        """The From stop's name."""

    @property
    def destination(self) -> str:
        """The destination's name."""

    @property
    def clock(self) -> str:
        """The clock text."""


def view_header(canvas: Canvas, *, view: Titled, active: str) -> None:
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
    """
    header(
        canvas,
        origin=view.origin,
        destination=view.destination,
        clock=view.clock,
        active=active,
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
        The countdown when live, else the timetabled clock time.
    """
    return row.countdown if row.live else row.leaves_at


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
