"""Header, banner and route badge shared by every view."""

from typing import Final

from paperroute.render.canvas import INK, PAPER, WIDTH, Canvas, Face

HEADER: Final[int] = 40
BANNER: Final[int] = 34
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
