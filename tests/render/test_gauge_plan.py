"""Tests for measuring the gauge against the hero's ink."""

import math
from dataclasses import replace
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import HEIGHT, WIDTH, text_width
from paperroute_transit.core.gauge_line import gauge_line
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.stop_under import GaugeCall, StopGauge
from paperroute_transit.render.gauge_plan import (
    BADGE_CLEAR,
    BOLD_FACE,
    EDGE,
    FIRST_X,
    LINE_FREE,
    LINE_RUNS,
    MINUTE_FACE,
    NAME_FACE,
    PITCH,
    RUN_RISE,
    WIDE_PITCH,
    badge_hang,
    badge_reach,
    cap_height,
    gauge_plan,
    keep_out,
    mark_lines,
    name_room,
    plan_at,
    reaches,
)
from paperroute_transit.render.names import Naming

PLAIN: Final[Naming] = Naming(rules=NameRules())
JOINED: Final[Naming] = Naming(rules=NameRules(joining=JOINING_WORDS))
CLEAR: Final[NDArray[np.bool_]] = keep_out(
    ink=np.zeros((HEIGHT, WIDTH), dtype=bool),
)


def numbered(count: int) -> tuple[GaugeCall, ...]:
    """Build `count` calls named Call 1, Call 2 and so on."""
    return tuple(
        GaugeCall(name=f'Call {n}', minutes=f':{n:02d}')
        for n in range(1, count + 1)
    )


def blocked(*, rows: slice) -> NDArray[np.bool_]:
    """Keep-out for hero ink across some rows."""
    ink = np.zeros((HEIGHT, WIDTH), dtype=bool)
    ink[rows, 18:400] = True
    return keep_out(ink=ink)


def test_keep_out_grows_ink_and_closes_the_right() -> None:
    """Ink grows five pixels and the right edge is shut."""
    ink = np.zeros((HEIGHT, WIDTH), dtype=bool)
    ink[100, 300] = True
    keep = keep_out(ink=ink)
    assert keep[100, 305] and keep[95, 295]
    assert not keep[100, 306] and not keep[94, 300]
    assert keep[470, 415] and not keep[470, 414]


def test_name_room_runs_to_the_right_edge_on_clear_paper() -> None:
    """On clear paper a name runs to the edge."""
    assert name_room(
        keep=CLEAR, x=300.0, line_y=LINE_FREE, face=NAME_FACE,
    ) == pytest.approx(220, abs=2)


def test_name_room_stops_short_of_hero_text() -> None:
    """Hero ink shortens the room."""
    assert name_room(
        keep=blocked(rows=slice(370, 381)), x=28.0, line_y=LINE_FREE,
        face=NAME_FACE,
    ) == pytest.approx(48, abs=2)


def test_reaches_leave_the_last_call_its_shortest_label() -> None:
    """Reaches keep room for the last call's label."""
    gauge = StopGauge(origin='F', calls=numbered(20), end=18)
    reach, dest_reach = reaches(
        gauge=gauge, first=FIRST_X, pitch=PITCH, naming=PLAIN,
    )
    assert reach == 14
    width = text_width(text='Call 19', face=BOLD_FACE)
    assert dest_reach == min(
        math.floor(
            (EDGE - width / 2 - cap_height(face=BOLD_FACE) - 2 - FIRST_X)
            / PITCH
        ),
        reach + 1,
    )


def test_gauge_plan_keeps_the_narrow_pitch_when_names_fit() -> None:
    """Names that fit keep the narrow pitch."""
    plan = gauge_plan(
        gauge=StopGauge(origin='Hollis St', calls=numbered(4)), route='606',
        keep=CLEAR, naming=PLAIN,
    )
    assert (plan.pitch, plan.wrap, plan.first, plan.line_y) == (
        PITCH, False, FIRST_X, LINE_FREE,
    )


def test_gauge_plan_widens_when_a_name_needs_two_lines() -> None:
    """A name needing two lines widens the pitch."""
    plan = gauge_plan(
        gauge=StopGauge(origin='Hollis St', calls=numbered(4)), route='606',
        keep=blocked(rows=slice(395, 401)), naming=PLAIN,
    )
    assert (plan.pitch, plan.wrap) == (WIDE_PITCH, True)


def test_gauge_plan_raises_the_line_for_a_run_and_more_under_a_badge(
) -> None:
    """A run lifts the line, and a badge over it lifts it more."""
    run = StopGauge(
        origin='Parramatta Rd Opp Univ Of Sydney',
        calls=(GaugeCall(name='Parramatta Rd At Ross St', minutes=':05'),),
    )
    assert gauge_plan(
        gauge=run, route='438', keep=CLEAR, naming=JOINED,
    ).line_y == LINE_RUNS
    under = replace(run, behind=('Parramatta Rd At Elswick St',), bus=0.5)
    assert gauge_plan(
        gauge=under, route='438', keep=CLEAR, naming=JOINED,
    ).line_y == LINE_RUNS - RUN_RISE


def test_badge_hang_clears_the_first_minute() -> None:
    """The badge hangs left to clear the first minute."""
    gauge = StopGauge(
        origin='Hollis St', behind=('Mays Hill',), bus=0.5,
        calls=(GaugeCall(name='Harris Park', minutes=':07'),),
    )
    found = gauge_line(
        gauge=gauge, reach=9, dest_reach=9, rules=NameRules(),
    )
    hang = badge_hang(line=found, pitch=PITCH, route='M52')
    right = badge_reach(route='M52')[1]
    minute = text_width(text=':07', face=MINUTE_FACE)
    assert hang > 0
    assert 0.5 * PITCH - hang + right + BADGE_CLEAR == pytest.approx(
        2 * PITCH - minute / 2,
    )
    assert badge_hang(line=found, pitch=WIDE_PITCH, route='6') == 0.0


def test_mark_lines_wrap_a_name_too_long_for_one_line() -> None:
    """A long name wraps onto two lines."""
    keep = blocked(rows=slice(265, 276))
    gauge = StopGauge(
        origin='Hollis St', calls=(GaugeCall(
            name='Cottenham Rampton Road Histon', minutes=':05',
        ),),
    )
    plan = gauge_plan(gauge=gauge, route='606', keep=keep, naming=PLAIN)
    lines = mark_lines(plan=plan, keep=keep, naming=PLAIN)
    assert (plan.pitch, plan.wrap) == (WIDE_PITCH, True)
    assert lines[0] == ('Hollis St',)
    assert lines[1] == ('Cottenham Rampton', 'Road Histon')


def test_reaches_keep_the_destination_within_one_slot_of_the_calls() -> None:
    """A short Destination label never leaves a dropped slot."""
    gauge = StopGauge(
        origin='F',
        calls=(*numbered(15), GaugeCall(name='Ryde', minutes=':16')),
        end=15,
    )
    reach, dest_reach = reaches(
        gauge=gauge, first=FIRST_X, pitch=PITCH, naming=PLAIN,
    )
    assert dest_reach <= reach + 1
    plan = plan_at(gauge=gauge, route='606', naming=PLAIN, pitch=PITCH)
    ahead = plan.line.ahead
    assert ahead is not None
    kept = [m for m in plan.line.marks if m.slot > 0]
    assert len(kept) - 1 + ahead.count == 15


def test_gauge_line_clamps_a_far_destination_reach() -> None:
    """A Destination reach past one slot beyond the calls changes nothing."""
    gauge = StopGauge(
        origin='Hollis St',
        calls=tuple(
            GaugeCall(name=f'Stop {n}', minutes=':05') for n in range(5)
        ),
        end=4,
    )
    far = gauge_line(
        gauge=gauge, reach=3, dest_reach=40, rules=NameRules(),
    )
    near = gauge_line(
        gauge=gauge, reach=3, dest_reach=4, rules=NameRules(),
    )
    assert far.marks == near.marks
