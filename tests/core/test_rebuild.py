"""Tests for when an outdated timetable is rebuilt."""

from collections.abc import Sequence
from datetime import timedelta

import pytest

from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.messages import (
    ColumnLoaded,
    Effect,
    Event,
    LoadColumn,
    RebuildTimetable,
    Tick,
    TimetableRebuilt,
    TimetableSeen,
)
from paperroute.core.rebuild import BACKOFF_MAX, is_outdated
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import Rebuild, State
from tests.core.chooser_walks import DOWN, KINDS, NEXT, OPEN
from tests.core.factories import START, pin_at

OLD = TimetableSeen(version='v', schema='4')


def pinned() -> State:
    """
    A state with one pin, drawn once.

    Returns
    -------
    State
        The state after its first tick.
    """
    saved = Saved(
        pins=(pin_at('first', x=100.0),),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
        zoom=DEFAULT_ZOOM,
    )
    state = initial_state(saved=saved, now=START, stored=True)
    return step(state, Tick(now=START))[0]


def rebuilds(*, effects: Sequence[Effect]) -> int:
    """
    Count the RebuildTimetable effects.

    Parameters
    ----------
    effects : Sequence[Effect]
        Effects.

    Returns
    -------
    int
        How many ask for a rebuild.
    """
    return sum(isinstance(effect, RebuildTimetable) for effect in effects)


def run_events(
    *, state: State, events: Sequence[Event]
) -> tuple[State, list[Effect]]:
    """
    Apply events in turn, gathering every effect.

    Parameters
    ----------
    state : State
        State before.
    events : Sequence[Event]
        Events.

    Returns
    -------
    tuple[State, list[Effect]]
        The state after and all effects.
    """
    gathered: list[Effect] = []
    for event in events:
        state, effects = step(state, event)
        gathered.extend(effects)
    return state, gathered


def failed_at(*, minutes: int) -> State:
    """
    Start a rebuild at START and fail it some minutes later.

    Parameters
    ----------
    minutes : int
        Minutes after START the failure arrives.

    Returns
    -------
    State
        The state after the failure.
    """
    later = START + timedelta(minutes=minutes)
    state, _ = run_events(
        state=pinned(),
        events=[OLD, Tick(now=later), TimetableRebuilt(ok=False)],
    )
    return state


@pytest.mark.parametrize(
    ('schema', 'expected'),
    [('4', True), ('5', False), ('6', False), (None, False), ('x', False)],
)
def test_is_outdated(schema: str | None, expected: bool) -> None:
    """Only a whole-number schema below the app's is outdated."""
    assert is_outdated(schema=schema) is expected


def test_outdated_timetable_starts_rebuild() -> None:
    """A schema-4 file asks for one rebuild and marks it running."""
    state, effects = step(pinned(), OLD)
    assert rebuilds(effects=effects) == 1
    assert state.rebuild.running and state.rebuild.outdated


def test_no_second_rebuild_while_running() -> None:
    """While a rebuild runs, neither TimetableSeen nor Ticks start another."""
    state, _ = step(pinned(), OLD)
    ticks = [
        Tick(now=START + timedelta(hours=hour))
        for hour in range(1, 11)
    ]
    _, effects = run_events(
        state=state,
        events=[TimetableSeen(version='v', schema='4'), *ticks],
    )
    assert rebuilds(effects=effects) == 0


def test_failed_rebuild_backs_off() -> None:
    """A failure waits an hour; a second failure waits two."""
    state = failed_at(minutes=0)
    _, early = step(state, Tick(now=START + timedelta(minutes=59)))
    state, late = step(state, Tick(now=START + timedelta(minutes=61)))
    assert (rebuilds(effects=early), rebuilds(effects=late)) == (0, 1)
    state, _ = step(state, TimetableRebuilt(ok=False))
    assert state.rebuild.retry_at == state.now + timedelta(hours=2)


def test_backoff_caps_at_a_day() -> None:
    """After eight failures in a row the wait is a day, no more."""
    state = failed_at(minutes=0)
    for _ in range(7):
        assert state.rebuild.retry_at is not None
        state, _ = step(state, Tick(now=state.rebuild.retry_at))
        state, _ = step(state, TimetableRebuilt(ok=False))
    assert state.rebuild.failures == 8
    assert state.rebuild.retry_at == state.now + BACKOFF_MAX


def test_rebuilt_ok_waits_before_any_retry() -> None:
    """A success on a file still on schema 4 waits an hour to try again."""
    state, _ = run_events(
        state=pinned(), events=[OLD, TimetableRebuilt(ok=True)],
    )
    _, effects = run_events(
        state=state,
        events=[
            OLD,
            Tick(now=START + timedelta(minutes=59)),
        ],
    )
    assert rebuilds(effects=effects) == 0
    assert state.rebuild.failures == 0


def test_current_schema_clears_outdated() -> None:
    """A later schema-5 file is no longer outdated."""
    state, _ = run_events(
        state=pinned(),
        events=[
            OLD,
            TimetableRebuilt(ok=True),
            TimetableSeen(version='w', schema='5'),
        ],
    )
    assert not state.rebuild.outdated


def test_chooser_works_while_outdated() -> None:
    """With the timetable outdated, picking Train loads its column."""
    walk: list[Event] = [
        OPEN, ColumnLoaded(path=(), column=KINDS), DOWN, NEXT,
    ]
    current = step(pinned(), TimetableSeen(version='v', schema='5'))[0]
    outdated = step(pinned(), OLD)[0]
    assert outdated.rebuild == Rebuild(
        outdated=True, running=True, failures=0, retry_at=None,
    )
    _, expected = run_events(state=current, events=walk)
    _, effects = run_events(state=outdated, events=walk)
    loads = [effect for effect in effects if isinstance(effect, LoadColumn)]
    assert loads
    assert loads == [
        effect for effect in expected if isinstance(effect, LoadColumn)
    ]
