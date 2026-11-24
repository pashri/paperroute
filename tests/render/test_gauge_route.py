"""Tests for drawing the route gauge."""

import math
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
    FIRST_X,
    PITCH,
    WIDE_PITCH,
    slot_x,
)
from paperroute_transit.render.gauge_route import (
    FOLD_GAP,
    GaugeBadge,
    badge_span,
    bus_marker,
    draw_badge,
    draw_route_gauge,
    folded,
    line_end,
    route_plan,
)
from paperroute_transit.render.markers import badge_box
from paperroute_transit.render.names import Naming
from paperroute_transit.render.strip import DISC_RADIUS

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


def badge(*, x: float, bearing: float = 90.0) -> GaugeBadge:
    """Build a live badge for route A."""
    return GaugeBadge(marker=Marker(
        route='A', x=x, y=0.0, bearing=bearing, live=True, pinned=True,
    ))


def drawn(gauge: RouteGauge) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """Draw a route gauge on a blank board; give canvas and names ink."""
    canvas = Canvas(smooth=True)
    names = draw_route_gauge(canvas, gauge=gauge, route='A', naming=NAMING)
    return canvas.grey() < 128, names


def test_route_plan_spreads_a_short_gauge_without_a_break() -> None:
    """A short gauge spreads out to fill the line."""
    plan = route_plan(gauge=SHORT, keep=NONE, naming=NAMING)
    room = (line_end(gauge=SHORT) - FIRST_X) / SHORT_TOP
    assert plan.pitch == pytest.approx(min(room, 2 * PITCH))


def test_route_plan_never_spreads_past_double() -> None:
    """Spreading stops at twice the pitch."""
    plan = route_plan(gauge=THREE_STOPS, keep=NONE, naming=NAMING)
    assert plan.pitch == 2 * PITCH


def test_route_plan_does_not_spread_a_gauge_with_a_break() -> None:
    """A gauge with a break keeps its pitch."""
    plan = route_plan(gauge=WITH_BREAK, keep=NONE, naming=NAMING)
    assert plan.pitch in (PITCH, WIDE_PITCH)


def test_draw_route_gauge_draws_badges_on_the_line_without_a_stem(
) -> None:
    """No ink hangs below a badge on the line."""
    ink, _ = drawn(replace(SHORT, buses=(bus(2.5),)))
    plan = route_plan(gauge=SHORT, keep=NONE, naming=NAMING)
    x = slot_x(plan=plan, slot=2.5)
    bottom = int(badge_box(route='A', centre=(x, plan.line_y))[3])
    assert ink[bottom - 4, int(x)]
    assert not ink[bottom + 2 : plan.line_y + 12, int(x)].any()


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


def test_draw_route_gauge_shows_a_folded_bus_tick_below_the_badge(
) -> None:
    """A folded bus's tick shows just under the badge it is folded into."""
    ink, _ = drawn(replace(SHORT, buses=(bus(2.0), bus(2.2))))
    plan = route_plan(gauge=SHORT, keep=NONE, naming=NAMING)
    x = slot_x(plan=plan, slot=2.2)
    bottom = round(badge_box(route='A', centre=(x, plan.line_y))[3])
    assert ink[bottom + 1 : bottom + 3, round(x)].all()


def alone(marker: Marker, *, y: int) -> NDArray[np.bool_]:
    """Draw one badge on blank paper at the line's height."""
    canvas = Canvas(smooth=True)
    draw_badge(canvas, badge=GaugeBadge(marker=replace(marker, y=float(y))))
    return canvas.grey() < 128


def inside(marker: Marker, *, y: int) -> tuple[slice, slice]:
    """The rows and columns inside a badge's outline."""
    left, top, right, bottom = badge_box(
        route=marker.route, centre=(marker.x, y),
    )
    return (
        slice(math.ceil(top) + 1, math.floor(bottom) - 1),
        slice(math.ceil(left) + 1, math.floor(right) - 1),
    )


@pytest.mark.parametrize('spot', [0.0, 3.0])
def test_draw_route_gauge_keeps_badge_text_clear_of_rings_and_the_disc(
    spot: float,
) -> None:
    """A badge on the From disc or a ring reads as it does alone."""
    gauge = replace(SHORT, buses=(bus(spot),))
    ink, _ = drawn(gauge)
    plan = route_plan(gauge=gauge, keep=NONE, naming=NAMING)
    marker = bus_marker(plan=plan, bus=plan.line.buses[0], route='A')
    rows, cols = inside(marker, y=plan.line_y)
    assert (
        ink[rows, cols] == alone(marker, y=plan.line_y)[rows, cols]
    ).all()


def test_draw_route_gauge_keeps_count_boxes_free_of_dot_ink() -> None:
    """A count in a break hides the dots behind it."""
    gauge = replace(WITH_BREAK, buses=(bus(20.0), bus(21.0), bus(22.0)))
    ink, _ = drawn(gauge)
    plan = route_plan(gauge=gauge, keep=NONE, naming=NAMING)
    count = next(slot for slot in plan.line.buses if slot.count)
    marker = bus_marker(plan=plan, bus=count, route='A')
    rows, cols = inside(marker, y=plan.line_y)
    assert (
        ink[rows, cols] == alone(marker, y=plan.line_y)[rows, cols]
    ).all()


def test_folded_merges_overlapping_badges_into_one_count() -> None:
    """Overlapping badges either way become one mixed count."""
    shown, ticks = folded(markers=(
        badge(x=100), badge(x=110, bearing=270.0), badge(x=118),
    ))
    assert [
        (item.marker.route, item.marker.bearing, item.mixed)
        for item in shown
    ] == [('+3', None, True)]
    assert ticks == [100, 110, 118]


def test_folded_keeps_a_shared_heading_on_a_count() -> None:
    """A count of badges facing one way keeps their heading."""
    shown, _ = folded(markers=(badge(x=100), badge(x=110)))
    assert [
        (item.marker.route, item.marker.bearing, item.mixed)
        for item in shown
    ] == [('+2', 90.0, False)]


def test_folded_keeps_a_merge_with_a_mixed_count_mixed() -> None:
    """A mixed count stays mixed after taking in a one-way badge."""
    mixed = GaugeBadge(
        marker=replace(badge(x=100).marker, route='+2', bearing=None),
        mixed=True,
    )
    shown, _ = folded(markers=(mixed, badge(x=112)))
    assert [(item.marker.route, item.mixed) for item in shown] == [
        ('+3', True),
    ]


def test_folded_checks_overlap_with_the_wedges() -> None:
    """Badges whose boxes clear but whose wedges meet fold."""
    width = badge_box(route='A', centre=(0, 0))[2] * 2
    shown, _ = folded(markers=(
        badge(x=100), badge(x=100 + width + FOLD_GAP + 4),
    ))
    assert [item.marker.route for item in shown] == ['+2']


def test_folded_leaves_no_badges_overlapping() -> None:
    """No two badges drawn overlap, wedges included."""
    shown, _ = folded(markers=tuple(
        badge(x=60 + 13 * n, bearing=90.0 if n % 3 else 270.0)
        for n in range(20)
    ))
    spans = sorted(badge_span(badge=item) for item in shown)
    assert all(
        after[0] >= before[1] + FOLD_GAP
        for before, after in zip(spans, spans[1:])
    )


def test_folded_leaves_badges_apart_unfolded() -> None:
    """Badges clear of each other stay as they are."""
    shown, ticks = folded(markers=(badge(x=100), badge(x=200)))
    assert [item.marker.route for item in shown] == ['A', 'A']
    assert not ticks


def count_ink(*, mixed: bool) -> tuple[bool, bool]:
    """Draw a +3 count facing right; tell if ink is left and right of it."""
    marker = Marker(
        route='+3', x=200.0, y=100.0, bearing=None if mixed else 90.0,
        live=False, pinned=True,
    )
    canvas = Canvas(smooth=True)
    draw_badge(canvas, badge=GaugeBadge(marker=marker, mixed=mixed))
    ink = canvas.grey() < 128
    left, _, right, _ = badge_box(route='+3', centre=(200.0, 100.0))
    return (
        bool(ink[100, math.floor(left) - 8 : math.floor(left) - 2].any()),
        bool(ink[100, math.ceil(right) + 2 : math.ceil(right) + 8].any()),
    )


def test_draw_badge_draws_a_wedge_each_side_of_a_mixed_count() -> None:
    """A count mixing directions points both ways."""
    assert count_ink(mixed=True) == (True, True)


def test_draw_badge_draws_one_wedge_on_a_one_way_count() -> None:
    """A count of buses one way points only that way."""
    assert count_ink(mixed=False) == (False, True)


@pytest.mark.parametrize('spots', [(30.0,), (30.0, 35.0)])
def test_draw_route_gauge_keeps_buses_past_the_arrow_off_the_last_disc(
    spots: tuple[float, ...],
) -> None:
    """Buses past the right arrow leave the last mark as it is bare."""
    gauge = gauge_of(40, end=20)
    busy = replace(gauge, buses=tuple(bus(spot) for spot in spots))

    def disc(shown: RouteGauge) -> NDArray[np.bool_]:
        """The ink within the last mark's disc."""
        plan = route_plan(gauge=shown, keep=NONE, naming=NAMING)
        assert plan.line.arrow
        x = round(slot_x(plan=plan, slot=plan.line.marks[-1].slot))
        y = plan.line_y
        size = int(DISC_RADIUS)
        rows, cols = np.ogrid[-size : size + 1, -size : size + 1]
        inside = cols ** 2 + rows ** 2 <= size ** 2
        ink = drawn(shown)[0][y - size : y + size + 1, x - size : x + size + 1]
        return np.asarray(ink[inside], dtype=bool)

    assert (disc(busy) == disc(gauge)).all()


def test_folded_puts_a_count_at_the_mean_of_its_buses() -> None:
    """A count of three sits at the mean x of the three, not a midpoint."""
    shown, _ = folded(markers=(badge(x=100), badge(x=110), badge(x=118)))
    assert shown[0].marker.x == pytest.approx((100 + 110 + 118) / 3)
