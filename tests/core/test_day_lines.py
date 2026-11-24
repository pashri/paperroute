"""Tests for the day's other lines a plain pin's map draws."""

from dataclasses import replace
from datetime import date, timedelta

from paperroute_device.core.kit_events import Tick
from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import START, departure, place
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import CENTRE, PIN, on_view
from paperroute_transit.core.day_lines import (
    day_extras,
    day_request,
    line_choice,
    request_day_lines,
)
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
    after = START.replace(day=17, hour=0, minute=30)
    late = replace(
        departure(departs=after + timedelta(minutes=10)),
        service_day=date(2026, 10, 16),
    )
    state = replace(plain_on_map(departures=(late,)), now=after)
    stop_view = live_stop_view(state=state)
    request = day_request(state=state, stop_view=stop_view)
    assert request is not None
    assert state.now.date() == date(2026, 10, 17)
    assert request.day == date(2026, 10, 16)


def test_day_lines_fall_back_to_the_local_date_with_nothing_listed() -> None:
    """With no departure listed the day asked for is today's local date."""
    state = plain_on_map(departures=())
    now = START.replace(day=17, hour=0, minute=30)
    request = day_request(
        state=replace(state, now=now), stop_view=live_stop_view(state=state),
    )
    assert request is not None
    assert request.day == date(2026, 10, 17)


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


def asleep_empty() -> State:
    """
    Make a bus pin's state asleep with no day lines held.

    Returns
    -------
    State
        The state.
    """
    state = plain_on_map(departures=bus_pin())
    return replace(
        state,
        map=replace(state.map, day_lines=None),
        panel=replace(state.panel, asleep=True),
    )


def day_loads(*, effects: tuple[Effect, ...]) -> list[LoadDayLines]:
    """
    List the day line loads among some effects.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[LoadDayLines]
        The loads.
    """
    return [e for e in effects if isinstance(e, LoadDayLines)]


def test_sleeping_map_asks_for_the_day_lines_once() -> None:
    """A sleeping pin with nothing held asks once, not every tick."""
    state = asleep_empty()
    state, effects = step(state, Tick(now=state.now + timedelta(seconds=1)))
    (load,) = day_loads(effects=effects)
    assert pending(state=state) == load
    _, effects = step(state, Tick(now=state.now + timedelta(seconds=1)))
    assert day_loads(effects=effects) == []


def test_sleeping_map_asks_again_for_a_new_timetable() -> None:
    """A new timetable version while asleep asks exactly once more."""
    state = asleep_empty()
    state, _ = step(state, Tick(now=state.now + timedelta(seconds=1)))
    state = replace(state, saving=replace(state.saving, timetable='v2'))
    state, effects = step(state, Tick(now=state.now + timedelta(seconds=1)))
    (load,) = day_loads(effects=effects)
    assert load.timetable == 'v2'
    _, effects = step(state, Tick(now=state.now + timedelta(seconds=1)))
    assert day_loads(effects=effects) == []


def test_awake_map_asks_again_for_a_new_timetable() -> None:
    """A new timetable version asks exactly once more."""
    state = plain_on_map(departures=bus_pin())
    state = replace(state, saving=replace(state.saving, timetable='v2'))
    (load,) = asked(state=state)
    assert load.timetable == 'v2'


def test_awake_map_asks_again_when_the_school_setting_flips() -> None:
    """Toggling the school buses setting asks exactly once more."""
    state = plain_on_map(departures=bus_pin())
    board = state.settings.board
    flipped = replace(
        state,
        settings=replace(
            state.settings,
            board=replace(
                board,
                skip_school_buses=not board.skip_school_buses,
            ),
        ),
    )
    (load,) = asked(state=flipped)
    assert load.skip_school is not board.skip_school_buses


def other_pin_state(*, state: State) -> State:
    """
    Show another bus pin with no Destination.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        The state with the other pin shown.
    """
    other = replace(state.places.pins[0], nickname='other')
    return replace(state, places=replace(state.places, pins=(other,)))


def test_awake_map_asks_again_for_a_new_pin() -> None:
    """Changing the pin asks exactly once more, for the new pin."""
    state = other_pin_state(state=plain_on_map(departures=bus_pin()))
    (load,) = asked(state=state)
    assert load.pin.nickname == 'other'


def test_stale_day_lines_are_ignored() -> None:
    """Day lines for an old request leave the held request alone."""
    state = plain_on_map(departures=bus_pin())
    old = pending(state=state)
    state = replace(state, saving=replace(state.saving, timetable='v2'))
    state, _, _ = map_effects(
        state=state, stop_view=live_stop_view(state=state),
    )
    after, _ = step(
        state, DayLinesLoaded(request=old, departures=(extra_line(),)),
    )
    held = after.map.day_lines
    assert held is not None and held.departures is None
    assert held.request.timetable == 'v2'


def test_old_pins_extras_are_not_drawn_for_a_new_pin() -> None:
    """After a pin change the old extras go while the new load waits."""
    state = plain_on_map(departures=bus_pin())
    state, _ = step(
        state,
        DayLinesLoaded(
            request=pending(state=state), departures=(extra_line(),),
        ),
    )
    stop_view = live_stop_view(state=state)
    assert day_extras(state=state, stop_view=stop_view)
    other = other_pin_state(state=state)
    other, _, _ = map_effects(
        state=other, stop_view=live_stop_view(state=other),
    )
    assert day_extras(
        state=other, stop_view=live_stop_view(state=other),
    ) == ()


def test_previous_extras_are_drawn_while_a_new_request_loads() -> None:
    """A new request for the same pin keeps the held departures drawn."""
    state = plain_on_map(departures=bus_pin())
    state, _ = step(
        state,
        DayLinesLoaded(
            request=pending(state=state), departures=(extra_line(),),
        ),
    )
    state = replace(state, saving=replace(state.saving, timetable='v2'))
    stop_view = live_stop_view(state=state)
    state, effects = request_day_lines(state=state, stop_view=stop_view)
    assert any(isinstance(e, LoadDayLines) for e in effects)
    held = state.map.day_lines
    assert held is not None and held.departures is None
    assert day_extras(state=state, stop_view=stop_view) == (extra_line(),)


def test_line_choice_names_the_kerb_across_for_both_sides() -> None:
    """A both-sides pin's choice carries its opposite kerb; others none."""
    kerb = place('x2', name='Opposite', point=(CENTRE[0], CENTRE[1] + 37.0))
    pin = replace(PIN, destination=None, opposite=kerb)
    state, _ = on_view(view=LiveView.MAP, pin=pin, departures=bus_pin())
    shown = live_stop_view(state=state)
    assert line_choice(state=state, stop_view=shown).across == kerb
    plain, _ = on_view(
        view=LiveView.MAP, pin=replace(PIN, destination=None),
        departures=bus_pin(),
    )
    assert line_choice(
        state=plain, stop_view=live_stop_view(state=plain),
    ).across is None
