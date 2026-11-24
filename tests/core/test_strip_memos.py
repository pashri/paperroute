"""Tests for building the strip reach once per tick."""

from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from paperroute_device.core.kit_events import Tick
from paperroute_testkit.core.factories import (
    EAST,
    START,
    example_reach,
    line_pattern,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import KEY, PIN, on_view, trains
from paperroute_transit.core import strip_follow
from paperroute_transit.core.messages import StripLoaded, TimetableSeen
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.reach import Reach
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_model import State
from paperroute_transit.core.strip_follow import Steady
from paperroute_transit.core.strip_state import shown_reach


@pytest.fixture(name='builds', autouse=True)
def _builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """
    Count the times a merged run of rows is built.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patcher.

    Returns
    -------
    list[int]
        One entry per run built.
    """
    counted: list[int] = []

    def counting(**fields: object) -> Steady:
        counted.append(1)
        return Steady(**fields)  # type: ignore[arg-type]

    monkeypatch.setattr(strip_follow, 'Steady', counting)
    return counted


def strip_state(*, reach: Reach | None = None) -> State:
    """
    Make a Strip state with a reach and the next train's pattern loaded.

    Parameters
    ----------
    reach : Reach | None
        The reach, or the example one.

    Returns
    -------
    State
        State on the Strip, with six departures.
    """
    state, _ = on_view(
        view=LiveView.STRIP,
        pin=replace(PIN, destination=None),
        departures=trains(count=6),
    )
    patterns: dict[str, TripPattern | None] = {
        'train-0': line_pattern(
            trip_id='train-0', stations=EAST, minutes=5,
        ),
    }
    return replace(
        state,
        strip=replace(
            state.strip,
            reaches={KEY: example_reach() if reach is None else reach},
        ),
        map=replace(state.map, patterns=patterns),
    )


def tick(*, state: State, seconds: int = 0) -> State:
    """
    Run one tick.

    Parameters
    ----------
    state : State
        State.
    seconds : int
        Seconds after the start.

    Returns
    -------
    State
        State after the tick.
    """
    state, _ = step(state, Tick(now=START + timedelta(seconds=seconds)))
    return state


def test_followed_reach_runs_steady_once_per_tick(builds: list[int]) -> None:
    """Settling and building the view share one run of the rows."""
    tick(state=strip_state())
    assert len(builds) == 1


def test_steady_memo_holds_across_a_second_tick(builds: list[int]) -> None:
    """A tick within the same minute and poll builds nothing again."""
    state = tick(state=strip_state())
    tick(state=state, seconds=1)
    assert len(builds) == 1


def test_steady_memo_misses_after_new_departures(builds: list[int]) -> None:
    """New departures build the rows again."""
    state = tick(state=strip_state())
    state = replace(
        state, board=replace(state.board, departures=trains(count=5)),
    )
    tick(state=state)
    assert len(builds) == 2


def test_steady_memo_misses_after_a_timetable_reload(
    builds: list[int],
) -> None:
    """A reach loaded for a new timetable builds the rows again."""
    state = tick(state=strip_state())
    state, _ = step(state, TimetableSeen(version='v2'))
    state, _ = step(
        state,
        StripLoaded(key=KEY, reach=example_reach(), timetable='v2'),
    )
    tick(state=state)
    assert len(builds) == 2


def test_steady_memo_misses_after_a_city_switch(builds: list[int]) -> None:
    """The same strip in another city builds again, to different rows."""
    first = tick(state=strip_state())
    before = shown_reach(state=first)
    other = replace(
        strip_state(reach=example_reach()),
        city=replace(first.city, zone=ZoneInfo('Europe/London')),
        map=replace(first.map, patterns={
            'train-0': line_pattern(
                trip_id='train-0', stations=EAST[:2], minutes=5,
            ),
        }),
    )
    after = shown_reach(state=tick(state=other))
    assert len(builds) == 2
    assert before is not None and after is not None
    assert after.rows != before.rows
