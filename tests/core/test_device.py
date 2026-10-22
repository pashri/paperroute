"""Tests for the device's own settings and power."""

from datetime import timedelta

import pytest

from paperroute.core.banners import NO_KEY
from paperroute.core.messages import (
    ApiKeyChanged,
    ControlPower,
    FetchFeed,
    Knob,
    KnobTurned,
    NoticeView,
    PowerAction,
    PowerFailed,
    PowerRequested,
    QuitRequested,
    Render,
    SaveState,
    SettingsChanged,
    Stop,
    Tick,
)
from paperroute.core.state import SAVE_AFTER, step
from paperroute.core.state_model import State
from paperroute.core.state_views import stop_view_of
from paperroute.core.stop_view import StopView
from tests.core.factories import START
from tests.core.test_rebuild import pinned
from tests.core.test_state import at, fresh, loaded


def test_settings_change_is_saved() -> None:
    """A switched-off rebuild is saved once the save delay has passed."""
    state, effects = step(pinned(), SettingsChanged(auto_rebuild=False))
    assert not effects
    _, effects = step(state, Tick(now=START + SAVE_AFTER * 2))
    saves = [effect for effect in effects if isinstance(effect, SaveState)]
    assert len(saves) == 1
    assert saves[0].saved.settings.auto_rebuild is False


def powering(*, action: PowerAction) -> State:
    """
    Return a pinned, drawn state with a power action accepted.

    Parameters
    ----------
    action : PowerAction
        The action asked for.

    Returns
    -------
    State
        The state after the request.
    """
    state, _ = step(pinned(), PowerRequested(action=action))
    return state


@pytest.mark.parametrize(
    ('action', 'title'),
    [
        (PowerAction.OFF, 'Shut down - safe to unplug'),
        (PowerAction.RESTART, 'Restarting'),
    ],
)
def test_power_request_saves_draws_then_powers(
    action: PowerAction, title: str
) -> None:
    """A power request saves, draws the notice, then powers."""
    state, effects = step(pinned(), PowerRequested(action=action))
    assert [type(effect) for effect in effects] == [
        SaveState, Render, ControlPower,
    ]
    assert effects[1] == Render(view=NoticeView(title=title))
    assert effects[2] == ControlPower(action=action)
    assert state.power is action


def test_no_redraw_while_powering() -> None:
    """After a power request, nothing redraws; a quit still stops."""
    state = powering(action=PowerAction.OFF)
    for event in (
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        Tick(now=START + timedelta(minutes=5)),
    ):
        state, effects = step(state, event)
        assert not [e for e in effects if isinstance(e, Render)]
    _, effects = step(state, QuitRequested())
    assert not [e for e in effects if isinstance(e, Render)]
    assert isinstance(effects[-1], Stop)


def test_second_power_request_ignored() -> None:
    """A second request while one is under way does nothing."""
    state = powering(action=PowerAction.OFF)
    after, effects = step(
        state, PowerRequested(action=PowerAction.RESTART),
    )
    assert after is state
    assert not effects


def test_power_failure_restores_the_view() -> None:
    """A failed power action clears it and draws the Live view again."""
    state = powering(action=PowerAction.RESTART)
    state, effects = step(state, PowerFailed(action=PowerAction.RESTART))
    assert state.power is None
    renders = [e for e in effects if isinstance(e, Render)]
    assert len(renders) == 1
    assert isinstance(renders[0].view, StopView)


def test_stale_power_failure_ignored() -> None:
    """A failure for an action not under way changes nothing."""
    state = pinned()
    after, effects = step(state, PowerFailed(action=PowerAction.OFF))
    assert after is state
    assert not effects


def fetches(*, effects: tuple[object, ...]) -> int:
    """
    Count the FetchFeed effects.

    Parameters
    ----------
    effects : tuple[object, ...]
        Effects.

    Returns
    -------
    int
        How many fetch a feed.
    """
    return sum(isinstance(effect, FetchFeed) for effect in effects)


def test_no_key_banner_and_no_polls() -> None:
    """With no key the banner warns and no feed is fetched or counted."""
    state, _ = step(loaded(state=fresh()), ApiKeyChanged(present=False))
    assert state.panel.key_set is False
    view = stop_view_of(state=state)
    assert view.banner == NO_KEY
    assert view.warning
    calls = state.quota.calls
    for seconds in range(30, 150, 30):
        state, effects = step(state, at(seconds=seconds))
        assert not fetches(effects=effects), seconds
    assert state.quota.calls == calls
    state, _ = step(state, ApiKeyChanged(present=True))
    assert not stop_view_of(state=state).warning
    _, effects = step(state, at(seconds=180))
    assert fetches(effects=effects)


def test_unknown_key_state_polls() -> None:
    """Before the key is known, the feeds are polled as before."""
    state = loaded(state=fresh())
    assert state.panel.key_set is None
    _, effects = step(state, at(seconds=30))
    assert fetches(effects=effects)
