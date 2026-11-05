"""Tests for the full-refresh schedule."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute.core.device_model import ResetKind
from paperroute.core.messages import (
    Effect,
    Knob,
    KnobHeld,
    PowerAction,
    Render,
    Tick,
)
from paperroute.core.panel_views import SleepView
from paperroute.core.refresh import Refresh, quick_now
from paperroute.core.saved import LiveView, Rate, Settings
from paperroute.core.state import step
from paperroute.core.state_model import State
from tests.core.factories import START
from tests.core.test_state import PRESS, effects_of, fresh, loaded

IGNORED = KnobHeld(knob=Knob.TOP)


def drawn(
    *,
    last_full: datetime,
    touched_at: datetime | None = None,
    settings: Settings | None = None,
) -> State:
    """
    Make a state with a view drawn and a schedule set.

    Parameters
    ----------
    last_full : datetime
        The last full refresh.
    touched_at : datetime | None
        The last knob touch.
    settings : Settings | None
        The settings; the defaults when None.

    Returns
    -------
    State
        The state at `START`.
    """
    state = step(loaded(state=fresh()), Tick(now=START))[0]
    return replace(
        state,
        settings=settings or state.settings,
        refresh=Refresh(last_full=last_full, touched_at=touched_at),
    )


def fulls(*, effects: tuple[Effect, ...]) -> list[Render]:
    """
    Pick the full renders.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[Render]
        The full Render effects.
    """
    return [
        render
        for render in effects_of(effects=effects, kind=Render)
        if render.full
    ]


def ticked(*, state: State, after: timedelta) -> tuple[State, list[Render]]:
    """
    Tick a span after `START`.

    Parameters
    ----------
    state : State
        State.
    after : timedelta
        The span.

    Returns
    -------
    tuple[State, list[Render]]
        State and its full renders.
    """
    state, effects = step(state, Tick(now=START + after))
    return state, fulls(effects=effects)


def test_first_frame_is_full() -> None:
    """The first view drawn is a full refresh."""
    state, effects = step(fresh(), Tick(now=START))
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full
    assert state.refresh.last_full == START


def test_full_after_interval_when_idle() -> None:
    """At peak, a full refresh follows 5 minutes on with idle knobs."""
    state = drawn(
        last_full=START - timedelta(minutes=5),
        touched_at=START - timedelta(seconds=25),
    )
    after, renders = ticked(state=state, after=timedelta())
    assert [(render.view, render.full) for render in renders] == [
        (state.last_view, True),
    ]
    assert after.refresh.last_full == START


def test_full_waits_for_idle() -> None:
    """A due refresh waits until the knobs are 20 s idle."""
    touch = START - timedelta(seconds=10)
    state = drawn(
        last_full=START - timedelta(minutes=5), touched_at=touch,
    )
    state, renders = ticked(state=state, after=timedelta())
    assert not renders
    _, renders = ticked(state=state, after=timedelta(seconds=10))
    assert len(renders) == 1


def test_full_at_most_two_minutes_late() -> None:
    """Touches every 5 s hold a refresh only until 2 minutes late."""
    state = drawn(
        last_full=START - timedelta(minutes=7, seconds=-5),
        touched_at=START - timedelta(seconds=5),
    )
    state, renders = ticked(state=state, after=timedelta())
    assert not renders
    state = replace(
        state, refresh=replace(state.refresh, touched_at=START),
    )
    _, renders = ticked(state=state, after=timedelta(seconds=5))
    assert len(renders) == 1


def test_off_peak_full_every_fifteen() -> None:
    """Off-peak at the slow rate, a full refresh comes every 15 minutes."""
    state = drawn(
        last_full=START,
        settings=Settings(peak_windows=(), offpeak_rate=Rate.SLOW),
    )
    state, renders = ticked(state=state, after=timedelta(minutes=10))
    assert not renders
    _, renders = ticked(state=state, after=timedelta(minutes=15))
    assert len(renders) == 1


def test_map_change_is_full() -> None:
    """Into or out of a map view is full; between map views is partial."""
    state = drawn(last_full=START)
    state, effects = step(state, PRESS)
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full
    state, effects = step(
        replace(state, view=LiveView.STRIP_MAP), Tick(now=START),
    )
    [render] = effects_of(effects=effects, kind=Render)
    assert not render.full
    _, effects = step(
        replace(state, view=LiveView.STRIP), Tick(now=START),
    )
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full


def test_touch_is_noted() -> None:
    """A knob event notes its time, even one that is ignored."""
    state = drawn(last_full=START)
    for event in (PRESS, IGNORED):
        after, _ = step(state, event)
        assert after.refresh.touched_at == START


def test_waking_is_full() -> None:
    """The first frame after the sleep screen is a full refresh."""
    state = drawn(last_full=START)
    asleep, _ = ticked(
        state=replace(state, panel=replace(state.panel, asleep=True)),
        after=timedelta(),
    )
    _, effects = step(asleep, PRESS)
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full
    assert not isinstance(render.view, SleepView)


def test_no_full_while_asleep_or_powering() -> None:
    """No full refresh is scheduled asleep, powering off or resetting."""
    state = drawn(last_full=START - timedelta(minutes=20))
    asleep, _ = ticked(
        state=replace(state, panel=replace(state.panel, asleep=True)),
        after=timedelta(),
    )
    powering = replace(state, power=PowerAction.OFF)
    resetting = replace(
        state, panel=replace(state.panel, resetting=ResetKind.SETTINGS),
    )
    for held in (asleep, powering, resetting):
        _, renders = ticked(state=held, after=timedelta(minutes=10))
        assert not renders


def test_quick_now_lasts_three_seconds() -> None:
    """A knob touched 2.9 s ago is quick; at 3 s or never it is not."""
    base = drawn(last_full=START)
    for age, quick in ((2.9, True), (3.0, False)):
        touched = START - timedelta(seconds=age)
        refresh = replace(base.refresh, touched_at=touched)
        state = replace(base, now=START, refresh=refresh)
        assert quick_now(state=state) is quick
    assert quick_now(state=base) is False
