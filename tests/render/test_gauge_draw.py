"""Tests for drawing the gauge."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.core.text import PLANE
from paperroute_device.render.canvas import HEIGHT, INK, WIDTH, Canvas
from paperroute_transit.core.gauge_line import gauge_line
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.stop_under import GaugeCall, StopGauge
from paperroute_transit.render.gauge_draw import (
    COUNT_FACES,
    TAIL,
    break_span,
    count_choice,
    dot_lefts,
    draw_brackets,
    draw_bus,
    draw_gauge,
    draw_track,
    names_ink,
)
from paperroute_transit.render.gauge_plan import (
    BOLD_FACE,
    FIRST_X,
    LIFT,
    LINE_FREE,
    PITCH,
    STEM,
    TEXT_CLEAR,
    GaugePlan,
    grown,
    name_start,
    slot_x,
)
from paperroute_transit.render.names import Naming
from paperroute_transit.render.strip import RIDE_WIDTH, TRACK_WIDTH
from tests.render.test_chrome import Recording

PLAIN_RULES: Final[NameRules] = NameRules()


def numbered(count: int) -> tuple[GaugeCall, ...]:
    """Build `count` calls named Call 1, Call 2 and so on."""
    return tuple(
        GaugeCall(name=f'Call {n}', minutes=f':{n:02d}')
        for n in range(1, count + 1)
    )


def calls_named(name: str, count: int) -> tuple[GaugeCall, ...]:
    """Build `count` calls named `name` and a number."""
    return tuple(
        GaugeCall(name=f'{name} {n}', minutes=f':{n:02d}')
        for n in range(1, count + 1)
    )


def plan_of(
    gauge: StopGauge,
    *,
    reach: int = 12,
    dest_reach: int = 12,
    rules: NameRules = PLAIN_RULES,
) -> GaugePlan:
    """Build a plan for a gauge on the free line."""
    return GaugePlan(
        line=gauge_line(
            gauge=gauge, reach=reach, dest_reach=dest_reach, rules=rules,
        ),
        pitch=PITCH, first=FIRST_X, line_y=LINE_FREE, hang=0.0, wrap=False,
    )


def track_ink(plan: GaugePlan) -> NDArray[np.bool_]:
    """Draw the track alone and return its ink."""
    canvas = Canvas()
    draw_track(canvas, plan=plan)
    return canvas.grey() < 128


def test_dot_lefts_space_five_pixel_dots_evenly() -> None:
    """Dots are spaced evenly."""
    assert dot_lefts(near=100.0, far=150.0) == [103, 111, 119, 126, 134, 142]


def test_draw_track_is_thick_only_on_the_ride() -> None:
    """The ride is thicker than the thin line."""
    plan = plan_of(StopGauge(origin='F', calls=numbered(6), end=2))
    ink = track_ink(plan)
    ride = int(slot_x(plan=plan, slot=1.5))
    beyond = int(slot_x(plan=plan, slot=4.5))
    assert ink[:, ride].sum() == RIDE_WIDTH
    assert ink[:, beyond].sum() == TRACK_WIDTH


def test_draw_track_draws_an_arrow_only_when_the_trip_goes_on() -> None:
    """Only a cut trip gets an arrowhead."""
    short = plan_of(StopGauge(origin='F', calls=numbered(2)))
    cut = plan_of(StopGauge(origin='F', calls=numbered(20)), reach=3)
    for plan, arrow in ((short, False), (cut, True)):
        last = slot_x(plan=plan, slot=plan.line.marks[-1].slot)
        assert track_ink(plan)[LINE_FREE, int(last + TAIL + 4)] == arrow


def test_draw_track_has_no_tail_before_the_trips_first_stop() -> None:
    """A trip that starts has no tail on the left."""
    starts = plan_of(StopGauge(origin='F', calls=numbered(2), starts=True))
    assert not track_ink(starts)[LINE_FREE, int(FIRST_X) - 4]


def test_draw_track_dots_a_break_mark_to_mark() -> None:
    """A break is dotted between its marks only."""
    plan = plan_of(
        StopGauge(origin='F', calls=numbered(12), end=10), dest_reach=6,
    )
    ink = track_ink(plan)
    assert plan.line.ahead is not None
    near, far = break_span(plan=plan, gap=plan.line.ahead)
    row = ink[LINE_FREE, int(near):int(far)]
    assert 0 < row.sum() < row.size
    assert not ink[LINE_FREE + 4, int(near):int(far)].any()


def test_draw_bus_hangs_the_badge_under_the_line() -> None:
    """The badge hangs below the line."""
    plan = plan_of(StopGauge(
        origin='F', behind=('B',), bus=0.5, calls=numbered(2),
    ))
    canvas = Canvas()
    draw_bus(canvas, plan=plan, route='6')
    rows = np.flatnonzero((canvas.grey() < 128).any(axis=1))
    assert rows.min() >= LINE_FREE
    assert rows.max() <= LINE_FREE + STEM + 11


def test_draw_brackets_names_each_run_once() -> None:
    """A run's street is drawn once."""
    plan = plan_of(StopGauge(
        origin='Hollis St',
        calls=tuple(
            GaugeCall(name=f'Parramatta Rd At {cross} St', minutes=':01')
            for cross in ('A', 'B', 'C')
        ),
    ), rules=NameRules(joining=JOINING_WORDS))
    canvas = Recording()
    draw_brackets(canvas, plan=plan, naming=Naming(rules=NameRules()))
    assert canvas.drawn == ['Parramatta Rd']


def test_names_ink_rises_up_and_right_of_its_mark() -> None:
    """Names rise to the right of their mark."""
    plan = plan_of(StopGauge(origin='Hollis St'))
    ink = names_ink(plan=plan, lines=(('Hollis St',),))
    rows, cols = np.nonzero(ink)
    assert ink.dtype == np.bool_
    assert rows.max() <= LINE_FREE - LIFT + 4
    assert cols.min() >= FIRST_X - 8
    assert cols.max() - cols.min() < rows.max() - rows.min()


def test_names_ink_turns_the_plane_with_the_name() -> None:
    """The aeroplane rotates with its name."""
    plan = plan_of(StopGauge(origin='Hollis St'))
    plane = names_ink(plan=plan, lines=((PLANE,),))
    flat = Canvas(smooth=True)
    flat.text(
        at=name_start(x=FIRST_X, line_y=LINE_FREE, face=BOLD_FACE),
        text=PLANE,
        face=BOLD_FACE,
        grey=INK,
    )
    rows = np.flatnonzero(plane.any(axis=1))
    assert rows.size
    assert rows.max() <= LINE_FREE - LIFT + 4
    assert (plane != (flat.grey() < 128)).any()


def test_count_choice_takes_the_words_when_they_fit() -> None:
    """The words win when they fit."""
    avoid = np.zeros((HEIGHT, WIDTH), dtype=bool)
    assert count_choice(
        line_y=LINE_FREE, near=100.0, far=200.0, count=11, avoid=avoid,
    ) == ('+11 stops', COUNT_FACES[0], 0)


def test_count_choice_falls_back_to_the_bare_count() -> None:
    """The bare count is the fallback."""
    avoid = np.ones((HEIGHT, WIDTH), dtype=bool)
    avoid[:, 135:166] = False
    assert count_choice(
        line_y=LINE_FREE, near=100.0, far=200.0, count=11, avoid=avoid,
    )[0] == '+11'


def test_draw_gauge_keeps_names_clear_of_hero_text() -> None:
    """Names keep clear of the hero's ink."""
    canvas = Canvas()
    canvas.rect(box=(18, 300, 400, 360), grey=INK)
    hero = canvas.grey() < 128
    names = draw_gauge(
        canvas, gauge=StopGauge(
            origin='Leichhardt Public School, Norton Street',
            calls=calls_named('Parramatta Road Opp University Of Sydney', 6),
        ), route='438', naming=Naming(rules=NameRules()),
    )
    near = names
    for _ in range(TEXT_CLEAR - 1):
        near = grown(mask=near)
    assert names.any()
    assert not (near & hero).any()
