"""Drawing the route gauge: the line, every bus on it, names and counts."""

import math
from collections.abc import Sequence
from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, Canvas, text_width
from paperroute_transit.core.gauge_ends import (
    CALLS_KEPT,
    RouteGauge,
    left_end,
    route_line,
)
from paperroute_transit.core.gauge_line import BREAK_SLOTS, BusSlot
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.names import base_name
from paperroute_transit.render.gauge_draw import (
    counts_ink,
    draw_brackets,
    draw_dots,
    draw_marks,
    draw_track,
    names_ink,
)
from paperroute_transit.render.gauge_plan import (
    BOLD_FACE,
    COS,
    DEST_LONGEST,
    EDGE,
    FIRST_X,
    LINE_FREE,
    LINE_RUNS,
    PITCH,
    WIDE_PITCH,
    GaugePlan,
    cap_height,
    keep_out,
    mark_lines,
    needs_two,
    slot_x,
)
from paperroute_transit.render.markers import (
    BADGE_HALF_HEIGHT,
    STRIP_WEDGE,
    badge_box,
    draw_dashed,
    draw_hollow,
    draw_live,
)
from paperroute_transit.render.names import Naming

SPREAD_MOST: Final[float] = 2.0  # times PITCH
FOLD_GAP: Final[float] = 2.0  # px between same-way badges before they fold
TICK: Final[int] = 4  # px a folded bus's tick shows below its badge
TICK_WIDTH: Final[int] = 2  # px
RIGHT_END: Final[float] = EDGE - 30  # px the last slot may reach
END_CLEAR: Final[float] = 2.0  # px the end name keeps from EDGE


def slot_budget(*, pitch: float, right_end: float) -> tuple[int, int]:
    """
    Give the slots the left end may take and the whole line's reach.

    The left end always has room for a break.

    Parameters
    ----------
    pitch : float
        Pixels between marks.
    right_end : float
        Pixel x the last slot may reach.

    Returns
    -------
    tuple[int, int]
        Last slot left of the From, and the last slot of the line.
    """
    reach = math.floor((right_end - FIRST_X) / pitch)
    return max(reach - CALLS_KEPT - 4, BREAK_SLOTS + 1), reach


def line_end(*, gauge: RouteGauge) -> float:
    """
    Give the pixel x the last slot may reach, leaving the end name room.

    Parameters
    ----------
    gauge : RouteGauge
        The gauge's content.

    Returns
    -------
    float
        Pixel x, short enough that the last name rises whole before
        ``EDGE``.
    """
    if gauge.end is not None:
        name = base_name(name=gauge.pattern.stops[gauge.end].name)
    elif gauge.calls:
        name = gauge.calls[-1].name
    else:
        return RIGHT_END
    width = min(text_width(text=name, face=BOLD_FACE), DEST_LONGEST)
    rise = width * COS + cap_height(face=BOLD_FACE) + END_CLEAR
    return min(RIGHT_END, EDGE - rise)


def route_plan_at(
    *, gauge: RouteGauge, naming: Naming, pitch: float,
) -> GaugePlan:
    """
    Measure the route gauge at one pitch.

    Parameters
    ----------
    gauge : RouteGauge
        The gauge's content.
    naming : Naming
        The shortening rules.
    pitch : float
        Pixels between marks.

    Returns
    -------
    GaugePlan
        The measurements, with names on one line.
    """
    budget, reach = slot_budget(pitch=pitch, right_end=line_end(gauge=gauge))
    origin = left_end(
        pattern=gauge.pattern,
        start=gauge.start,
        buses=gauge.buses,
        budget=budget,
    ).marks[-1].slot
    line = route_line(
        pattern=gauge.pattern,
        start=gauge.start,
        end=gauge.end,
        calls=gauge.calls,
        buses=gauge.buses,
        budget=budget,
        room=reach - origin,
        rules=naming.rules,
    )
    return GaugePlan(
        line=line,
        pitch=pitch,
        first=FIRST_X,
        line_y=LINE_RUNS if line.runs else LINE_FREE,
        hang=0.0,
        wrap=False,
    )


def route_plan(
    *, gauge: RouteGauge, keep: NDArray[np.bool_], naming: Naming,
) -> GaugePlan:
    """
    Measure the route gauge, widening for wrapped names, then spreading.

    A line without a break spreads to fill its room, up to
    ``SPREAD_MOST`` times ``PITCH``.

    Parameters
    ----------
    gauge : RouteGauge
        The gauge's content.
    keep : NDArray[np.bool_]
        The keep-out.
    naming : Naming
        The shortening rules.

    Returns
    -------
    GaugePlan
        The measurements.
    """
    plan = route_plan_at(gauge=gauge, naming=naming, pitch=PITCH)
    if needs_two(plan=plan, keep=keep, naming=naming):
        plan = replace(
            route_plan_at(gauge=gauge, naming=naming, pitch=WIDE_PITCH),
            wrap=True,
        )
    line = plan.line
    top = line.marks[-1].slot
    if line.back is not None or line.ahead is not None or top == 0:
        return plan
    spread = min(
        (line_end(gauge=gauge) - FIRST_X) / top, SPREAD_MOST * PITCH,
    )
    return replace(plan, pitch=max(plan.pitch, spread))


def buses_in(*, marker: Marker) -> int:
    """
    Count the buses a badge stands for.

    Parameters
    ----------
    marker : Marker
        A badge, or a hollow ``+n`` count.

    Returns
    -------
    int
        ``n`` for a count, 1 for a badge.
    """
    return int(marker.route[1:]) if marker.route.startswith('+') else 1


def merged(*, first: Marker, second: Marker) -> Marker:
    """
    Fold two badges into one count midway between them.

    Parameters
    ----------
    first : Marker
        The left badge or count.
    second : Marker
        The right badge or count.

    Returns
    -------
    Marker
        A count of both, keeping a heading and liveness they share.
    """
    count = buses_in(marker=first) + buses_in(marker=second)
    return replace(
        first,
        route=f'+{count}',
        x=(first.x + second.x) / 2,
        bearing=first.bearing if first.bearing == second.bearing else None,
        live=first.live and second.live,
        age=None,
    )


def overlap(*, first: Marker, second: Marker) -> bool:
    """
    Tell whether two badges come closer than ``FOLD_GAP``.

    Parameters
    ----------
    first : Marker
        The left badge.
    second : Marker
        The right badge.

    Returns
    -------
    bool
        True when they overlap or nearly touch.
    """
    right = badge_box(route=first.route, centre=(first.x, 0))[2]
    left = badge_box(route=second.route, centre=(second.x, 0))[0]
    return left < right + FOLD_GAP


def folded(
    *, markers: Sequence[Marker],
) -> tuple[list[Marker], list[float]]:
    """
    Fold overlapping badges, whichever way they face, into counts.

    Folding repeats until no two badges overlap.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges in x order.

    Returns
    -------
    tuple[list[Marker], list[float]]
        Badges to draw, and the x of every bus folded into a count.
    """
    shown = list(markers)
    ticks: set[float] = set()
    index = 1
    while index < len(shown):
        first, second = shown[index - 1], shown[index]
        if not overlap(first=first, second=second):
            index += 1
            continue
        ticks.update(
            marker.x for marker in (first, second)
            if buses_in(marker=marker) == 1
        )
        shown[index - 1 : index + 1] = [
            merged(first=first, second=second),
        ]
        index = max(1, index - 1)
    return shown, sorted(ticks)


def bus_marker(*, plan: GaugePlan, bus: BusSlot, route: str) -> Marker:
    """
    Make the badge for a bus or a count placed on the line.

    Parameters
    ----------
    plan : GaugePlan
        The measurements.
    bus : BusSlot
        The placed bus.
    route : str
        Route label.

    Returns
    -------
    Marker
        A badge on the line; a count has a heading only when its buses
        share one.
    """
    bearing = None if bus.right is None else (
        90.0 if bus.right else 270.0
    )
    return Marker(
        route=f'+{bus.count}' if bus.count else route,
        x=slot_x(plan=plan, slot=bus.slot),
        y=float(plan.line_y),
        bearing=bearing,
        live=bool(bus.live) and not bus.count,
        pinned=True,
    )


def draw_badge(canvas: Canvas, *, marker: Marker) -> None:
    """
    Draw one badge centred on the line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    marker : Marker
        The badge.
    """
    centre = (marker.x, marker.y)
    if marker.bearing is None:
        draw_hollow(canvas, marker=marker, centre=centre)
    elif buses_in(marker=marker) > 1:
        draw_hollow(
            canvas, marker=marker, centre=centre, wedge=STRIP_WEDGE,
        )
    elif marker.live:
        draw_live(canvas, marker=marker, centre=centre, wedge=STRIP_WEDGE)
    else:
        draw_dashed(canvas, marker=marker, centre=centre, wedge=STRIP_WEDGE)


def draw_buses(canvas: Canvas, *, plan: GaugePlan, route: str) -> None:
    """
    Draw every bus's tick and badge on the line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    plan : GaugePlan
        The measurements.
    route : str
        Route label.
    """
    markers, ticks = folded(markers=[
        bus_marker(plan=plan, bus=bus, route=route)
        for bus in plan.line.buses
    ])
    y = plan.line_y
    bottom = y + BADGE_HALF_HEIGHT + TICK
    for x in ticks:
        canvas.rect(
            box=(x - TICK_WIDTH / 2, y, x + TICK_WIDTH / 2, bottom),
            grey=INK,
        )
    for marker in markers:
        draw_badge(canvas, marker=marker)


def draw_route_gauge(
    canvas: Canvas, *, gauge: RouteGauge, route: str, naming: Naming,
) -> NDArray[np.bool_]:
    """
    Plan the route gauge and draw what the board canvas holds of it.

    Parameters
    ----------
    canvas : Canvas
        The board canvas, holding the hero already.
    gauge : RouteGauge
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
    plan = route_plan(gauge=gauge, keep=keep, naming=naming)
    draw_track(canvas, plan=plan)
    for gap in (plan.line.back, plan.line.ahead):
        if gap is not None:
            draw_dots(canvas, plan=plan, gap=gap)
    draw_marks(canvas, plan=plan)
    draw_buses(canvas, plan=plan, route=route)
    draw_brackets(canvas, plan=plan, naming=naming)
    names = names_ink(
        plan=plan, lines=mark_lines(plan=plan, keep=keep, naming=naming),
    )
    marks = Canvas(smooth=True)
    draw_marks(marks, plan=plan)
    return names | counts_ink(
        plan=plan, avoid=names | (marks.grey() < 128), short=False,
    )
