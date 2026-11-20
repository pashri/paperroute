"""Tests for the day's other lines a plain pin's map draws."""

from dataclasses import replace
from datetime import date, timedelta

from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import CENTRE, PIN, on_view
from paperroute_transit.core.day_lines import line_choice
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.map_requests import map_effects
from paperroute_transit.core.messages import (
    DayLinesLoaded,
    Effect,
    LoadDayLines,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    ShapesLoaded,
)
from paperroute_transit.core.model import Departure
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.sleep import pin_map
from paperroute_transit.core.state_model import RETRY_AFTER, State
from paperroute_transit.core.state_views import live_stop_view
from tests.core.test_map_requests import bus_pin


def plain_effects(
    *, departures: tuple[Departure, ...]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Start on the Map with a bus pin that has no Destination.

    Parameters
    ----------
    departures : tuple[Departure, ...]
        The departures loaded.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The state and effects after the departures load.
    """
    return on_view(
        view=LiveView.MAP,
        pin=replace(PIN, destination=None, kind=SydneyMode.BUS),
        departures=departures,
    )


def plain_on_map(*, departures: tuple[Departure, ...]) -> State:
    """
    Start on the Map with a bus pin that has no Destination.

    Parameters
    ----------
    departures : tuple[Departure, ...]
        The departures loaded.

    Returns
    -------
    State
        The state, its day lines pending.
    """
    return plain_effects(departures=departures)[0]


def pending(*, state: State) -> LoadDayLines:
    """
    Give the day line load a state waits on.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    LoadDayLines
        The pending request.
    """
    held = state.map.day_lines
    assert held is not None
    return held.request


def asked(*, state: State) -> list[LoadDayLines]:
    """
    List the day line loads a map step asks for.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    list[LoadDayLines]
        The loads.
    """
    _, effects, _ = map_effects(
        state=state, stop_view=live_stop_view(state=state),
    )
    return [e for e in effects if isinstance(e, LoadDayLines)]


def extra_line() -> Departure:
    """
    Make a 620 departure on its own shape.

    Returns
    -------
    Departure
        The departure.
    """
    return replace(
        departure(departs=START, route='620', trip_id='x'), shape_id='x:0',
    )


def test_day_lines_are_asked_once_per_pin_and_day() -> None:
    """A plain pin's map asks for its day lines once."""
    state, effects = plain_effects(departures=bus_pin())
    (load,) = [e for e in effects if isinstance(e, LoadDayLines)]
    assert pending(state=state) == load
    assert asked(state=state) == []
    assert load.skip_school is state.settings.board.skip_school_buses


def test_day_lines_are_not_asked_with_a_destination() -> None:
    """A pin with a Destination draws loaded departures only."""
    state, _ = on_view(view=LiveView.MAP, departures=bus_pin())
    assert asked(state=state) == []


def test_day_lines_are_not_asked_without_a_map() -> None:
    """A Strip with no map reads no day lines."""
    state, _ = on_view(
        view=LiveView.STRIP,
        pin=replace(PIN, destination=None),
        departures=bus_pin(),
    )
    assert asked(state=state) == []


def test_day_lines_follow_the_service_day_in_view() -> None:
    """After midnight the list's service day is the one asked for."""
    late = replace(
        departure(departs=START + timedelta(minutes=3)),
        service_day=date(2026, 10, 15),
    )
    load = pending(state=plain_on_map(departures=(late,)))
    assert load.day == date(2026, 10, 15)
    load = pending(state=plain_on_map(departures=bus_pin()))
    assert load.day == START.date()


def test_extra_lines_ask_for_their_shapes_and_patterns() -> None:
    """Loaded day lines are shaped and patterned like drawn ones."""
    state = plain_on_map(departures=bus_pin())
    load = pending(state=state)
    state, effects = step(
        state, DayLinesLoaded(request=load, departures=(extra_line(),)),
    )
    assert any(
        isinstance(e, LoadShapes) and 'x:0' in e.shape_ids for e in effects
    )
    assert any(
        isinstance(e, LoadPatterns) and 'x' in e.trip_ids for e in effects
    )
    _, _, ask = map_effects(
        state=state, stop_view=live_stop_view(state=state),
    )
    assert ask is not None and 'x' in ask.wanted


def test_failed_day_lines_are_asked_again_after_the_back_off() -> None:
    """A failed day read is asked for again once the backoff passes."""
    state = plain_on_map(departures=bus_pin())
    load = pending(state=state)
    state, _ = step(state, MapDataFailed(request=load))
    assert asked(state=state) == []
    later = replace(state, now=state.now + RETRY_AFTER + timedelta(seconds=1))
    assert asked(state=later) == [load]


def test_line_choice_takes_the_board_station() -> None:
    """A stand under a station finds its calls by the parent station."""
    state, _ = on_view(
        view=LiveView.MAP,
        pin=replace(PIN, destination=None),
        departures=bus_pin(),
        station='P',
    )
    shown = live_stop_view(state=state)
    assert line_choice(state=state, stop_view=shown).station == 'P'
    bare = replace(state, board=replace(state.board, station=''))
    assert line_choice(state=bare, stop_view=shown).station == 'C'


def test_sleep_map_draws_the_day_lines() -> None:
    """The sleep screen's map carries the day's other lines too."""
    state = plain_on_map(departures=bus_pin())
    load = pending(state=state)
    state, _ = step(
        state, DayLinesLoaded(request=load, departures=(extra_line(),)),
    )
    line = make_shape(shape_id='x:0', points=(CENTRE, (0.0, 0.0)))
    state, _ = step(state, ShapesLoaded(shapes={'x:0': line}))
    view = pin_map(state=state, stop_view=live_stop_view(state=state))
    assert 'x:0' in {item.shape_id for item in view.base.lines}


def test_loaded_extras_keep_the_shape_and_pattern_mappings() -> None:
    """A map step keeps the extras' shapes and the same mappings."""
    state = plain_on_map(departures=bus_pin())
    load = pending(state=state)
    state, _ = step(
        state, DayLinesLoaded(request=load, departures=(extra_line(),)),
    )
    line = make_shape(shape_id='x:0', points=(CENTRE, (0.0, 0.0)))
    state, _ = step(state, ShapesLoaded(shapes={'x:0': line}))
    after, _, _ = map_effects(
        state=state, stop_view=live_stop_view(state=state),
    )
    assert after.map.patterns is state.map.patterns
    assert after.map.shapes is state.map.shapes
    assert 'x:0' in after.map.shapes
