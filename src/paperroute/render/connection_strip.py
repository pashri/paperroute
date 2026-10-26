"""Draw a connection pin's Strip: a section per leg, then the pairs."""

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.connection_strip import (
    STRIP_STOPS,
    ConnectionStripView,
    LegSection,
    LegStop,
    StopMark,
)
from paperroute.render.canvas import HEIGHT, INK, PAPER, Canvas, Face
from paperroute.render.chrome import (
    BANNER,
    HEADER,
    arrow,
    badge,
    view_banner,
    view_header,
)
from paperroute.render.pair_list import draw_pair_list
from paperroute.render.stop_view import finish

STRIP_RIGHT: Final[int] = 440
RULE_WIDTH: Final[int] = 3
PAIRS_LEFT: Final[int] = 456
PAIRS_RIGHT: Final[int] = 786
PAIR_STEP: Final[int] = 70
SIDE: Final[int] = 8  # pixels kept each side of the sections
HEAD_STEP: Final[int] = 30
HEAD_GAP: Final[int] = 4  # pixels above each heading band
ROW_MAX: Final[int] = 32
TRACK: Final[int] = 18  # track centre from the section's left
NAME_LEFT: Final[int] = 36  # name start from the section's left
TIME_ROOM: Final[int] = 52  # pixels kept for the time
DOT: Final[int] = 7  # stop marker radius
BADGE_PX: Final[int] = 13
ARROW_ROOM: Final[int] = 26  # pixels an arrow and its gaps take
HEAD_FACE: Final[Face] = Face(weight=700, px=13)
NAME_FACE: Final[Face] = Face(weight=400, px=16)
END_FACE: Final[Face] = Face(weight=700, px=16)
TIME_FACE: Final[Face] = Face(weight=700, px=16)
FOLD_FACE: Final[Face] = Face(weight=400, px=13)
EMPTY_FACE: Final[Face] = Face(weight=700, px=18)


def name_room(*, left: int, right: int) -> int:
    """
    Give the width a stop name may use in a section.

    Parameters
    ----------
    left : int
        Section left.
    right : int
        Section right.

    Returns
    -------
    int
        Pixels between the name's start and the time.
    """
    return right - left - NAME_LEFT - TIME_ROOM


def row_step(*, sections: Sequence[LegSection], top: int) -> int:
    """
    Fit the stop rows into the height left under the headings.

    Parameters
    ----------
    sections : Sequence[LegSection]
        The sections.
    top : int
        Top of the content.

    Returns
    -------
    int
        Row height, at most `ROW_MAX`.
    """
    heads = len(sections) * (HEAD_STEP + HEAD_GAP)
    return min(ROW_MAX, (HEIGHT - top - heads - SIDE) // STRIP_STOPS)


def heading_parts(*, title: str) -> tuple[str, str | None]:
    """
    Split a heading at its arrow.

    Parameters
    ----------
    title : str
        Heading text.

    Returns
    -------
    tuple[str, str | None]
        The text before ' → ' and after it, or the whole text and None.
    """
    before, arrow_, after = title.partition(' → ')
    return (before, after) if arrow_ else (title, None)


def draw_title(
    canvas: Canvas, *, title: str, at: tuple[float, float], right: float,
) -> None:
    """
    Draw a heading, its arrow as a shape, cut to fit.

    Parameters
    ----------
    canvas : Canvas
        Target.
    title : str
        Heading text.
    at : tuple[float, float]
        Left end and baseline.
    right : float
        Right limit.
    """
    x, y = at
    before, after = heading_parts(title=title)
    room = right - x
    if after is not None:
        room -= ARROW_ROOM
        rest = canvas.length(text=after, face=HEAD_FACE)
        room = max(room / 2, room - rest)
    x += canvas.text(
        at=(x, y),
        text=canvas.fit(text=before, face=HEAD_FACE, width=room),
        face=HEAD_FACE,
        grey=INK,
    )
    if after is None:
        return
    x += 4 + arrow(canvas, x=x + 4, y=y - 4, grey=INK)
    canvas.text(
        at=(x, y),
        text=canvas.fit(text=after, face=HEAD_FACE, width=right - x),
        face=HEAD_FACE,
        grey=INK,
    )


def draw_heading(
    canvas: Canvas, *, section: LegSection, y: int, edges: tuple[int, int],
) -> None:
    """
    Draw a section heading as a band: badge, title and note.

    Parameters
    ----------
    canvas : Canvas
        Target.
    section : LegSection
        The section.
    y : int
        Band top.
    edges : tuple[int, int]
        Left and right of the band.
    """
    left, right = edges
    middle = y + HEAD_STEP // 2
    used = badge(
        canvas, x=left + 4, y=middle, route=section.route, px=BADGE_PX,
    )
    title = section.title
    if section.note is not None:
        title = f'{title} · {section.note}'
    draw_title(
        canvas, title=title, at=(left + used + 12, middle + 5),
        right=right - 6,
    )
    canvas.invert(box=(left, y, right, y + HEAD_STEP))


def draw_leg_stop(
    canvas: Canvas,
    *,
    stop: LegStop,
    y: int,
    edges: tuple[int, int],
    step: int,
) -> None:
    """
    Draw one stop row: its track, marker, name and time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    stop : LegStop
        The row.
    y : int
        Row top.
    edges : tuple[int, int]
        Left and right of the section.
    step : int
        Row height.
    """
    x = edges[0] + TRACK
    middle = y + step / 2
    top = middle if stop.mark is StopMark.FIRST else y
    bottom = middle if stop.mark is StopMark.LAST else y + step
    if stop.mark is StopMark.FOLD:
        canvas.dots(
            points=[(x, y + 4 + i * 6) for i in range(step // 6)],
            diameter=3,
            grey=INK,
        )
    else:
        canvas.rect(box=(x - 1.5, top, x + 1.5, bottom), grey=INK)
        box = (x - DOT, middle - DOT, x + DOT, middle + DOT)
        canvas.ellipse(box=box, grey=INK)
        if stop.mark is StopMark.MIDDLE:
            inner = (x - DOT + 2.5, middle - DOT + 2.5)
            canvas.ellipse(
                box=(*inner, x + DOT - 2.5, middle + DOT - 2.5), grey=PAPER,
            )
    draw_stop_text(canvas, stop=stop, y=middle + 6, edges=edges)


def draw_stop_text(
    canvas: Canvas, *, stop: LegStop, y: float, edges: tuple[int, int],
) -> None:
    """
    Draw a stop row's name and time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    stop : LegStop
        The row.
    y : float
        Baseline.
    edges : tuple[int, int]
        Left and right of the section.
    """
    left, right = edges
    face = NAME_FACE
    if stop.mark in {StopMark.FIRST, StopMark.LAST}:
        face = END_FACE
    elif stop.mark is StopMark.FOLD:
        face = FOLD_FACE
    canvas.text(
        at=(left + NAME_LEFT, y),
        text=canvas.fit(
            text=stop.name, face=face, width=name_room(left=left, right=right),
        ),
        face=face,
        grey=INK,
    )
    if stop.time:
        canvas.text(
            at=(right - 6, y), text=stop.time, face=TIME_FACE, grey=INK,
            anchor='rs',
        )


def draw_section(
    canvas: Canvas,
    *,
    section: LegSection,
    y: int,
    edges: tuple[int, int],
    step: int,
) -> int:
    """
    Draw a section's heading, then its rows or its empty text.

    Parameters
    ----------
    canvas : Canvas
        Target.
    section : LegSection
        The section.
    y : int
        Top of the section.
    edges : tuple[int, int]
        Left and right of the section.
    step : int
        Row height.

    Returns
    -------
    int
        Top of the next section.
    """
    left, right = edges
    y += HEAD_GAP
    draw_heading(canvas, section=section, y=y, edges=edges)
    y += HEAD_STEP
    if section.empty is not None:
        canvas.text(
            at=(left + NAME_LEFT, y + step / 2 + 6),
            text=canvas.fit(
                text=section.empty, face=NAME_FACE, width=right - left - 40,
            ),
            face=NAME_FACE,
            grey=INK,
        )
        return y + step
    for index, stop in enumerate(section.stops):
        draw_leg_stop(
            canvas, stop=stop, y=y + index * step, edges=edges, step=step,
        )
    return y + len(section.stops) * step


def draw_strip_sections(
    canvas: Canvas,
    *,
    sections: Sequence[LegSection],
    left: int,
    right: int,
    top: int = HEADER,
) -> None:
    """
    Draw the legs of a pair one under another.

    Parameters
    ----------
    canvas : Canvas
        Target.
    sections : Sequence[LegSection]
        The sections, leg 1 first.
    left : int
        Left of the sections.
    right : int
        Right of the sections; names and headings are cut to fit.
    top : int
        Top of the content.
    """
    step = row_step(sections=sections, top=top)
    y = top
    for section in sections:
        y = draw_section(
            canvas, section=section, y=y, edges=(left, right), step=step,
        )


def draw_pairs(
    canvas: Canvas, *, view: ConnectionStripView, top: int,
) -> None:
    """
    Draw the pair list right of the rule, keeping the selected pair shown.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : ConnectionStripView
        View-model.
    top : int
        Top of the content.
    """
    fits = (HEIGHT - top) // PAIR_STEP
    start = max(0, view.selected - fits + 1)
    draw_pair_list(
        canvas,
        rows=view.pairs[start : start + fits],
        top=top,
        left=PAIRS_LEFT,
        right=PAIRS_RIGHT,
        step=PAIR_STEP,
        selected=view.selected - start,
    )


def draw_connection_strip(*, view: ConnectionStripView) -> NDArray[np.uint8]:
    """
    Draw a connection Strip as a 1-bit frame.

    Parameters
    ----------
    view : ConnectionStripView
        View-model.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    view_header(canvas, view=view, active='Strip')
    view_banner(canvas, view=view)
    top = HEADER + (BANNER if view.banner else 0)
    rule = (STRIP_RIGHT, top, STRIP_RIGHT + RULE_WIDTH, HEIGHT)
    canvas.rect(box=rule, grey=INK)
    draw_strip_sections(
        canvas, sections=view.sections, left=SIDE, right=STRIP_RIGHT - SIDE,
        top=top,
    )
    if not view.sections and view.empty:
        canvas.text(
            at=(STRIP_RIGHT / 2, (top + HEIGHT) / 2),
            text=canvas.fit(
                text=view.empty, face=EMPTY_FACE, width=STRIP_RIGHT - 36,
            ),
            face=EMPTY_FACE,
            grey=INK,
            anchor='mm',
        )
    draw_pairs(canvas, view=view, top=top)
    return finish(canvas)
