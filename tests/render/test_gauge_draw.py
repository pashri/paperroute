"""Tests for drawing the gauge."""

from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.core.text import PLANE
from paperroute_device.render.canvas import HEIGHT, INK, WIDTH, Canvas
from paperroute_transit.core.gauge_ends import route_line
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.stop_under import GaugeCall
from paperroute_transit.render.gauge_draw import (
    ARROW,
    COUNT_FACES,
    TAIL,
    break_span,
    count_choice,
    dot_lefts,
    draw_brackets,
    draw_track,
    names_ink,
)
from paperroute_transit.render.gauge_plan import (
    BOLD_FACE,
    FIRST_X,
    LIFT,
    LINE_FREE,
    PITCH,
    GaugePlan,
    name_start,
    slot_x,
)
from paperroute_transit.render.names import Naming
from paperroute_transit.render.strip import RIDE_WIDTH, TRACK_WIDTH
from tests.helpers import bare_pattern
from tests.render.test_chrome import Recording

PLAIN_RULES: Final[NameRules] = NameRules()


def numbered(count: int) -> tuple[GaugeCall, ...]:
    """Build `count` calls named Call 1, Call 2 and so on."""
    return tuple(
        GaugeCall(name=f'Call {n}', minutes=f':{n:02d}')
        for n in range(1, count + 1)
    )


def named_trip(*, names: tuple[str, ...]) -> TripPattern:
    """Build a trip calling at each name in turn, with no times."""
    return bare_pattern(
        route='6',
        names=names,
        stations=[f'S{index}' for index in range(len(names))],
    )


def plan_of(
    calls: tuple[GaugeCall, ...] = (),
    *,
    end: int | None = None,
    room: int = 12,
    starts: bool = False,
    rules: NameRules = PLAIN_RULES,
) -> GaugePlan:
    """Build a plan on the free line for a From and the calls after it."""
    line = route_line(
        pattern=named_trip(
            names=('Hollis St', *(call.name for call in calls)),
        ),
        start=0,
        end=None if end is None else end + 1,
        calls=calls,
        buses=(),
        budget=0,
        room=room,
        rules=rules,
    )
    return GaugePlan(
        line=replace(line, starts=starts),
        pitch=PITCH,
        first=FIRST_X,
        line_y=LINE_FREE,
        hang=0.0,
        wrap=False,
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
    plan = plan_of(numbered(6), end=2)
    ink = track_ink(plan)
    ride = int(slot_x(plan=plan, slot=1.5))
    beyond = int(slot_x(plan=plan, slot=4.5))
    assert ink[:, ride].sum() == RIDE_WIDTH
    assert ink[:, beyond].sum() == TRACK_WIDTH


def test_draw_track_draws_an_arrow_only_when_the_trip_goes_on() -> None:
    """Only a cut trip gets an arrowhead."""
    short = plan_of(numbered(2))
    cut = plan_of(numbered(20), end=10, room=3)
    for plan, arrow in ((short, False), (cut, True)):
        last = slot_x(plan=plan, slot=plan.line.marks[-1].slot)
        assert track_ink(plan)[LINE_FREE, int(last + TAIL + 4)] == arrow


def test_draw_track_has_no_tail_before_the_trips_first_stop() -> None:
    """A trip that starts has no tail on the left."""
    starts = plan_of(numbered(2), starts=True)
    assert not track_ink(starts)[LINE_FREE, int(FIRST_X) - 4]


def test_draw_track_draws_a_left_arrow_on_a_cut_line() -> None:
    """A line cut on the left ends in an arrow pointing left."""
    plan = plan_of(numbered(2))
    cut = replace(plan, line=replace(plan.line, cut=True))
    left = FIRST_X - TAIL
    tip = int(left - ARROW[0] + 2)
    assert track_ink(cut)[LINE_FREE, tip]
    assert not track_ink(plan)[LINE_FREE, tip]


def test_draw_track_dots_a_break_mark_to_mark() -> None:
    """A break is dotted between its marks only."""
    plan = plan_of(numbered(12), end=10, room=6)
    ink = track_ink(plan)
    assert plan.line.ahead is not None
    near, far = break_span(plan=plan, gap=plan.line.ahead)
    row = ink[LINE_FREE, int(near):int(far)]
    assert 0 < row.sum() < row.size
    assert not ink[LINE_FREE + 4, int(near):int(far)].any()


def test_draw_brackets_names_each_run_once() -> None:
    """A run's street is drawn once."""
    plan = plan_of(
        tuple(
            GaugeCall(name=f'Parramatta Rd At {cross} St', minutes=':01')
            for cross in ('A', 'B', 'C')
        ),
        rules=NameRules(joining=JOINING_WORDS),
    )
    canvas = Recording()
    draw_brackets(canvas, plan=plan, naming=Naming(rules=NameRules()))
    assert canvas.drawn == ['Parramatta Rd']


def test_names_ink_rises_up_and_right_of_its_mark() -> None:
    """Names rise to the right of their mark."""
    plan = plan_of()
    ink = names_ink(plan=plan, lines=(('Hollis St',),))
    rows, cols = np.nonzero(ink)
    assert ink.dtype == np.bool_
    assert rows.max() <= LINE_FREE - LIFT + 4
    assert cols.min() >= FIRST_X - 8
    assert cols.max() - cols.min() < rows.max() - rows.min()


def test_names_ink_turns_the_plane_with_the_name() -> None:
    """The aeroplane rotates with its name."""
    plan = plan_of()
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
