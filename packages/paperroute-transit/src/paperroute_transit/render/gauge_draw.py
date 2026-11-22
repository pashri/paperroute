"""Drawing the gauge's track, marks, bus, brackets, names and counts."""

import math
from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas, Face, font
from paperroute_transit.core.gauge_line import BOLD_KINDS, GaugeBreak, MarkKind
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.stop_under import StopGauge
from paperroute_transit.core.stop_view import stops_text
from paperroute_transit.render.gauge_plan import (
    ANGLE,
    BRACKET_PAD,
    BRACKET_Y,
    EDGE,
    LEFT_MARGIN,
    LINE_DX,
    MINUTE_DROP,
    MINUTE_FACE,
    STEM,
    GaugePlan,
    cap_height,
    face_of,
    gauge_plan,
    grown,
    keep_out,
    mark_lines,
    name_start,
    slot_x,
)
from paperroute_transit.render.markers import draw_live
from paperroute_transit.render.names import Naming, fit_name
from paperroute_transit.render.strip import (
    DISC_RADIUS,
    RIDE_WIDTH,
    RING_RADIUS,
    RING_STROKE,
    TRACK_WIDTH,
)

TAIL: Final[float] = 14.0  # px of line past an end mark
ARROW: Final[tuple[float, float]] = (8.0, 6.0)  # px long, px half high
DOT: Final[int] = 5  # px
DOT_GAP: Final[float] = 3.0  # px
STEM_EDGE: Final[float] = 2.0  # px from a badge's stem to the first dot
COUNT_LIFT: Final[float] = 2.0  # px a count's descender keeps over the line
COUNT_SLIDE: Final[tuple[int, ...]] = tuple(
    sorted(range(-12, 13), key=abs),
)
MARK_CLEAR: Final[int] = 3  # px a count keeps from names and marks
COUNT_FACES: Final[tuple[Face, ...]] = (
    Face(weight=400, px=12),
    Face(weight=400, px=11),
)
LABEL_FACE: Final[Face] = Face(weight=700, px=12)
TICK: Final[int] = 3  # px a bracket's ends rise
LABEL_GAP: Final[float] = 8.0  # px between bracket labels
STEM_WIDTH: Final[int] = 2  # px
COUNT_PAD: Final[int] = 60  # px of crop around a count's span
COUNT_RISE: Final[int] = 30  # px of crop above the line


def mark_edge(*, kind: MarkKind) -> float:
    """
    Give how far a mark's drawing reaches from its centre.

    Parameters
    ----------
    kind : MarkKind
        What the mark stands for.

    Returns
    -------
    float
        Pixels.
    """
    if kind in BOLD_KINDS:
        return DISC_RADIUS
    return RING_RADIUS + RING_STROKE / 2


def dot_lefts(*, near: float, far: float) -> list[int]:
    """
    Space dots evenly across a stretch of line.

    Parameters
    ----------
    near : float
        Pixel x where the stretch starts.
    far : float
        Pixel x where it ends.

    Returns
    -------
    list[int]
        Left pixel x of each dot.
    """
    room = far - near
    count = max(1, round((room - DOT_GAP) / (DOT + DOT_GAP)))
    gap = (room - count * DOT) / (count + 1)
    return [round(near + gap + k * (DOT + gap)) for k in range(count)]


def kind_at(*, plan: GaugePlan, slot: int) -> MarkKind:
    """
    Give the kind of the mark in a slot.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    slot : int
        Slot position.

    Returns
    -------
    MarkKind
        The mark's kind, CALL when no mark is there.
    """
    for mark in plan.line.marks:
        if mark.slot == slot:
            return mark.kind
    return MarkKind.CALL


def break_span(
    *, plan: GaugePlan, gap: GaugeBreak,
) -> tuple[float, float]:
    """
    Give the pixel stretch a break's dots fill.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    gap : GaugeBreak
        The break.

    Returns
    -------
    tuple[float, float]
        Near and far pixel x.
    """
    bus = plan.line.bus
    if bus is not None and plan.line.back == gap and bus > gap.first:
        near = slot_x(plan=plan, slot=bus) + STEM_EDGE
    else:
        near = slot_x(plan=plan, slot=gap.first) + mark_edge(
            kind=kind_at(plan=plan, slot=gap.first),
        )
    far = slot_x(plan=plan, slot=gap.last) - mark_edge(
        kind=kind_at(plan=plan, slot=gap.last),
    )
    return near, far


def band(
    canvas: Canvas, *, left: float, right: float, y: float, height: int,
) -> None:
    """
    Fill a horizontal bar centred on a line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    left : float
        Pixel x of the left end.
    right : float
        Pixel x of the right end.
    y : float
        Frame y of the line.
    height : int
        Bar height in pixels.
    """
    top = y - height // 2
    canvas.rect(box=(left, top, right, top + height), grey=INK)


def draw_break(canvas: Canvas, *, plan: GaugePlan, gap: GaugeBreak) -> None:
    """
    Replace a break's line with dots.

    Parameters
    ----------
    canvas : Canvas
        Target.
    plan : GaugePlan
        The measurements.
    gap : GaugeBreak
        The break.
    """
    y = plan.line_y
    near, far = break_span(plan=plan, gap=gap)
    reach = RIDE_WIDTH / 2 + 1
    canvas.rect(
        box=(math.floor(near), y - reach, math.ceil(far), y + reach),
        grey=PAPER,
    )
    for left in dot_lefts(near=near, far=far):
        canvas.ellipse(
            box=(left, y + 0.5 - DOT / 2, left + DOT, y + 0.5 + DOT / 2),
            grey=INK,
        )


def draw_arrow(canvas: Canvas, *, right: float, y: float) -> None:
    """
    Draw the arrowhead at the end of the line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    right : float
        Pixel x where the head starts.
    y : float
        Frame y of the line.
    """
    length, half = ARROW
    canvas.polygon(
        points=[(right, y - half), (right + length, y), (right, y + half)],
        grey=INK,
    )


def draw_track(canvas: Canvas, *, plan: GaugePlan) -> None:
    """
    Draw the thin line, the ride, the arrow and the dotted breaks.

    Parameters
    ----------
    canvas : Canvas
        Target.
    plan : GaugePlan
        The measurements.
    """
    line, y = plan.line, plan.line_y
    first = slot_x(plan=plan, slot=line.marks[0].slot)
    last = slot_x(plan=plan, slot=line.marks[-1].slot)
    left = first if line.starts else first - TAIL
    right = last + TAIL if line.arrow else last
    band(canvas, left=left, right=right, y=y, height=TRACK_WIDTH)
    if line.arrow:
        draw_arrow(canvas, right=right, y=y)
    if line.ride is not None:
        band(
            canvas,
            left=slot_x(plan=plan, slot=line.ride[0]),
            right=slot_x(plan=plan, slot=line.ride[1]),
            y=y,
            height=RIDE_WIDTH,
        )
    for gap in (line.back, line.ahead):
        if gap is not None:
            draw_break(canvas, plan=plan, gap=gap)


def draw_marks(canvas: Canvas, *, plan: GaugePlan) -> None:
    """
    Draw the rings, discs and minutes.

    Parameters
    ----------
    canvas : Canvas
        Target.
    plan : GaugePlan
        The measurements.
    """
    y = plan.line_y
    for mark in plan.line.marks:
        x = slot_x(plan=plan, slot=mark.slot)
        if mark.kind in BOLD_KINDS:
            radius = DISC_RADIUS
            canvas.ellipse(
                box=(x - radius, y - radius, x + radius, y + radius),
                grey=INK,
            )
        else:
            radius = RING_RADIUS
            box = (x - radius, y - radius, x + radius, y + radius)
            canvas.ellipse(box=box, grey=PAPER)
            canvas.ellipse(box=box, grey=INK, stroke=RING_STROKE)
        if mark.minutes:
            canvas.text(
                at=(x, y + MINUTE_DROP),
                text=mark.minutes,
                face=MINUTE_FACE,
                grey=INK,
                anchor='ms',
            )


def draw_bus(canvas: Canvas, *, plan: GaugePlan, route: str) -> None:
    """
    Draw the bus's stem and its hung badge.

    Parameters
    ----------
    canvas : Canvas
        Target.
    plan : GaugePlan
        The measurements.
    route : str
        Route label.
    """
    if plan.line.bus is None:
        return
    x = slot_x(plan=plan, slot=plan.line.bus)
    y = plan.line_y
    canvas.rect(
        box=(x - STEM_WIDTH / 2, y, x + STEM_WIDTH / 2, y + STEM),
        grey=INK,
    )
    marker = Marker(
        route=route,
        x=x - plan.hang,
        y=y + STEM,
        bearing=90.0,
        live=True,
        pinned=True,
    )
    draw_live(canvas, marker=marker, centre=(x - plan.hang, y + STEM))


def draw_brackets(
    canvas: Canvas, *, plan: GaugePlan, naming: Naming,
) -> None:
    """
    Draw a bracket and street label under each shared-street run.

    Parameters
    ----------
    canvas : Canvas
        Target.
    plan : GaugePlan
        The measurements.
    naming : Naming
        The shortening rules.
    """
    taken = LEFT_MARGIN - LABEL_GAP
    baseline = BRACKET_Y + 6 + cap_height(face=LABEL_FACE)
    for run in plan.line.runs:
        left = slot_x(plan=plan, slot=run.first) - BRACKET_PAD
        right = slot_x(plan=plan, slot=run.last) + BRACKET_PAD
        canvas.rect(box=(left, BRACKET_Y, right, BRACKET_Y + 2), grey=INK)
        for end in (left, right - 2):
            canvas.rect(
                box=(end, BRACKET_Y - TICK, end + 2, BRACKET_Y), grey=INK,
            )
        text = fit_name(
            text=run.street,
            face=LABEL_FACE,
            width=EDGE - LEFT_MARGIN,
            naming=naming,
        )[0]
        width = canvas.length(text=text, face=LABEL_FACE)
        start = max((left + right - width) / 2, taken + LABEL_GAP)
        start = min(start, EDGE - width)
        canvas.text(at=(start, baseline), text=text, face=LABEL_FACE, grey=INK)
        taken = start + width


def names_ink(
    *, plan: GaugePlan, lines: Sequence[tuple[str, ...]],
) -> NDArray[np.bool_]:
    """
    Draw every name rising from its mark and return the ink.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    lines : Sequence[tuple[str, ...]]
        Each mark's lines, in mark order.

    Returns
    -------
    NDArray[np.bool_]
        True where a name has ink.
    """
    canvas = Canvas(smooth=True)
    for mark, mark_text in zip(plan.line.marks, lines, strict=True):
        face = face_of(mark=mark)
        x = slot_x(plan=plan, slot=mark.slot)
        for index, text in enumerate(mark_text):
            at = name_start(
                x=x + index * LINE_DX, line_y=plan.line_y, face=face,
            )
            canvas.text(
                at=at, text=text, face=face, grey=INK, angle=-ANGLE,
            )
    return canvas.grey() < 128


def draw_count(
    canvas: Canvas,
    *,
    text: str,
    face: Face,
    centre: float,
    baseline: float,
) -> None:
    """
    Draw a count centred on a point.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The count's text.
    face : Face
        The count's face.
    centre : float
        Pixel x of the text's middle.
    baseline : float
        Pixel y of the baseline.
    """
    canvas.text(
        at=(centre, baseline), text=text, face=face, grey=INK, anchor='ms',
    )


def count_baseline(*, line_y: int, face: Face) -> float:
    """
    Give the y a count's baseline sits at above the ride.

    Parameters
    ----------
    line_y : int
        Frame y of the line.
    face : Face
        The count's face.

    Returns
    -------
    float
        Pixel y.
    """
    descent = float(font(face).getMetrics().fDescent)
    return line_y - RIDE_WIDTH / 2 - COUNT_LIFT - descent


def count_choice(
    *,
    line_y: int,
    near: float,
    far: float,
    count: int,
    avoid: NDArray[np.bool_],
) -> tuple[str, Face, int]:
    """
    Pick the count's wording, size and sideways shift.

    Parameters
    ----------
    line_y : int
        Frame y of the line.
    near : float
        Pixel x where the break starts.
    far : float
        Pixel x where it ends.
    count : int
        Stops hidden.
    avoid : NDArray[np.bool_]
        Ink the count keeps clear of.

    Returns
    -------
    tuple[str, Face, int]
        Text, face and x shift; the last wording unshifted when none
        fits.
    """
    words = f'+{stops_text(stops=count)}'
    options = (
        (words, COUNT_FACES[0]),
        (words, COUNT_FACES[1]),
        (f'+{count}', COUNT_FACES[0]),
    )
    left = max(0, int(near) - COUNT_PAD)
    right = min(avoid.shape[1], int(far) + COUNT_PAD)
    top = max(0, line_y - COUNT_RISE)
    near_ink = avoid[top:line_y + 1, left:right]
    for _ in range(MARK_CLEAR):
        near_ink = grown(mask=near_ink)
    for text, face in options:
        for shift in COUNT_SLIDE:
            crop = Canvas(
                width=right - left,
                height=line_y + 1 - top,
                smooth=True,
            )
            draw_count(
                crop,
                text=text,
                face=face,
                centre=(near + far) / 2 + shift - left,
                baseline=count_baseline(line_y=line_y, face=face) - top,
            )
            if not ((crop.grey() < 128) & near_ink).any():
                return text, face, shift
    return options[-1][0], options[-1][1], 0


def counts_ink(
    *, plan: GaugePlan, avoid: NDArray[np.bool_],
) -> NDArray[np.bool_]:
    """
    Draw each break's count and return the ink.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    avoid : NDArray[np.bool_]
        Ink the counts keep clear of.

    Returns
    -------
    NDArray[np.bool_]
        True where a count has ink.
    """
    canvas = Canvas(smooth=True)
    for gap in (plan.line.back, plan.line.ahead):
        if gap is None:
            continue
        near, far = break_span(plan=plan, gap=gap)
        text, face, shift = count_choice(
            line_y=plan.line_y,
            near=near,
            far=far,
            count=gap.count,
            avoid=avoid,
        )
        draw_count(
            canvas,
            text=text,
            face=face,
            centre=(near + far) / 2 + shift,
            baseline=count_baseline(line_y=plan.line_y, face=face),
        )
    return canvas.grey() < 128


def draw_gauge(
    canvas: Canvas, *, gauge: StopGauge, route: str, naming: Naming,
) -> NDArray[np.bool_]:
    """
    Plan the gauge and draw what the board canvas holds of it.

    Parameters
    ----------
    canvas : Canvas
        The board canvas, holding the hero already.
    gauge : StopGauge
        The gauge's content.
    route : str
        Route label.
    naming : Naming
        The shortening rules.

    Returns
    -------
    NDArray[np.bool_]
        Ink of the names and counts, drawn separately.
    """
    keep = keep_out(ink=canvas.grey() < 128)
    plan = gauge_plan(gauge=gauge, route=route, keep=keep, naming=naming)
    draw_track(canvas, plan=plan)
    draw_bus(canvas, plan=plan, route=route)
    draw_marks(canvas, plan=plan)
    draw_brackets(canvas, plan=plan, naming=naming)
    names = names_ink(
        plan=plan, lines=mark_lines(plan=plan, keep=keep, naming=naming),
    )
    return names | counts_ink(
        plan=plan, avoid=names | (canvas.grey() < 128),
    )
