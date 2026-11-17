"""Tests for the map's scale staying on the rule across the hold."""

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute_device.core.kit_events import Tick
from paperroute_testkit.core.factories import START
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import CENTRE, turn
from paperroute_transit.core.map_requests import map_inputs
from paperroute_transit.core.map_view import MAP_WIDTH, STRIP_MAP_WIDTH
from paperroute_transit.core.model import Departure
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.core.tiles import ZOOMS
from tests.core.test_map_requests import ALONG, along_patterns, buses_every_ten

FAR = {**ALONG, 'X': (CENTRE[0] + 1_700.0, CENTRE[1])}
FARTHER = {**ALONG, 'X': (CENTRE[0] + 4_500.0, CENTRE[1])}
WIDE = {
    **ALONG,
    'W': (CENTRE[0] - 2_400.0, CENTRE[1]),
    'X': (CENTRE[0] + 1_600.0, CENTRE[1]),
}


def rule_zoom(
    *, state: State, width: int = MAP_WIDTH, asleep: bool = False,
) -> int:
    """
    Find the zoom of a fit with no hold and no knob.

    Parameters
    ----------
    state : State
        State.
    width : int
        Map width in pixels.
    asleep : bool
        True for the sleep screen's map.

    Returns
    -------
    int
        The zoom, in metres per pixel.
    """
    unheld = replace(state, map=replace(state.map, frame_key=None, step=0))
    inputs = map_inputs(
        state=unheld,
        stop_view=live_stop_view(state=unheld),
        width=width,
        asleep=asleep,
    )
    return ZOOMS[inputs.zoom]


def drawn_zoom(*, state: State) -> int:
    """
    Find the zoom a state's map is drawn at.

    Parameters
    ----------
    state : State
        State with a frame.

    Returns
    -------
    int
        The zoom, in metres per pixel.
    """
    assert state.map.frame is not None
    return ZOOMS[state.map.frame[1]]


def held_coarser() -> tuple[State, tuple[Departure, ...]]:
    """
    Settle at 8 m/px, then let the next bus leave so 8 is held.

    Returns
    -------
    tuple[State, tuple[Departure, ...]]
        State, and the departures.
    """
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures[:1]))
    state, _ = step(
        state, along_patterns(departures=departures[1:], stations='VAX'),
    )
    state, _ = step(state, Tick(now=START + timedelta(minutes=4)))
    assert state.map.step == 0
    assert drawn_zoom(state=state) == 8 > rule_zoom(state=state)
    return state, departures


def held_finer() -> tuple[State, tuple[Departure, ...]]:
    """
    Settle at 8 m/px, then load a wider call inside the held frame.

    Returns
    -------
    tuple[State, tuple[Departure, ...]]
        State, and the departures.
    """
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures[:1]))
    state, _ = step(
        state, along_patterns(departures=departures[1:2], line=WIDE),
    )
    assert drawn_zoom(state=state) == 8 < rule_zoom(state=state)
    return state, departures


def break_off_frame(state: State, departures: tuple[Departure, ...]) -> State:
    """
    Load a call off the held frame.

    Parameters
    ----------
    state : State
        State on the Map.
    departures : tuple[Departure, ...]
        The departures.

    Returns
    -------
    State
        The state, redrawn.
    """
    state, _ = step(
        state,
        along_patterns(departures=departures[1:2], stations='VAX', line=FAR),
    )
    return state


def break_by_strip(state: State, departures: tuple[Departure, ...]) -> State:
    """
    Switch to Strip + map.

    Parameters
    ----------
    state : State
        State on the Map.
    departures : tuple[Departure, ...]
        The departures.

    Returns
    -------
    State
        The state, redrawn.
    """
    assert departures
    state, _ = step(
        replace(state, view=LiveView.STRIP_MAP),
        Tick(now=START + timedelta(minutes=5)),
    )
    return state


BREAKS: list[Callable[[State, tuple[Departure, ...]], State]] = [
    break_off_frame,
    break_by_strip,
]


def width_of(*, state: State) -> int:
    """
    Find the width of the map the view draws.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    int
        Pixels.
    """
    strip = state.view is LiveView.STRIP_MAP
    return STRIP_MAP_WIDTH if strip else MAP_WIDTH


@pytest.mark.parametrize('breaking', BREAKS)
def test_breaking_a_coarser_hold_refits_on_the_rule(
    breaking: Callable[[State, tuple[Departure, ...]], State],
) -> None:
    """Without a pin change, the refit is the rule's scale at step 0."""
    state, departures = held_coarser()
    state = breaking(state, departures)
    assert state.map.step == 0
    width = width_of(state=state)
    assert drawn_zoom(state=state) == rule_zoom(state=state, width=width)


@pytest.mark.parametrize('breaking', BREAKS)
def test_breaking_a_finer_hold_refits_on_the_rule(
    breaking: Callable[[State, tuple[Departure, ...]], State],
) -> None:
    """A held frame finer than the rule leaves the step at 0 too."""
    state, departures = held_finer()
    assert state.map.step == 0
    state = breaking(state, departures)
    assert state.map.step == 0
    width = width_of(state=state)
    assert drawn_zoom(state=state) == rule_zoom(state=state, width=width)


def test_strip_map_after_a_finer_hold_draws_at_the_rule() -> None:
    """Strip + map after the wider call draws at 8 m/px, not 5."""
    state, departures = held_finer()
    state = break_by_strip(state, departures)
    assert drawn_zoom(state=state) == rule_zoom(state=state, width=STRIP_MAP_WIDTH)


def test_sleep_after_a_coarser_hold_fits_the_rule() -> None:
    """The sleep map fits fresh and the step stays 0."""
    state, _ = held_coarser()
    asleep = replace(state, panel=replace(state.panel, asleep=True))
    asleep, _ = step(asleep, Tick(now=START + timedelta(minutes=5)))
    inputs = map_inputs(
        state=asleep, stop_view=live_stop_view(state=asleep), asleep=True,
    )
    assert asleep.map.step == 0
    assert ZOOMS[inputs.zoom] == rule_zoom(state=asleep, asleep=True)


def test_hold_then_break_cycles_never_move_the_step() -> None:
    """Hold, break, hold, break: the step stays 0 and the scale is the rule."""
    state, departures = held_coarser()
    state, _ = step(
        state,
        along_patterns(departures=departures[1:2], stations='VAX', line=FAR),
    )
    assert state.map.step == 0
    state, _ = step(state, Tick(now=START + timedelta(minutes=14)))
    assert state.map.step == 0
    state, _ = step(
        state,
        along_patterns(
            departures=departures[2:3], stations='VAX', line=FARTHER,
        ),
    )
    assert state.map.step == 0
    assert drawn_zoom(state=state) == rule_zoom(state=state)
    state, _ = step(
        replace(state, view=LiveView.STRIP_MAP),
        Tick(now=START + timedelta(minutes=15)),
    )
    assert state.map.step == 0


def test_a_departure_leaving_keeps_a_zoomed_in_frame() -> None:
    """One detent finer, a bus leaving the list leaves the frame alone."""
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures[:2]))
    state, _ = step(state, turn(steps=1))
    before = state.map.frame
    assert state.map.step == -1 and before is not None
    state, _ = step(state, Tick(now=START + timedelta(minutes=4)))
    assert state.map.frame == before


def test_knob_from_a_held_coarser_frame_moves_one_level() -> None:
    """From 8 where 5 fits, a detent goes to 5 or 14, step is vs the rule."""
    state, _ = held_coarser()
    finer, _ = step(state, turn(steps=1))
    coarser, _ = step(state, turn(steps=-1))
    assert drawn_zoom(state=finer) == 5 and finer.map.step == 0
    assert drawn_zoom(state=coarser) == 14 and coarser.map.step == 2


def test_knob_detents_add_up_to_the_step() -> None:
    """From a frame on the rule, the step equals the detents turned."""
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures))
    steps = []
    for detents in (1, 1, -1):
        state, _ = step(state, turn(steps=detents))
        steps.append(state.map.step)
    assert steps == [-1, -2, -1]


@pytest.mark.parametrize('direction', [1, -1])
def test_clamped_knob_turns_hide_no_detents(direction: int) -> None:
    """Turning past the finest or coarsest zoom adds no detents."""
    state, departures = buses_every_ten()
    state, _ = step(state, along_patterns(departures=departures))
    for _ in range(12):
        state, _ = step(state, turn(steps=direction))
    clamped = state.map.step
    assert drawn_zoom(state=state) == (3 if direction > 0 else 45)
    back, _ = step(state, turn(steps=-direction))
    assert back.map.step == clamped + direction
    assert drawn_zoom(state=back) != drawn_zoom(state=state)

