"""Tests for the device's own settings and power."""

from datetime import time, timedelta

import pytest

from paperroute_device.core.device_events import ApiKeyChanged, PowerRequested
from paperroute_device.core.device_model import (
    DeviceStatus,
    PowerAction,
    ResetKind,
)
from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.kit_events import QuitRequested
from paperroute_device.core.panel_views import NoticeView
from paperroute_device_testkit.device_fakes import device_facts
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY
from paperroute_testkit.core.factories import START
from paperroute_testkit.core.steps import step_untouched
from paperroute_transit.core.banners import no_key_banner
from paperroute_transit.core.device import (
    NOTICE,
    PROBE_EVERY,
    app_status,
    device_status,
    on_reset,
    on_settings,
)
from paperroute_transit.core.messages import (
    ControlPower,
    DeviceProbed,
    DeviceProbeFailed,
    FetchFeed,
    PowerFailed,
    ProbeDevice,
    RebuildTimetable,
    Render,
    ResetDevice,
    ResetDone,
    SaveState,
    SettingsChanged,
    Stop,
    Tick,
)
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
    Settings,
    default_settings,
)
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_saves import SAVE_AFTER
from paperroute_transit.core.state_views import stop_view_of
from paperroute_transit.core.stop_view import StopView
from tests.core.test_rebuild import failed_at, pinned
from tests.core.test_state import at, fresh, loaded


def test_settings_change_is_saved() -> None:
    """A changed setting is saved once the save delay has passed."""
    event = SettingsChanged(auto_rebuild=False, rotate=True)
    before = pinned()
    assert before.last_view is not None
    state, effects = step(before, event)
    assert effects == (Render(view=before.last_view, full=True),)
    _, effects = step(state, Tick(now=START + SAVE_AFTER * 2))
    saves = [effect for effect in effects if isinstance(effect, SaveState)]
    assert len(saves) == 1
    assert saves[0].saved.settings.board.auto_rebuild is False
    assert saves[0].saved.settings.device.rotate is True


def test_settings_change_merges() -> None:
    """Only the fields given change; an empty change changes nothing."""
    state, effects = step(pinned(), SettingsChanged(peak_rate=Rate.STEADY))
    assert not effects
    assert state.settings == Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS, peak_rate=Rate.STEADY),
    )
    unchanged, effects = step(state, SettingsChanged())
    assert not effects
    assert unchanged.settings == state.settings


def test_on_settings_routes_every_field() -> None:
    """Each changed field lands on its own part; an empty change none."""
    quiet = Window(start=time(22), end=time(6))
    peaks = (Window(start=time(7), end=time(9)),)
    event = SettingsChanged(
        auto_rebuild=False,
        peak_rate=Rate.STEADY,
        offpeak_rate=Rate.FAST,
        peak_windows=peaks,
        quiet_mode=QuietMode.APP,
        my_hours=quiet,
        rotate=True,
        larger_text=True,
        inverted_lists=True,
        alert_lookahead=Lookahead.DAY,
        connection_map=ConnectionMap.WHOLE,
        skip_school_buses=False,
        skip_school_on_route_pins=True,
        clean_after=120,
        swap_knobs=True,
    )
    state, _ = on_settings(state=fresh(), event=event)
    assert state.settings == Settings(
        device=DeviceSettings(
            quiet_mode=QuietMode.APP,
            my_hours=quiet,
            rotate=True,
            larger_text=True,
            inverted_lists=True,
            clean_after=120,
            swap_knobs=True,
        ),
        board=BoardSettings(
            auto_rebuild=False,
            peak_rate=Rate.STEADY,
            offpeak_rate=Rate.FAST,
            peak_windows=peaks,
            alert_lookahead=Lookahead.DAY,
            connection_map=ConnectionMap.WHOLE,
            skip_school_buses=False,
            skip_school_on_route_pins=True,
        ),
    )
    same, _ = on_settings(state=fresh(), event=SettingsChanged())
    assert same.settings == default_settings(city=SYDNEY)


def test_lookahead_change_merges() -> None:
    """A lookahead change sets it and keeps every other setting."""
    before, _ = step(pinned(), SettingsChanged(rotate=True))
    state, effects = step(
        before, SettingsChanged(alert_lookahead=Lookahead.DAY),
    )
    assert not effects
    assert state.settings == Settings(
        device=DeviceSettings(rotate=True),
        board=BoardSettings(
            peak_windows=PEAK_WINDOWS,
            alert_lookahead=Lookahead.DAY,
        ),
    )


def test_school_switch_reloads_departures() -> None:
    """Turning the school switch drops the loaded departures."""
    before = loaded(state=fresh())
    state, _ = step(before, SettingsChanged(skip_school_buses=False))
    assert state.board.loaded_until is None
    assert not state.board.loading
    assert before.board.departures
    assert not state.board.departures
    same, _ = step(before, SettingsChanged(skip_school_buses=True))
    assert same.board == before.board


def test_route_pin_school_switch_reloads_departures() -> None:
    """Turning the route-pin school switch drops the loaded departures."""
    before = loaded(state=fresh())
    state, _ = step(before, SettingsChanged(skip_school_on_route_pins=True))
    assert state.board.loaded_until is None
    assert not state.board.departures
    same, _ = step(before, SettingsChanged(skip_school_on_route_pins=False))
    assert same.board == before.board


def test_rotate_change_redraws_full() -> None:
    """Turning the frame redraws in full; an unchanged rotate does not."""
    before = pinned()
    assert before.last_view is not None
    state, effects = step(before, SettingsChanged(rotate=True))
    assert effects == (Render(view=before.last_view, full=True),)
    _, effects = step(before, SettingsChanged(rotate=False))
    assert not effects
    _, effects = step(state, SettingsChanged(rotate=True))
    assert not effects


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
        (PowerAction.OFF, 'Shutting down'),
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
    assert effects[1] == Render(view=NoticeView(title=title), full=True)
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



def test_touch_while_powering_changes_nothing() -> None:
    """A touch during a power action leaves the Live state alone."""
    state = powering(action=PowerAction.RESTART)
    for event in (
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        KnobPressed(knob=Knob.TOP),
        KnobsHeld(),
    ):
        after, effects = step_untouched(state=state, event=event)
        assert after == state
        assert not effects


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
    state, _ = step(loaded(state=fresh()), ApiKeyChanged(missing=('TfNSW',)))
    assert state.panel.key_set is False
    view = stop_view_of(state=state)
    assert view.banner == no_key_banner(name='TfNSW')
    assert view.warning
    calls = state.quota.calls
    for seconds in range(30, 150, 30):
        state, effects = step(state, at(seconds=seconds))
        assert not fetches(effects=effects), seconds
    assert state.quota.calls == calls
    state, _ = step(state, ApiKeyChanged(missing=()))
    assert not stop_view_of(state=state).warning
    _, effects = step(state, at(seconds=180))
    assert fetches(effects=effects)


def test_unknown_key_state_polls() -> None:
    """Before the key is known, the feeds are polled as before."""
    state = loaded(state=fresh())
    assert state.panel.key_set is None
    _, effects = step(state, at(seconds=30))
    assert fetches(effects=effects)


def probes(*, effects: tuple[object, ...]) -> int:
    """
    Count the ProbeDevice effects.

    Parameters
    ----------
    effects : tuple[object, ...]
        Effects.

    Returns
    -------
    int
        How many probe the device.
    """
    return sum(isinstance(effect, ProbeDevice) for effect in effects)


def test_first_tick_probes() -> None:
    """The first tick asks for the device facts."""
    state, effects = step(fresh(), at(seconds=1))
    assert probes(effects=effects) == 1
    assert state.panel.probed_at == START + timedelta(seconds=1)


def test_probe_every_thirty_seconds() -> None:
    """Later ticks probe again once PROBE_EVERY has passed."""
    assert PROBE_EVERY == timedelta(seconds=30)
    state, _ = step(fresh(), at(seconds=0))
    state, effects = step(state, at(seconds=29))
    assert not probes(effects=effects)
    _, effects = step(state, at(seconds=30))
    assert probes(effects=effects) == 1


def test_probed_facts_are_kept_with_the_key() -> None:
    """Facts are stored and their key state copied to the panel."""
    facts = device_facts(missing_keys=('TfNSW',))
    state, _ = step(loaded(state=fresh()), DeviceProbed(facts=facts))
    assert state.panel.facts == facts
    assert state.panel.key_set is False
    after, effects = step(state, DeviceProbeFailed())
    assert after == state
    assert not effects


def test_device_status() -> None:
    """The status gathers the keys and facts, and nothing of the app's."""
    state, _ = step(loaded(state=fresh()), at(seconds=30))
    state, _ = step(state, DeviceProbed(facts=device_facts()))
    status = device_status(state=state)
    assert status == DeviceStatus(missing_keys=(), facts=device_facts())


def test_app_status() -> None:
    """The app status gathers the timetable, rebuild and calls, in order."""
    state, _ = step(loaded(state=fresh()), at(seconds=30))
    status = app_status(state=state)
    assert status == {
        'timetable': state.saving.timetable,
        'schema': None,
        'rebuilding': False,
        'rebuild_failed': False,
        'calls_today': state.quota.calls,
    }
    assert list(status) == [
        'timetable', 'schema', 'rebuilding', 'rebuild_failed', 'calls_today',
    ]
    assert state.quota.calls > 0


def resetting(*, kind: ResetKind) -> State:
    """
    Return a pinned, drawn state with a reset under way.

    Parameters
    ----------
    kind : ResetKind
        The reset asked for.

    Returns
    -------
    State
        The state after the reset was asked for.
    """
    state, _ = on_reset(state=pinned(), kind=kind)
    return state


def test_reset_draws_notice_then_resets() -> None:
    """A reset draws its notice, then resets, and saves nothing."""
    state, effects = on_reset(state=pinned(), kind=ResetKind.SETTINGS)
    notice = NoticeView(
        title='Resetting settings',
        detail='paperroute starts again by itself',
    )
    assert effects == (
        Render(view=notice, full=True),
        ResetDevice(kind=ResetKind.SETTINGS),
    )
    assert state.panel.resetting is ResetKind.SETTINGS
    assert state.last_view == notice


def test_no_save_after_reset_asked() -> None:
    """After a reset is asked for, nothing saves; a quit still stops."""
    state, _ = step(pinned(), SettingsChanged(auto_rebuild=False))
    state, _ = on_reset(state=state, kind=ResetKind.ERASE)
    for seconds in (1, 10, 60):
        tick = Tick(now=START + SAVE_AFTER + timedelta(seconds=seconds))
        state, effects = step(state, tick)
        assert not [e for e in effects if isinstance(e, SaveState | Render)]
    _, effects = step(state, QuitRequested())
    assert effects == (Stop(),)


def test_reset_done_stops() -> None:
    """A finished reset stops the app without saving."""
    state = resetting(kind=ResetKind.ERASE)
    state, effects = step(state, ResetDone(kind=ResetKind.ERASE))
    assert not state.running
    assert effects == (Stop(),)


def test_second_reset_ignored() -> None:
    """A second reset while one is under way does nothing."""
    state = resetting(kind=ResetKind.SETTINGS)
    after, effects = on_reset(state=state, kind=ResetKind.ERASE)
    assert after is state
    assert not effects


def test_reset_ignored_while_powering() -> None:
    """A reset while a power action is under way does nothing."""
    state = powering(action=PowerAction.OFF)
    after, effects = on_reset(state=state, kind=ResetKind.ERASE)
    assert after is state
    assert not effects


def test_no_rebuild_while_resetting() -> None:
    """A tick during a reset starts no timetable rebuild."""
    tick = Tick(now=START + timedelta(minutes=61))
    _, due = step(failed_at(minutes=0), tick)
    state, _ = on_reset(state=failed_at(minutes=0), kind=ResetKind.ERASE)
    _, effects = step(state, tick)
    assert [type(e) for e in due].count(RebuildTimetable) == 1
    assert not [e for e in effects if isinstance(e, RebuildTimetable)]


def test_connection_map_changed() -> None:
    """A connection map change is applied to the settings."""
    state, _ = step(
        pinned(), SettingsChanged(connection_map=ConnectionMap.WHOLE),
    )
    assert state.settings.board.connection_map is ConnectionMap.WHOLE


def test_off_notice_says_shutting_down() -> None:
    """The Off notice names what is happening, not the finished state."""
    assert NOTICE[PowerAction.OFF] == 'Shutting down'
