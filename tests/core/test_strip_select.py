"""Tests for picking a train in the Strip's panel with the bottom knob."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from paperroute_device.core.kit_events import Tick
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
)
from paperroute_testkit.core.factories import START, line_pattern, live, train
from paperroute_transit.core.cycle import cycle_held
from paperroute_transit.core.messages import Event
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.core.strip_follow import origin_of
from paperroute_transit.core.strip_model import (
    StripInputs,
    StripView,
    trip_day,
)
from paperroute_transit.core.strip_select import panel_trains
from paperroute_transit.core.strip_view import build_strip_view, marks_of
from tests.core.test_state import check_selection
from tests.core.test_strip_follow import (
    ALL_STOPS,
    carrying,
    followed,
    line_state,
    parramatta,
    row_times,
)

UP: Final[KnobHeldTurned] = KnobHeldTurned(knob=Knob.BOTTOM, steps=-1)
DOWN: Final[KnobHeldTurned] = KnobHeldTurned(knob=Knob.BOTTOM, steps=1)
KINDS: Final[tuple[str, ...]] = (
    'tick', 'held', 'held', 'turn', 'press', 'hold',
)


def picked(*, inputs: StripInputs, trip_id: str) -> StripInputs:
    """
    Pick a train by its trip ID.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.
    trip_id : str
        The trip to pick.

    Returns
    -------
    StripInputs
        The inputs with that train picked.
    """
    day = inputs.departures[0].departure.service_day
    return replace(inputs, selected=(trip_id, day))


def selected_trip(*, state: State) -> str | None:
    """
    Read the picked trip ID.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str | None
        The trip ID, or None.
    """
    selected = state.strip.selected
    return None if selected is None else selected[0]


def stepped(*, state: State, events: list[Event]) -> State:
    """
    Apply events in turn.

    Parameters
    ----------
    state : State
        State.
    events : list[Event]
        Events.

    Returns
    -------
    State
        The state after them all.
    """
    for event in events:
        state, _ = step(state, event)
    return state


def test_times_follow_the_picked_all_stops_train() -> None:
    """Picking the all-stops train times every row, with the express next."""
    inputs = parramatta(express=1, stopping=3)
    found = row_times(inputs=picked(inputs=inputs, trip_id='y'))
    assert all(time for _, time in found[:9])
    assert found[6] == ('SF', '8:24')


def test_times_of_the_picked_express_are_blank_where_it_skips() -> None:
    """Picking the express is what the next train shows: skips blank."""
    inputs = parramatta(express=3, stopping=1)
    assert row_times(inputs=picked(inputs=inputs, trip_id='x'))[:9] == [
        ('HP', ''), ('GR', ''), ('AU', ''), ('LI', ''), ('FL', ''),
        ('HB', ''), ('SF', '8:06'), ('RF', '8:09'), ('CE', '8:12'),
    ]


def test_rows_stay_put_whichever_train_is_picked() -> None:
    """The layout is the steady union, whatever is picked."""
    inputs = parramatta(express=1, stopping=3)
    plain = followed(inputs=inputs).reach
    chosen = followed(inputs=picked(inputs=inputs, trip_id='y')).reach
    assert plain == chosen


def test_a_picked_train_off_the_followed_ones_is_timed_by_station() -> None:
    """A train short of the Destination is timed where it stops."""
    inputs = parramatta(express=1, stopping=3)
    inputs = replace(
        inputs,
        departures=(
            *inputs.departures, live(item=train(minutes=2, trip_id='z')),
        ),
        patterns={
            **inputs.patterns,
            'z': line_pattern(
                trip_id='z', stations=('PA', 'GR', 'HB'), minutes=2,
            ),
        },
    )
    found = row_times(inputs=picked(inputs=inputs, trip_id='z'))
    assert [pair for pair in found if pair[1]] == [
        ('GR', '8:05'), ('HB', '8:08'),
    ]


def test_an_unknown_pick_shows_the_next_train() -> None:
    """A pick no longer loaded falls back to the next train's times."""
    inputs = parramatta(express=1, stopping=3)
    assert row_times(
        inputs=picked(inputs=inputs, trip_id='gone'),
    ) == row_times(inputs=inputs)


def test_the_note_is_the_picked_trains_own() -> None:
    """The picked train's change shows, not the next train's."""
    inputs = parramatta(express=1, stopping=3)
    express, stopping = inputs.departures
    inputs = replace(
        inputs,
        departures=(express, carrying(item=stopping)),
        patterns={
            **inputs.patterns,
            'y': line_pattern(trip_id='y', stations=ALL_STOPS[:-1], minutes=3),
        },
    )
    plain = followed(inputs=inputs)
    assert plain.reach is not None
    assert marks_of(inputs=plain, reach=plain.reach).note is None
    chosen = followed(inputs=picked(inputs=inputs, trip_id='y'))
    reach = chosen.reach
    assert reach is not None
    note = marks_of(inputs=chosen, reach=reach).note
    assert note is not None and reach.rows[note[0]].place.stop_id == 'CE'


def test_the_panel_marks_the_picked_row() -> None:
    """The panel's picked row is the picked train's; none with no pick."""
    inputs = parramatta(express=1, stopping=3)
    assert build_strip_view(inputs=inputs).panel.selected is None
    chosen = build_strip_view(inputs=picked(inputs=inputs, trip_id='y'))
    assert chosen.panel.selected == 1
    picking = replace(picked(inputs=inputs, trip_id='y'), picking='TO')
    assert build_strip_view(inputs=picking).panel.selected is None


def test_hold_and_turn_picks_down_the_panel() -> None:
    """The first turn picks the first row, then each turn the next."""
    state = line_state(scroll=0)
    state = stepped(state=state, events=[DOWN])
    assert selected_trip(state=state) == 'train-0'
    state = stepped(state=state, events=[DOWN])
    assert selected_trip(state=state) == 'train-1'
    view = state.last_view
    assert isinstance(view, StripView) and view.panel.selected == 1


def test_turning_past_the_last_row_holds_there() -> None:
    """A pick past the panel's last row stays on it."""
    state = line_state(scroll=0)
    trains = panel_trains(stop_view=live_stop_view(state=state))
    state = stepped(
        state=state, events=[KnobHeldTurned(knob=Knob.BOTTOM, steps=20)],
    )
    assert selected_trip(state=state) == trains[-1].trip_id


def test_turning_back_before_the_first_row_is_the_next_train() -> None:
    """Back past the first row there is no pick."""
    state = line_state(scroll=0)
    state = stepped(state=state, events=[DOWN, DOWN, UP])
    assert selected_trip(state=state) == 'train-0'
    state = stepped(state=state, events=[UP])
    assert state.strip.selected is None
    assert stepped(state=state, events=[UP]).strip.selected is None


def test_the_pick_stays_after_letting_go() -> None:
    """With no hold after a turn, ticks keep the pick and the pin."""
    state = line_state(scroll=0)
    state = stepped(
        state=state,
        events=[DOWN, DOWN, Tick(now=START + timedelta(minutes=1))],
    )
    assert selected_trip(state=state) == 'train-1'
    assert state.places.pins


def test_a_hold_without_a_turn_still_unpins_and_drops_the_pick() -> None:
    """A hold toggles the pin, which drops the pick."""
    state = line_state(scroll=0)
    before = state.places
    state = stepped(state=state, events=[DOWN, KnobHeld(knob=Knob.BOTTOM)])
    assert state.places != before
    assert state.strip.selected is None


def test_the_pick_goes_when_its_train_leaves() -> None:
    """Once the picked train has left the From the next train shows."""
    state = line_state(scroll=0)
    state = stepped(state=state, events=[DOWN, DOWN])
    assert selected_trip(state=state) == 'train-1'
    state = stepped(
        state=state, events=[Tick(now=START + timedelta(minutes=11))],
    )
    assert state.strip.selected is None


def test_the_pick_goes_on_a_view_change_or_the_chooser() -> None:
    """Moving view or opening the chooser drops the pick."""
    state = stepped(state=line_state(scroll=0), events=[DOWN])
    moved = stepped(state=state, events=[KnobPressed(knob=Knob.BOTTOM)])
    assert moved.view is not LiveView.STRIP
    assert moved.strip.selected is None
    opened = stepped(state=state, events=[KnobPressed(knob=Knob.TOP)])
    assert opened.chooser is not None
    assert opened.strip.selected is None


def test_a_plain_turn_scrolls_and_keeps_the_pick() -> None:
    """Turning without holding scrolls, the pick kept."""
    state = stepped(state=line_state(scroll=0), events=[DOWN])
    turned = stepped(
        state=state, events=[KnobTurned(knob=Knob.BOTTOM, steps=1)],
    )
    assert turned.strip.selected == state.strip.selected


def test_hold_and_turn_does_nothing_off_the_strip() -> None:
    """On Strip + map, the top knob or in the chooser nothing is picked."""
    state = line_state(scroll=0)
    pane = replace(state, view=LiveView.STRIP_MAP)
    assert step(pane, DOWN) == (pane, ())
    top = KnobHeldTurned(knob=Knob.TOP, steps=1)
    assert step(state, top) == (state, ())
    opened = stepped(state=state, events=[KnobPressed(knob=Knob.TOP)])
    assert step(opened, DOWN) == (opened, ())


@st.composite
def pick_events(draw: st.DrawFn) -> list[Event]:
    """
    Draw knob events and ticks on the Strip.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    list[Event]
        Turns while held, plain turns, presses, holds and ticks.
    """
    events: list[Event] = []
    minutes = 0
    for kind in draw(st.lists(st.sampled_from(KINDS), max_size=30)):
        knob = draw(st.sampled_from(list(Knob)))
        steps = draw(st.integers(-6, 6))
        if kind == 'tick':
            minutes += draw(st.integers(0, 4))
            events.append(Tick(now=START + timedelta(minutes=minutes)))
        elif kind == 'held':
            events.append(KnobHeldTurned(knob=Knob.BOTTOM, steps=steps))
        elif kind == 'turn':
            events.append(KnobTurned(knob=knob, steps=steps))
        elif kind == 'press':
            events.append(KnobPressed(knob=knob))
        else:
            events.append(KnobHeld(knob=knob))
    return events


@given(events=pick_events())
def test_pick_invariants(events: list[Event]) -> None:
    """The pick is always a panel train on the Strip, and nothing saved
    changes for a turn while held; the panel marks it once drawn."""
    state = line_state(scroll=0)
    for event in events:
        before = state
        state, _ = step(before, event)
        check_selection(before=before, after=state, event=event)
        view = state.last_view
        drawn = not cycle_held(state=state)
        if drawn and isinstance(view, StripView) and state.chooser is None:
            trains = panel_trains(stop_view=live_stop_view(state=state))
            index = view.panel.selected
            assert (index is None) is (state.strip.selected is None)
            if index is not None:
                assert trip_day(departure=trains[index]) == (
                    state.strip.selected
                )


def test_the_from_shows_the_picked_trains_departure() -> None:
    """The From's time is the picked train's, else the next train's."""
    inputs = parramatta(express=1, stopping=3)
    plain = followed(inputs=inputs)
    chosen = followed(inputs=picked(inputs=inputs, trip_id='y'))
    for laid, time in ((plain, '8:01'), (chosen, '8:03')):
        reach = laid.reach
        assert reach is not None
        origin = origin_of(reach=reach)
        assert origin is not None
        assert marks_of(inputs=laid, reach=reach).times[origin] == time
