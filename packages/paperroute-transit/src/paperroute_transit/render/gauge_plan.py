"""Where the gauge's marks and names go, measured against the hero."""

import math
from dataclasses import dataclass, replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import (
    HEIGHT,
    WIDTH,
    Face,
    font,
    text_width,
)
from paperroute_transit.core.gauge_line import (
    BOLD_KINDS,
    GaugeLine,
    GaugeMark,
    gauge_line,
)
from paperroute_transit.core.names import name_forms
from paperroute_transit.core.stop_under import StopGauge
from paperroute_transit.render.markers import badge_half
from paperroute_transit.render.names import Naming, fit_name

ANGLE: Final[float] = 60.0  # degrees names rise
PITCH: Final[float] = 22.0  # px between marks
WIDE_PITCH: Final[float] = 35.0  # px between marks when names wrap
LINE_DX: Final[float] = 16.0  # px a second line starts right of the first
FIRST_X: Final[float] = 28.0  # px
EDGE: Final[int] = 418  # px, right end of hero text
LEFT_MARGIN: Final[float] = 8.0  # px a badge keeps from the left edge
LIFT: Final[float] = 13.0  # px a name starts above the line
TEXT_CLEAR: Final[int] = 5  # px names keep from hero text
ROOM_STEP: Final[int] = 2  # px
ROOM_MOST: Final[int] = 400  # px
SHORTEST: Final[float] = 110.0  # px of label the last call keeps
DEST_LONGEST: Final[float] = 190.0  # px of Destination label kept
LINE_FREE: Final[int] = 446  # px
LINE_RUNS: Final[int] = 428  # px
RUN_RISE: Final[int] = 7  # px
BRACKET_Y: Final[int] = 458  # px
BRACKET_PAD: Final[float] = 6.0  # px a bracket runs past its end marks
MINUTE_DROP: Final[int] = 22  # px, minutes baseline below the line
STEM: Final[float] = 22.0  # px from the line to a hung badge's centre
WEDGE: Final[float] = 10.0  # px the heading wedge reaches past a badge
BADGE_CLEAR: Final[float] = 6.0  # px a badge keeps from a minute
NAME_FACE: Final[Face] = Face(weight=400, px=15)
BOLD_FACE: Final[Face] = Face(weight=700, px=15)
MINUTE_FACE: Final[Face] = Face(weight=700, px=12)
COS: Final[float] = math.cos(math.radians(ANGLE))
SIN: Final[float] = math.sin(math.radians(ANGLE))


@dataclass(frozen=True)
class GaugePlan:
    """
    The gauge's measurements.

    Attributes
    ----------
    line : GaugeLine
        The marks in slots.
    pitch : float
        Pixels between marks.
    first : float
        Pixel x of slot 0.
    line_y : int
        Frame y of the gauge line.
    hang : float
        Pixels the badge hangs left of its spot.
    wrap : bool
        True when names may take two lines.
    """

    line: GaugeLine
    pitch: float
    first: float
    line_y: int
    hang: float
    wrap: bool


def slot_x(*, plan: GaugePlan, slot: float) -> float:
    """
    Give the pixel x of a slot.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    slot : float
        Slot position.

    Returns
    -------
    float
        Pixel x.
    """
    return plan.first + slot * plan.pitch


def face_of(*, mark: GaugeMark) -> Face:
    """
    Pick the face a mark's name is drawn in.

    Parameters
    ----------
    mark : GaugeMark
        The mark.

    Returns
    -------
    Face
        Bold for the From and the Destination, else regular.
    """
    return BOLD_FACE if mark.kind in BOLD_KINDS else NAME_FACE


def cap_height(*, face: Face) -> float:
    """
    Measure a face's capital height.

    Parameters
    ----------
    face : Face
        The face.

    Returns
    -------
    float
        Height in pixels.
    """
    return float(font(face).getMetrics().fCapHeight)


def name_start(
    *, x: float, line_y: int, face: Face,
) -> tuple[float, float]:
    """
    Give where a name's baseline starts above a mark.

    Parameters
    ----------
    x : float
        Pixel x of the mark.
    line_y : int
        Frame y of the line.
    face : Face
        The name's face.

    Returns
    -------
    tuple[float, float]
        Pixel x and y.
    """
    cap = cap_height(face=face)
    return x + cap / 2 * SIN, line_y - LIFT


def grown(*, mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """
    Grow a mask by one pixel in all eight directions.

    Parameters
    ----------
    mask : NDArray[np.bool_]
        The mask.

    Returns
    -------
    NDArray[np.bool_]
        The grown copy.
    """
    padded = np.pad(mask, 1)
    height, width = mask.shape
    out = mask.copy()
    for dy in range(3):
        for dx in range(3):
            out |= padded[dy : dy + height, dx : dx + width]
    return out


def keep_out(*, ink: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """
    Build the area names keep out of.

    Parameters
    ----------
    ink : NDArray[np.bool_]
        Pixels the hero has inked.

    Returns
    -------
    NDArray[np.bool_]
        The ink with the columns right of the hero text, grown.
    """
    keep = ink.copy()
    keep[:, EDGE + 2 :] = True
    for _ in range(TEXT_CLEAR):
        keep = grown(mask=keep)
    return keep


def blocked_at(
    *, keep: NDArray[np.bool_], x: float, y: float,
) -> bool:
    """
    Test whether a point is off the frame or in the keep-out.

    Parameters
    ----------
    keep : NDArray[np.bool_]
        The keep-out.
    x : float
        Pixel x.
    y : float
        Pixel y.

    Returns
    -------
    bool
        True when blocked.
    """
    col, row = round(x), round(y)
    if not (0 <= col < WIDTH and 0 <= row < HEIGHT):
        return True
    return bool(keep[row, col])


def name_room(
    *, keep: NDArray[np.bool_], x: float, line_y: int, face: Face,
) -> float:
    """
    Measure how far a rising name can run before it hits something.

    Parameters
    ----------
    keep : NDArray[np.bool_]
        The keep-out.
    x : float
        Pixel x of the mark.
    line_y : int
        Frame y of the line.
    face : Face
        The name's face.

    Returns
    -------
    float
        Length along the name in pixels.
    """
    cap = cap_height(face=face)
    start_x, start_y = name_start(x=x, line_y=line_y, face=face)
    for length in range(0, ROOM_MOST, ROOM_STEP):
        for step in (0.0, 0.5, 1.0):
            px = start_x + length * COS - step * cap * SIN
            py = start_y - length * SIN - step * cap * COS
            if blocked_at(keep=keep, x=px, y=py):
                return float(length - ROOM_STEP)
    return float(ROOM_MOST)


def badge_reach(*, route: str) -> tuple[float, float]:
    """
    Give how far a badge reaches left and right of its centre.

    Parameters
    ----------
    route : str
        Route label.

    Returns
    -------
    tuple[float, float]
        Left and right reach in pixels.
    """
    half = badge_half(route=route)[0]
    return half + 1, half + WEDGE


def reaches(
    *, gauge: StopGauge, first: float, pitch: float, naming: Naming,
) -> tuple[int, int]:
    """
    Give the last slots a call and the Destination may take.

    Parameters
    ----------
    gauge : StopGauge
        The gauge's content.
    first : float
        Pixel x of slot 0.
    pitch : float
        Pixels between marks.
    naming : Naming
        The shortening rules.

    Returns
    -------
    tuple[int, int]
        Last slot for a call and for the Destination.
    """
    cap = cap_height(face=NAME_FACE)
    reach = math.floor((EDGE - SHORTEST * COS - cap - first) / pitch)
    if gauge.end is None:
        return reach, reach
    forms = name_forms(
        name=gauge.calls[gauge.end].name,
        rules=naming.rules,
        siblings=naming.siblings,
    )
    width = min(text_width(text=forms[-1], face=BOLD_FACE), DEST_LONGEST)
    bold_cap = cap_height(face=BOLD_FACE)
    dest = math.floor((EDGE - width * COS - bold_cap - 2 - first) / pitch)
    return reach, min(dest, reach + 1)


def badge_hang(*, line: GaugeLine, pitch: float, route: str) -> float:
    """
    Give how far the badge hangs left to clear the next minute.

    Parameters
    ----------
    line : GaugeLine
        The marks in slots.
    pitch : float
        Pixels between marks.
    route : str
        Route label.

    Returns
    -------
    float
        Pixels, 0 without a badge.
    """
    if line.bus is None:
        return 0.0
    bus = line.bus
    ahead = next(
        (m for m in line.marks if m.slot > bus and m.minutes), None,
    )
    if ahead is None:
        return 0.0
    right = badge_reach(route=route)[1]
    half = text_width(text=ahead.minutes, face=MINUTE_FACE) / 2
    gap = bus * pitch + right + BADGE_CLEAR - (ahead.slot * pitch - half)
    return max(0.0, gap)


def first_x(
    *, line: GaugeLine, pitch: float, hang: float, route: str,
) -> float:
    """
    Give the pixel x of slot 0, moved right to keep the badge on paper.

    Parameters
    ----------
    line : GaugeLine
        The marks in slots.
    pitch : float
        Pixels between marks.
    hang : float
        Pixels the badge hangs left.
    route : str
        Route label.

    Returns
    -------
    float
        Pixel x.
    """
    if line.bus is None:
        return FIRST_X
    left = badge_reach(route=route)[0]
    return max(FIRST_X, LEFT_MARGIN + left + hang - line.bus * pitch)


def line_level(
    *,
    line: GaugeLine,
    first: float,
    pitch: float,
    hang: float,
    route: str,
) -> int:
    """
    Pick the frame y of the gauge line.

    Parameters
    ----------
    line : GaugeLine
        The marks in slots.
    first : float
        Pixel x of slot 0.
    pitch : float
        Pixels between marks.
    hang : float
        Pixels the badge hangs left.
    route : str
        Route label.

    Returns
    -------
    int
        Frame y, raised for runs and again when a badge hangs over one.
    """
    if not line.runs:
        return LINE_FREE
    if line.bus is None:
        return LINE_RUNS
    left, right = badge_reach(route=route)
    centre = first + line.bus * pitch - hang
    clash = any(
        first + run.first * pitch - BRACKET_PAD <= centre + right
        and centre - left <= first + run.last * pitch + BRACKET_PAD
        for run in line.runs
    )
    return LINE_RUNS - RUN_RISE if clash else LINE_RUNS


def plan_at(
    *, gauge: StopGauge, route: str, naming: Naming, pitch: float,
) -> GaugePlan:
    """
    Measure a gauge at one pitch.

    Parameters
    ----------
    gauge : StopGauge
        The gauge's content.
    route : str
        Route label.
    naming : Naming
        The shortening rules.
    pitch : float
        Pixels between marks.

    Returns
    -------
    GaugePlan
        The measurements, with names on one line.
    """
    reach, dest = reaches(
        gauge=gauge, first=FIRST_X, pitch=pitch, naming=naming,
    )
    line = gauge_line(
        gauge=gauge, reach=reach, dest_reach=dest, rules=naming.rules,
    )
    hang = badge_hang(line=line, pitch=pitch, route=route)
    first = first_x(line=line, pitch=pitch, hang=hang, route=route)
    if first != FIRST_X:
        reach, dest = reaches(
            gauge=gauge, first=first, pitch=pitch, naming=naming,
        )
        line = gauge_line(
            gauge=gauge, reach=reach, dest_reach=dest, rules=naming.rules,
        )
    level = line_level(
        line=line, first=first, pitch=pitch, hang=hang, route=route,
    )
    return GaugePlan(
        line=line, pitch=pitch, first=first, line_y=level, hang=hang,
        wrap=False,
    )


def sibling_naming(*, plan: GaugePlan, naming: Naming) -> Naming:
    """
    Add every mark's name to the naming as siblings.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    naming : Naming
        The shortening rules.

    Returns
    -------
    Naming
        The naming with the marks' names as siblings.
    """
    names = tuple(mark.name for mark in plan.line.marks)
    return replace(naming, siblings=names)


def needs_two(
    *, plan: GaugePlan, keep: NDArray[np.bool_], naming: Naming,
) -> bool:
    """
    Test whether any name is too long for one line.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    keep : NDArray[np.bool_]
        The keep-out.
    naming : Naming
        The shortening rules.

    Returns
    -------
    bool
        True when some mark's shortest form overruns its room.
    """
    shared = sibling_naming(plan=plan, naming=naming)
    for mark in plan.line.marks:
        face = face_of(mark=mark)
        forms = name_forms(
            name=mark.name, rules=shared.rules, siblings=shared.siblings,
        )
        room = name_room(
            keep=keep,
            x=slot_x(plan=plan, slot=mark.slot),
            line_y=plan.line_y,
            face=face,
        )
        if text_width(text=forms[-1], face=face) > room:
            return True
    return False


def gauge_plan(
    *,
    gauge: StopGauge,
    route: str,
    keep: NDArray[np.bool_],
    naming: Naming,
) -> GaugePlan:
    """
    Measure a gauge, widening the pitch when names must wrap.

    Parameters
    ----------
    gauge : StopGauge
        The gauge's content.
    route : str
        Route label.
    keep : NDArray[np.bool_]
        The keep-out.
    naming : Naming
        The shortening rules.

    Returns
    -------
    GaugePlan
        The measurements.
    """
    plan = plan_at(gauge=gauge, route=route, naming=naming, pitch=PITCH)
    if not needs_two(plan=plan, keep=keep, naming=naming):
        return plan
    wide = plan_at(
        gauge=gauge, route=route, naming=naming, pitch=WIDE_PITCH,
    )
    return replace(wide, wrap=True)


def mark_lines(
    *, plan: GaugePlan, keep: NDArray[np.bool_], naming: Naming,
) -> tuple[tuple[str, ...], ...]:
    """
    Fit each mark's name to its room.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    keep : NDArray[np.bool_]
        The keep-out.
    naming : Naming
        The shortening rules.

    Returns
    -------
    tuple[tuple[str, ...], ...]
        Each mark's lines, in mark order.
    """
    shared = sibling_naming(plan=plan, naming=naming)
    out = []
    for mark in plan.line.marks:
        face = face_of(mark=mark)
        x = slot_x(plan=plan, slot=mark.slot)
        room = name_room(keep=keep, x=x, line_y=plan.line_y, face=face)
        forms = name_forms(
            name=mark.name, rules=shared.rules, siblings=shared.siblings,
        )
        if plan.wrap and text_width(text=forms[-1], face=face) > room:
            room = min(room, name_room(
                keep=keep, x=x + LINE_DX, line_y=plan.line_y, face=face,
            ))
            out.append(fit_name(
                text=mark.name, face=face, width=room, naming=shared,
                two_lines=True,
            ))
        else:
            out.append(fit_name(
                text=mark.name, face=face, width=room, naming=shared,
            ))
    return tuple(out)
