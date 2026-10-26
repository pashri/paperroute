"""Draw a connection pin's Stop view: the next pair, then a pair list."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.connection_view import PAIR_ROWS, ConnectionView, PairHero
from paperroute.render.canvas import HEIGHT, INK, WIDTH, Canvas, Face
from paperroute.render.chrome import (
    BANNER,
    HEADER,
    arrow,
    badge,
    view_banner,
    view_header,
)
from paperroute.render.pair_list import draw_pair_list
from paperroute.render.stop_view import SPLIT, finish

HERO_LEFT: Final[int] = 18
HERO_RIGHT: Final[int] = 418
LABEL_Y: Final[int] = 24
COUNT_Y: Final[int] = 112
LEG_Y: Final[int] = 140  # centre of leg 1's badge line
STEP_Y: Final[tuple[int, ...]] = (178, 204)
ONTO_Y: Final[int] = 230  # centre of leg 2's badge line
FOOTER_RULE: Final[int] = 252
ARRIVE_Y: Final[int] = 286
LIST_HEAD_Y: Final[int] = 22
LIST_TOP: Final[int] = 30
LIST_RIGHT: Final[int] = WIDTH - 12
ROW_STEP: Final[int] = 76
SMALL_BOLD: Final[Face] = Face(weight=700, px=12)
LINE_FACE: Final[Face] = Face(weight=400, px=18)
STEP_FACE: Final[Face] = Face(weight=400, px=17)
BADGE_PX: Final[int] = 18


def hero_countdown(canvas: Canvas, *, hero: PairHero, top: int) -> None:
    """
    Draw the countdown to leg 1 as the Stop view's hero does.

    Parameters
    ----------
    canvas : Canvas
        Target.
    hero : PairHero
        Next pair.
    top : int
        Top of the panel.
    """
    canvas.text(
        at=(HERO_LEFT, top + LABEL_Y), text='NEXT CONNECTION',
        face=SMALL_BOLD, grey=INK,
    )
    big = Face(weight=800, px=88 if hero.live else 68)
    shown = hero.countdown if hero.live else hero.leaves_at
    used = canvas.text(
        at=(HERO_LEFT, top + COUNT_Y), text=shown, face=big, grey=INK,
    )
    if hero.live and shown != 'now' and ':' not in shown:
        canvas.text(
            at=(HERO_LEFT + 8 + used, top + COUNT_Y), text='min',
            face=Face(weight=800, px=28), grey=INK,
        )


def badge_line(
    canvas: Canvas, *, route: str, text: str, y: int, at: str = '',
) -> None:
    """
    Draw a route badge, then text, then a time at the right.

    Parameters
    ----------
    canvas : Canvas
        Target.
    route : str
        Route label.
    text : str
        Text after the badge, cut to fit.
    y : int
        Centre line.
    at : str
        Time drawn at the right edge; empty for none.
    """
    width = badge(canvas, x=HERO_LEFT, y=y, route=route, px=BADGE_PX)
    left = HERO_LEFT + width + 10
    room = HERO_RIGHT - left - (canvas.length(text=at, face=LINE_FACE) + 10)
    canvas.text(
        at=(left, y + 7),
        text=canvas.fit(text=text, face=LINE_FACE, width=room),
        face=LINE_FACE,
        grey=INK,
    )
    if at:
        canvas.text(
            at=(HERO_RIGHT, y + 7), text=at, face=LINE_FACE, grey=INK,
            anchor='rs',
        )


def hero_steps(canvas: Canvas, *, hero: PairHero, top: int) -> None:
    """
    Draw leg 1, the change steps and leg 2.

    Parameters
    ----------
    canvas : Canvas
        Target.
    hero : PairHero
        Next pair.
    top : int
        Top of the panel.
    """
    badge_line(
        canvas, route=hero.route, text=hero.headsign, y=top + LEG_Y,
        at=hero.leaves_at,
    )
    for step, y in zip(hero.steps, STEP_Y, strict=False):
        canvas.text(
            at=(HERO_LEFT, top + y),
            text=canvas.fit(
                text=step, face=STEP_FACE, width=HERO_RIGHT - HERO_LEFT,
            ),
            face=STEP_FACE,
            grey=INK,
        )
    if hero.onto is not None:
        badge_line(
            canvas, route=hero.onto, text=f'at {hero.onto_at}',
            y=top + ONTO_Y,
        )


def hero_footer(canvas: Canvas, *, hero: PairHero, top: int) -> None:
    """
    Draw the rule and the arrival time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    hero : PairHero
        Next pair.
    top : int
        Top of the panel.
    """
    rule = top + FOOTER_RULE
    canvas.rect(box=(HERO_LEFT, rule, HERO_RIGHT, rule + 2), grey=INK)
    used = arrow(canvas, x=HERO_LEFT, y=top + ARRIVE_Y - 9, grey=INK)
    canvas.text(
        at=(HERO_LEFT + used + 6, top + ARRIVE_Y),
        text=f'arr {hero.arrives_at}',
        face=Face(weight=700, px=26),
        grey=INK,
    )


def pair_column(canvas: Canvas, *, view: ConnectionView, top: int) -> None:
    """
    Draw the pair list's heading and rows right of the split.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : ConnectionView
        View-model.
    top : int
        Top of the panel.
    """
    left = SPLIT + 3
    if not view.rows:
        return
    canvas.text(
        at=(left + 9, top + LIST_HEAD_Y), text='LEAVE', face=SMALL_BOLD,
        grey=INK,
    )
    canvas.text(
        at=(LIST_RIGHT - 6, top + LIST_HEAD_Y), text='ARRIVE',
        face=SMALL_BOLD, grey=INK, anchor='rs',
    )
    first = top + LIST_TOP
    draw_pair_list(
        canvas,
        rows=view.rows,
        top=first,
        left=left + 3,
        right=LIST_RIGHT,
        step=min(ROW_STEP, (HEIGHT - first) // PAIR_ROWS),
    )


def draw_connection_view(*, view: ConnectionView) -> NDArray[np.uint8]:
    """
    Draw a connection Stop view as a 1-bit frame.

    Parameters
    ----------
    view : ConnectionView
        View-model.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    view_header(canvas, view=view, active='Stop')
    view_banner(canvas, view=view)
    top = HEADER + (BANNER if view.banner else 0)
    canvas.rect(box=(SPLIT, top, SPLIT + 3, HEIGHT), grey=INK)
    if view.hero is not None:
        hero_countdown(canvas, hero=view.hero, top=top)
        hero_steps(canvas, hero=view.hero, top=top)
        hero_footer(canvas, hero=view.hero, top=top)
    elif view.empty:
        face = Face(weight=700, px=22)
        canvas.text(
            at=(SPLIT / 2, (top + HEIGHT) / 2),
            text=canvas.fit(text=view.empty, face=face, width=SPLIT - 36),
            face=face,
            grey=INK,
            anchor='mm',
        )
    pair_column(canvas, view=view, top=top)
    return finish(canvas)
