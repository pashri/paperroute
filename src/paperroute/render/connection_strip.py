"""Draw a connection pin's Strip: a section per leg, then the pairs."""

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.connection_strip import (
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
        Row height, at most `ROW_MAX`, so every row and vehicle note
        fits.
    """
    heads = len(sections) * (HEAD_STEP + HEAD_GAP)
    rows = sum(section_rows(section=section) for section in sections)
    return min(ROW_MAX, (HEIGHT - top - heads - SIDE) // max(1, rows))


def section_rows(*, section: LegSection) -> int:
    """
    Count the rows a section draws under its heading.

    Parameters
    ----------
    section : LegSection
        The section.

    Returns
    -------
    int
        Its stop rows, or one for its empty text, plus one for a vehicle
        note.
    """
    stops = 1 if section.empty is not None else len(section.stops)
    return stops + (section.vehicle is not None)


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
        room = max(room / 2, room - canvas.length(text=after, face=HEAD_FACE))
    x += canvas.text(
        at=(x, y),
        text=canvas.fit(text=before, face=HEAD_FACE, width=room),
        face=HEAD_FACE,
        grey=INK,
    )
    if after is not None:
        title_tail(canvas, text=after, at=(x, y), right=right)


def title_tail(
    canvas: Canvas, *, text: str, at: tuple[float, float], right: float,
) -> None:
    """
    Draw a heading's arrow and the part after it, cut to fit.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The part after the arrow.
    at : tuple[float, float]
        Where the arrow starts, and the baseline.
    right : float
        Right limit.
    """
    x, y = at
    x += 4 + arrow(canvas, x=x + 4, y=y - 4, grey=INK)
    canvas.text(
        at=(x, y),
        text=canvas.fit(text=text, face=HEAD_FACE, width=right - x),
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
    if stop.mark is StopMark.FOLD:
        canvas.dots(
            points=[(x, y + 4 + i * 6) for i in range(step // 6)],
            diameter=3,
            grey=INK,
        )
    else:
        stop_disc(canvas, mark=stop.mark, x=x, span=(y, y + step))
    draw_stop_text(canvas, stop=stop, y=middle + 6, edges=edges)


def stop_disc(
    canvas: Canvas, *, mark: StopMark, x: float, span: tuple[int, int],
) -> None:
    """
    Draw a stop row's track and its disc, hollow for a middle stop.

    The track stops at the disc on the first and last rows.

    Parameters
    ----------
    canvas : Canvas
        Target.
    mark : StopMark
        The row's mark; not a fold.
    x : float
        The track's centre.
    span : tuple[int, int]
        The row's top and bottom.
    """
    middle = sum(span) / 2
    top = middle if mark is StopMark.FIRST else span[0]
    bottom = middle if mark is StopMark.LAST else span[1]
    canvas.rect(box=(x - 1.5, top, x + 1.5, bottom), grey=INK)
    canvas.ellipse(
        box=(x - DOT, middle - DOT, x + DOT, middle + DOT), grey=INK,
    )
    if mark is StopMark.MIDDLE:
        canvas.ellipse(
            box=(
                x - DOT + 2.5, middle - DOT + 2.5,
                x + DOT - 2.5, middle + DOT - 2.5,
            ),
            grey=PAPER,
        )


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
    draw_heading(canvas, section=section, y=y + HEAD_GAP, edges=edges)
    y += HEAD_GAP + HEAD_STEP
    if section.vehicle is not None:
        vehicle_line(canvas, text=section.vehicle, y=y, edges=edges, step=step)
        y += step
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


def vehicle_line(
    canvas: Canvas,
    *,
    text: str,
    y: int,
    edges: tuple[int, int],
    step: int,
) -> None:
    """
    Draw where a leg's vehicle is, in a row of its own.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The line.
    y : int
        Row top.
    edges : tuple[int, int]
        Left and right of the section.
    step : int
        Row height.
    """
    left, right = edges
    canvas.text(
        at=(left + NAME_LEFT, y + step / 2 + 5),
        text=canvas.fit(
            text=text, face=FOLD_FACE,
            width=right - left - NAME_LEFT - SIDE,
        ),
        face=FOLD_FACE,
        grey=INK,
    )


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


def empty_strip(canvas: Canvas, *, text: str, top: int) -> None:
    """
    Draw why there are no legs, centred left of the rule.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The message.
    top : int
        Top of the content.
    """
    canvas.text(
        at=(STRIP_RIGHT / 2, (top + HEIGHT) / 2),
        text=canvas.fit(text=text, face=EMPTY_FACE, width=STRIP_RIGHT - 36),
        face=EMPTY_FACE,
        grey=INK,
        anchor='mm',
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
    canvas.rect(
        box=(STRIP_RIGHT, top, STRIP_RIGHT + RULE_WIDTH, HEIGHT), grey=INK,
    )
    draw_strip_sections(
        canvas, sections=view.sections, left=SIDE, right=STRIP_RIGHT - SIDE,
        top=top,
    )
    if not view.sections and view.empty:
        empty_strip(canvas, text=view.empty, top=top)
    draw_pairs(canvas, view=view, top=top)
    return finish(canvas)
