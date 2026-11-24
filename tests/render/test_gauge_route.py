"""Tests for drawing the route gauge."""

from dataclasses import replace
from datetime import date
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import HEIGHT, WIDTH, Canvas, text_width
from paperroute_transit.core.gauge_buses import GaugeBus
from paperroute_transit.core.gauge_ends import RouteGauge
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.stop_under import GaugeCall
from paperroute_transit.render.gauge_draw import COUNT_FACES
from paperroute_transit.render.gauge_plan import (
    EDGE,
    FIRST_X,
    PITCH,
    WIDE_PITCH,
    slot_x,
)
from paperroute_transit.render.gauge_route import (
    draw_route_gauge,
    folded,
    route_plan,
)
from paperroute_transit.render.markers import badge_box
from paperroute_transit.render.names import Naming
from paperroute_transit.render.strip import RING_RADIUS

NAMING: Final[Naming] = Naming(rules=NameRules())
NONE: Final[NDArray[np.bool_]] = np.zeros((HEIGHT, WIDTH), dtype=bool)
SHORT_TOP: Final[int] = 9


def gauge_of(
    stops: int, *, end: int | None = None, buses: tuple[GaugeBus, ...] = (),
) -> RouteGauge:
    """Build a route gauge over `stops` stops from the first."""
    pattern = TripPattern(
        trip_id='t',
        route='A',
        source='s',
        shape_id=None,
        stops=tuple(
            PatternStop(
                arrives=0,
                departs=0,
                point=None,
                station=f'S{n}',
                name=f'Stop {n}',
                passes=False,
            )
            for n in range(stops)
        ),
        days=(date(2026, 11, 23),),
    )
    return RouteGauge(
        pattern=pattern,
        start=0,
        end=end,
        calls=tuple(
            GaugeCall(name=f'Stop {n}', minutes=f':{n:02d}')
            for n in range(1, stops)
        ),
        buses=buses,
    )


def bus(spot: float, *, live: bool = True) -> GaugeBus:
    """Build a bus running the hero's way."""
    return GaugeBus(spot=spot, right=True, live=live, trip_id='t')


SHORT: Final[RouteGauge] = gauge_of(SHORT_TOP + 1)
THREE_STOPS: Final[RouteGauge] = gauge_of(3)
WITH_BREAK: Final[RouteGauge] = gauge_of(40, end=39)


def badge(*, x: float, bearing: float = 90.0) -> Marker:
    """Build a live badge for route A."""
    return Marker(
        route='A', x=x, y=0.0, bearing=bearing, live=True, pinned=True,
    )


def drawn(gauge: RouteGauge) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """Draw a route gauge on a blank board; give canvas and names ink."""
    canvas = Canvas(smooth=True)
    names = draw_route_gauge(canvas, gauge=gauge, route='A', naming=NAMING)
    return canvas.grey() < 128, names


def test_route_plan_spreads_a_short_gauge_without_a_break() -> None:
    """A short gauge spreads out to fill the line."""
    plan = route_plan(gauge=SHORT, keep=NONE, naming=NAMING)
    room = (EDGE - 30 - FIRST_X) / SHORT_TOP
    assert plan.pitch == pytest.approx(min(room, 2 * PITCH))


def test_route_plan_never_spreads_past_double() -> None:
    """Spreading stops at twice the pitch."""
    plan = route_plan(gauge=THREE_STOPS, keep=NONE, naming=NAMING)
    assert plan.pitch == 2 * PITCH


def test_route_plan_does_not_spread_a_gauge_with_a_break() -> None:
    """A gauge with a break keeps its pitch."""
    plan = route_plan(gauge=WITH_BREAK, keep=NONE, naming=NAMING)
    assert plan.pitch in (PITCH, WIDE_PITCH)


def test_folded_turns_overlapping_same_way_badges_into_badge_and_count(
) -> None:
    """Overlapping badges one way become the first and a count."""
    shown, ticks = folded(
        markers=(badge(x=100), badge(x=110), badge(x=118)),
    )
    assert [marker.route for marker in shown] == ['A', '+2']
    assert ticks == [110, 118]


def test_folded_leaves_opposite_badges_unfolded() -> None:
    """Badges facing opposite ways stay apart."""
    shown, ticks = folded(
        markers=(badge(x=100), badge(x=110, bearing=270.0)),
    )
    assert [marker.route for marker in shown] == ['A', 'A']
    assert not ticks


def test_draw_route_gauge_draws_badges_on_the_line_without_a_stem(
) -> None:
    """No ink hangs below a badge on the line."""
    ink, _ = drawn(replace(SHORT, buses=(bus(2.5),)))
    plan = route_plan(gauge=SHORT, keep=NONE, naming=NAMING)
    x = slot_x(plan=plan, slot=2.5)
    bottom = int(badge_box(route='A', centre=(x, plan.line_y))[3])
    assert ink[bottom - 4, int(x)]
    assert not ink[bottom + 2 : plan.line_y + 12, int(x)].any()


def test_draw_route_gauge_draws_rings_over_badges() -> None:
    """A ring under a badge looks as it does on a bare gauge."""
    bare, _ = drawn(SHORT)
    busy, _ = drawn(replace(SHORT, buses=(bus(3.0),)))
    plan = route_plan(gauge=SHORT, keep=NONE, naming=NAMING)
    x, y = slot_x(plan=plan, slot=3), plan.line_y
    rows, cols = np.ogrid[:HEIGHT, :WIDTH]
    inside = (cols - x) ** 2 + (rows - y) ** 2 <= RING_RADIUS ** 2
    assert (bare[inside] == busy[inside]).all()


def test_draw_route_gauge_break_count_reads_stops_beside_a_badge(
) -> None:
    """The break's count keeps its words with a badge under it."""
    plan = route_plan(gauge=WITH_BREAK, keep=NONE, naming=NAMING)
    gap = plan.line.ahead
    assert gap is not None
    _, names = drawn(replace(WITH_BREAK, buses=(bus(10.0),)))
    left = int(slot_x(plan=plan, slot=gap.first))
    right = int(slot_x(plan=plan, slot=gap.last))
    cols = np.flatnonzero(
        names[plan.line_y - 20 : plan.line_y, left - 20 : right + 20].any(
            axis=0,
        ),
    )
    words = text_width(text=f'+{gap.count} stops', face=COUNT_FACES[1])
    assert cols.max() - cols.min() >= words - 3


def test_draw_route_gauge_busy_route_keeps_from_name_clear() -> None:
    """Twenty buses leave the names as they are with none."""
    busy = tuple(bus(1 + n * 0.4, live=n % 2 == 0) for n in range(20))
    _, bare = drawn(SHORT)
    _, names = drawn(replace(SHORT, buses=busy))
    assert (bare == names).all()
