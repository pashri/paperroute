"""Drawing the route gauge: the line, every bus on it, names and counts."""

import math
from collections.abc import Sequence
from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, Canvas
from paperroute_transit.core.gauge_ends import (
    CALLS_KEPT,
    RouteGauge,
    left_end,
    route_line,
)
from paperroute_transit.core.gauge_line import BusSlot
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.strip_badges import as_count
from paperroute_transit.render.gauge_draw import (
    counts_ink,
    draw_brackets,
    draw_dots,
    draw_marks,
    draw_track,
    names_ink,
)
from paperroute_transit.render.gauge_plan import (
    EDGE,
    FIRST_X,
    LINE_FREE,
    LINE_RUNS,
    PITCH,
    WIDE_PITCH,
    GaugePlan,
    keep_out,
    mark_lines,
    needs_two,
    slot_x,
)
from paperroute_transit.render.markers import (
    STRIP_WEDGE,
    badge_box,
    draw_dashed,
    draw_hollow,
    draw_live,
)
from paperroute_transit.render.names import Naming

SPREAD_MOST: Final[float] = 2.0  # times PITCH
FOLD_GAP: Final[float] = 2.0  # px between same-way badges before they fold
TICK: Final[int] = 4  # px a folded bus's tick reaches below the line
TICK_WIDTH: Final[int] = 2  # px
RIGHT_END: Final[float] = EDGE - 30  # px the last slot may reach


def slot_budget(*, pitch: float) -> tuple[int, int]:
    """
    Give the slots the left end may take and the whole line's reach.

    Parameters
    ----------
    pitch : float
        Pixels between marks.

    Returns
    -------
    tuple[int, int]
        Last slot left of the From, and the last slot of the line.
    """
    reach = math.floor((RIGHT_END - FIRST_X) / pitch)
    return reach - CALLS_KEPT - 4, reach


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
    budget, reach = slot_budget(pitch=pitch)
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
    spread = min((RIGHT_END - FIRST_X) / top, SPREAD_MOST * PITCH)
    return replace(plan, pitch=max(plan.pitch, spread))


def folded(
    *, markers: Sequence[Marker],
) -> tuple[list[Marker], list[float]]:
    """
    Fold overlapping badges facing one way into a badge and a count.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges in x order.

    Returns
    -------
    tuple[list[Marker], list[float]]
        Badges to draw, and the x of every folded bus.
    """
    heads: dict[float, tuple[int, float]] = {}
    shown: list[Marker] = []
    counts: dict[int, int] = {}
    ticks: list[float] = []
    for marker in markers:
        left, _, right, _ = badge_box(route=marker.route, centre=(marker.x, 0))
        bearing = marker.bearing
        head = None if bearing is None else heads.get(bearing)
        if bearing is not None and head is not None and (
            left < head[1] + FOLD_GAP
        ):
            counts[head[0]] = counts.get(head[0], 0) + 1
            ticks.append(marker.x)
            heads[bearing] = (head[0], max(head[1], right))
            continue
        if bearing is not None:
            heads[bearing] = (len(shown), right)
        shown.append(marker)
    return beside(shown=shown, counts=counts), ticks


def beside(*, shown: Sequence[Marker], counts: dict[int, int]) -> list[Marker]:
    """
    Add each fold's count right of its first badge.

    Parameters
    ----------
    shown : Sequence[Marker]
        Badges kept.
    counts : dict[int, int]
        Buses folded into each kept badge, by its index.

    Returns
    -------
    list[Marker]
        The badges with their counts after them.
    """
    out: list[Marker] = []
    for index, marker in enumerate(shown):
        out.append(marker)
        if index not in counts:
            continue
        count = as_count(marker=marker, count=counts[index])
        right = badge_box(route=marker.route, centre=(marker.x, 0))[2]
        half = badge_box(route=count.route, centre=(0, 0))[2]
        out.append(replace(count, x=right + FOLD_GAP + half))
    return out


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
        A badge on the line; a count has no heading.
    """
    bearing = None if bus.count or bus.right is None else (
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
    for x in ticks:
        canvas.rect(
            box=(x - TICK_WIDTH / 2, y, x + TICK_WIDTH / 2, y + TICK),
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
    draw_buses(canvas, plan=plan, route=route)
    draw_marks(canvas, plan=plan)
    for gap in (plan.line.back, plan.line.ahead):
        if gap is not None:
            draw_dots(canvas, plan=plan, gap=gap)
    draw_brackets(canvas, plan=plan, naming=naming)
    names = names_ink(
        plan=plan, lines=mark_lines(plan=plan, keep=keep, naming=naming),
    )
    marks = Canvas(smooth=True)
    draw_marks(marks, plan=plan)
    return names | counts_ink(
        plan=plan, avoid=names | (marks.grey() < 128), short=False,
    )
