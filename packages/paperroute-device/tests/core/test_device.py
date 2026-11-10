"""Tests for the device's own settings, power, keys, probe and resets."""

from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest

from paperroute_device.core.device import (
    NOTICE,
    PROBE_EVERY,
    device_status,
    on_device_settings,
    on_reset,
)
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
from paperroute_device.core.device_state import (
    ClockWait,
    DeviceState,
    KitSaved,
    clock_waiting,
    elapsed,
)
from paperroute_device.core.kit_effects import (
    ControlPower,
    ProbeDevice,
    Render,
    ResetDevice,
    SaveState,
    Stop,
)
from paperroute_device.core.kit_events import (
    DeviceChange,
    DeviceProbed,
    DeviceProbeFailed,
    PowerFailed,
    QuitRequested,
    ResetDone,
    SettingsPosted,
    Tick,
)
from paperroute_device.core.kit_state import SAVE_WAIT, KitState, with_device
from paperroute_device.core.panel_views import NoticeView
from paperroute_device.core.reducer import step
from paperroute_device_testkit.device_fakes import device_facts
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from tests.core.fake_app import (
    START,
    ZONE,
    FakeApp,
    FakeState,
    drawn,
    fresh,
    kit_step,
    renders,
)


def posted(
    *, rotate: bool | None = None, larger_text: bool | None = None,
) -> SettingsPosted:
    """
    Post device settings.

    Parameters
    ----------
    rotate : bool | None
        Rotate, or None to leave it.
    larger_text : bool | None
        Larger text, or None to leave it.

    Returns
    -------
    SettingsPosted
        The post, with no app part.
    """
    change = DeviceChange(rotate=rotate, larger_text=larger_text)
    return SettingsPosted(device=change)


def at(*, seconds: int) -> Tick:
    """
    Tick some seconds after START.

    Parameters
    ----------
    seconds : int
        Seconds.

    Returns
    -------
    Tick
        The tick.
    """
    return Tick(now=START + timedelta(seconds=seconds))


def test_settings_change_is_saved() -> None:
    """A changed setting is saved once the save delay has passed."""
    before = drawn()
    kit, effects = kit_step(kit=before, event=posted(rotate=True))
    assert effects == (Render(view=before.device.last_view, full=True),)
    _, effects = kit_step(kit=kit, event=Tick(now=START + SAVE_WAIT * 2))
    saves = [effect for effect in effects if isinstance(effect, SaveState)]
    assert len(saves) == 1
    assert saves[0].saved.device.rotate is True
    assert saves[0].saved.app == 'live'


def test_settings_change_merges() -> None:
    """Only the fields given change; an empty change changes nothing."""
    kit, effects = kit_step(kit=drawn(), event=posted(larger_text=True))
    assert not effects
    assert kit.device.settings == DeviceSettings(larger_text=True)
    unchanged, effects = kit_step(kit=kit, event=posted())
    assert not effects
    assert unchanged.device.settings == kit.device.settings


def test_on_settings_routes_every_field() -> None:
    """Each changed field lands on the settings; an empty change none."""
    quiet = Window(start=time(22), end=time(6))
    change = DeviceChange(
        quiet_mode=QuietMode.APP,
        my_hours=quiet,
        rotate=True,
        larger_text=True,
        inverted_lists=True,
        clean_after=120,
        swap_knobs=True,
    )
    device, _ = on_device_settings(
        device=DeviceState(now=START), change=change,
    )
    assert device.settings == DeviceSettings(
        quiet_mode=QuietMode.APP,
        my_hours=quiet,
        rotate=True,
        larger_text=True,
        inverted_lists=True,
        clean_after=120,
        swap_knobs=True,
    )
    same, _ = on_device_settings(
        device=DeviceState(now=START), change=DeviceChange(),
    )
    assert same.settings == DeviceSettings()


def test_rotate_change_redraws_full() -> None:
    """Turning the frame redraws in full; an unchanged rotate does not."""
    before = drawn()
    kit, effects = kit_step(kit=before, event=posted(rotate=True))
    assert effects == (Render(view=before.device.last_view, full=True),)
    _, effects = kit_step(kit=before, event=posted(rotate=False))
    assert not effects
    _, effects = kit_step(kit=kit, event=posted(rotate=True))
    assert not effects


def test_app_part_of_a_post_goes_to_the_app() -> None:
    """A post's app part reaches `App.on_event` after the device part."""
    event = SettingsPosted(device=DeviceChange(rotate=True), app='posted')
    kit, effects = kit_step(kit=drawn(), event=event)
    assert kit.app.view == 'posted'
    assert kit.device.settings.rotate
    assert effects == ('kept', Render(view='view:live', full=True))


def powering(*, action: PowerAction) -> KitState[FakeState]:
    """
    Return a drawn kit state with a power action accepted.

    Parameters
    ----------
    action : PowerAction
        The action asked for.

    Returns
    -------
    KitState[FakeState]
        The kit state after the request.
    """
    kit, _ = kit_step(kit=drawn(), event=PowerRequested(action=action))
    return kit


@pytest.mark.parametrize(
    ('action', 'title'),
    [
        (PowerAction.OFF, 'Shutting down'),
        (PowerAction.RESTART, 'Restarting'),
    ],
)
def test_power_request_saves_draws_then_powers(
    action: PowerAction, title: str,
) -> None:
    """A power request saves, draws the notice, then powers."""
    kit, effects = kit_step(kit=drawn(), event=PowerRequested(action=action))
    assert [type(effect) for effect in effects] == [
        SaveState, Render, ControlPower,
    ]
    assert effects[1] == Render(view=NoticeView(title=title), full=True)
    assert effects[2] == ControlPower(action=action)
    assert kit.device.power is action


def test_no_redraw_while_powering() -> None:
    """After a power request, nothing redraws; a quit still stops."""
    kit = powering(action=PowerAction.OFF)
    for event in (
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        Tick(now=START + timedelta(minutes=5)),
    ):
        kit, effects = kit_step(kit=kit, event=event)
        assert not [e for e in effects if isinstance(e, Render)]
    _, effects = kit_step(kit=kit, event=QuitRequested())
    assert not [e for e in effects if isinstance(e, Render)]
    assert isinstance(effects[-1], Stop)


def test_touch_while_powering_changes_nothing() -> None:
    """A touch during a power action leaves the app alone."""
    kit = powering(action=PowerAction.RESTART)
    for event in (
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        KnobPressed(knob=Knob.TOP),
        KnobsHeld(),
    ):
        after, effects = kit_step(kit=kit, event=event)
        assert after.device.panel == kit.device.panel
        assert after.app.knobs == kit.app.knobs
        assert not effects


def test_second_power_request_ignored() -> None:
    """A second request while one is under way does nothing."""
    kit = powering(action=PowerAction.OFF)
    after, effects = kit_step(
        kit=kit, event=PowerRequested(action=PowerAction.RESTART),
    )
    assert after.device == kit.device
    assert not effects


def test_power_failure_restores_the_view() -> None:
    """A failed power action clears it and draws the live view again."""
    kit = powering(action=PowerAction.RESTART)
    kit, effects = kit_step(
        kit=kit, event=PowerFailed(action=PowerAction.RESTART),
    )
    assert kit.device.power is None
    assert renders(effects=effects) == ['view:live']


def test_stale_power_failure_ignored() -> None:
    """A failure for an action not under way changes nothing."""
    kit = drawn()
    failed = PowerFailed(action=PowerAction.OFF)
    after, effects = kit_step(kit=kit, event=failed)
    assert after.device == kit.device
    assert not effects


def test_no_key_banner_and_no_polls() -> None:
    """With no key the live view warns; once set the warning goes."""
    missing = ApiKeyChanged(missing=('Test',))
    kit, effects = kit_step(kit=drawn(), event=missing)
    assert kit.device.panel.key_set is False
    assert renders(effects=effects) == [
        'view:live No Test key · set it on your phone',
    ]
    kit, effects = kit_step(kit=kit, event=ApiKeyChanged(missing=()))
    assert kit.device.panel.key_set is True
    assert renders(effects=effects) == ['view:live']


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
    kit, effects = kit_step(kit=fresh(), event=at(seconds=1))
    assert probes(effects=effects) == 1
    assert kit.device.panel.probed_at == START + timedelta(seconds=1)


def test_probe_every_thirty_seconds() -> None:
    """Later ticks probe again once PROBE_EVERY has passed."""
    assert PROBE_EVERY == timedelta(seconds=30)
    kit, _ = kit_step(kit=fresh(), event=at(seconds=0))
    kit, effects = kit_step(kit=kit, event=at(seconds=29))
    assert not probes(effects=effects)
    _, effects = kit_step(kit=kit, event=at(seconds=30))
    assert probes(effects=effects) == 1


def test_probed_facts_are_kept_with_the_key() -> None:
    """Facts are stored and their key state copied to the panel."""
    facts = device_facts(missing_keys=('Test',))
    kit, _ = kit_step(kit=drawn(), event=DeviceProbed(facts=facts))
    assert kit.device.panel.facts == facts
    assert kit.device.panel.key_set is False
    after, effects = kit_step(kit=kit, event=DeviceProbeFailed())
    assert after.device == kit.device
    assert not effects


def test_device_status() -> None:
    """The status gathers the keys and facts, and nothing of the app's."""
    kit, _ = kit_step(kit=drawn(), event=at(seconds=30))
    kit, _ = kit_step(kit=kit, event=DeviceProbed(facts=device_facts()))
    status = device_status(device=kit.device)
    assert status == DeviceStatus(missing_keys=(), facts=device_facts())


def resetting(*, kind: ResetKind) -> KitState[FakeState]:
    """
    Return a drawn kit state with a reset under way.

    Parameters
    ----------
    kind : ResetKind
        The reset asked for.

    Returns
    -------
    KitState[FakeState]
        The kit state after the reset was asked for.
    """
    kit = drawn()
    device, _ = on_reset(device=kit.device, kind=kind)
    return with_device(kit=kit, device=device)


def test_reset_draws_notice_then_resets() -> None:
    """A reset draws its notice, then resets, and saves nothing."""
    device, effects = on_reset(device=drawn().device, kind=ResetKind.SETTINGS)
    notice = NoticeView(
        title='Resetting settings',
        detail='paperroute starts again by itself',
    )
    assert effects == (
        Render(view=notice, full=True),
        ResetDevice(kind=ResetKind.SETTINGS),
    )
    assert device.panel.resetting is ResetKind.SETTINGS
    assert device.last_view == notice


def test_no_save_after_reset_asked() -> None:
    """After a reset is asked for, nothing saves; a quit still stops."""
    kit, _ = kit_step(kit=drawn(), event=posted(rotate=True))
    device, _ = on_reset(device=kit.device, kind=ResetKind.ERASE)
    kit = with_device(kit=kit, device=device)
    for seconds in (1, 10, 60):
        tick = Tick(now=START + SAVE_WAIT + timedelta(seconds=seconds))
        kit, effects = kit_step(kit=kit, event=tick)
        assert not [e for e in effects if isinstance(e, SaveState | Render)]
    _, effects = kit_step(kit=kit, event=QuitRequested())
    assert effects == (Stop(),)


def test_reset_done_stops() -> None:
    """A finished reset stops the board without saving."""
    kit, effects = kit_step(
        kit=resetting(kind=ResetKind.ERASE),
        event=ResetDone(kind=ResetKind.ERASE),
    )
    assert not kit.running
    assert effects == (Stop(),)


def test_second_reset_ignored() -> None:
    """A second reset while one is under way does nothing."""
    kit = resetting(kind=ResetKind.SETTINGS)
    after, effects = on_reset(device=kit.device, kind=ResetKind.ERASE)
    assert after is kit.device
    assert not effects


def test_reset_ignored_while_powering() -> None:
    """A reset while a power action is under way does nothing."""
    kit = powering(action=PowerAction.OFF)
    after, effects = on_reset(device=kit.device, kind=ResetKind.ERASE)
    assert after is kit.device
    assert not effects


def test_off_notice_says_shutting_down() -> None:
    """The Off notice names what is happening, not the finished state."""
    assert NOTICE[PowerAction.OFF] == 'Shutting down'


def test_clock_wait_starts_gives_up_and_sets() -> None:
    """An unsynced first tick waits; the wait gives up, then a sync sets."""
    kit, _ = kit_step(kit=fresh(), event=Tick(now=START, synced=False))
    clock = kit.device.clock
    assert clock.wait_until == START + timedelta(minutes=2)
    late = START + timedelta(minutes=2)
    kit, effects = kit_step(kit=kit, event=Tick(now=late, synced=False))
    assert kit.device.clock.gave_up
    assert probes(effects=effects) == 1
    later = late + timedelta(seconds=1)
    kit, _ = kit_step(kit=kit, event=Tick(now=later))
    assert kit.device.clock.wait_until is None
    assert kit.device.clock.set_at == later
    assert not kit.device.clock.gave_up


def test_clock_wait_only_before_the_first_frame() -> None:
    """An unsynced tick after a frame is drawn starts no wait."""
    kit, _ = kit_step(kit=drawn(), event=Tick(now=START, synced=False))
    assert kit.device.clock.wait_until is None
    assert not clock_waiting(device=kit.device)
    unknown = replace(kit.device, clock=ClockWait(known=False))
    assert clock_waiting(device=unknown)
    over = ClockWait(wait_until=START + timedelta(minutes=2), gave_up=True)
    assert not clock_waiting(device=replace(kit.device, clock=over))


QUOTA_WAIT = timedelta(minutes=10)


class WaitingApp(FakeApp):
    """The fake app holding a change of its own part for ten minutes."""

    def save_waits(
        self,
        *,
        written: object | None,
        current: object | None,
        written_at: datetime | None,
        now: datetime,
    ) -> bool:
        """
        Hold a change to the app's part until ten minutes after a write.

        Parameters
        ----------
        written : object | None
            Ignored.
        current : object | None
            Ignored.
        written_at : datetime | None
            When the kit last wrote.
        now : datetime
            The time.

        Returns
        -------
        bool
            True until `QUOTA_WAIT` after the last write.
        """
        del written, current
        return not elapsed(since=written_at, now=now, span=QUOTA_WAIT)


WAITING = WaitingApp()


def waiting_step(
    *, kit: KitState[FakeState], event: object,
) -> tuple[KitState[FakeState], tuple[object, ...]]:
    """
    Step the kit with the waiting app.

    Parameters
    ----------
    kit : KitState[FakeState]
        Kit state.
    event : object
        What happened.

    Returns
    -------
    tuple[KitState[FakeState], tuple[object, ...]]
        The next state and effects.
    """
    return step(kit=kit, app=WAITING, zone=ZONE, event=event)


def saves(*, effects: tuple[object, ...]) -> list[SaveState]:
    """
    List the saves asked for.

    Parameters
    ----------
    effects : tuple[object, ...]
        Effects.

    Returns
    -------
    list[SaveState]
        Each SaveState.
    """
    return [effect for effect in effects if isinstance(effect, SaveState)]


def test_waiting_app_holds_its_save_until_its_rule_allows() -> None:
    """An app's own change waits as `save_waits` says, then is written."""
    kit, _ = waiting_step(kit=drawn(), event='other')
    for minutes in (1, 5, 9):
        tick = Tick(now=START + timedelta(minutes=minutes))
        kit, effects = waiting_step(kit=kit, event=tick)
        assert not saves(effects=effects), minutes
    now = START + QUOTA_WAIT
    kit, effects = waiting_step(kit=kit, event=Tick(now=now))
    assert saves(effects=effects) == [
        SaveState(saved=KitSaved(device=DeviceSettings(), app='other')),
    ]
    assert kit.written_at == now
    kit, _ = waiting_step(kit=kit, event='again')
    later = Tick(now=now + timedelta(minutes=1))
    _, effects = waiting_step(kit=kit, event=later)
    assert not saves(effects=effects)


def test_device_change_saves_even_when_the_app_would_wait() -> None:
    """A device-setting change is written `SAVE_WAIT` later regardless."""
    kit, _ = waiting_step(kit=drawn(), event='other')
    kit, _ = waiting_step(kit=kit, event=posted(rotate=True))
    _, effects = waiting_step(kit=kit, event=Tick(now=START + SAVE_WAIT))
    saved = KitSaved(device=DeviceSettings(rotate=True), app='other')
    assert saves(effects=effects) == [SaveState(saved=saved)]


def test_quit_and_power_write_at_once_for_a_waiting_app() -> None:
    """A quit or a power request writes a held change without waiting."""
    kit, _ = waiting_step(kit=drawn(), event='other')
    _, effects = waiting_step(kit=kit, event=QuitRequested())
    assert saves(effects=effects)
    off = PowerRequested(action=PowerAction.OFF)
    after, effects = waiting_step(kit=kit, event=off)
    assert saves(effects=effects)
    assert after.written_at == START
