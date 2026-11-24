"""Tests for app: panel."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.panel_views import NoticeView
from paperroute_device.runtime.loop import stop
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY
from paperroute_testkit.core.kit_steps import settings_posted
from paperroute_testkit.fakes import MemoryStates
from paperroute_transit.core.cycle import CYCLE_WAIT
from paperroute_transit.core.saved import (
    BoardSettings,
    Settings,
    default_saved,
)
from paperroute_transit.core.state_views import LOADING_WAIT
from tests.app.helpers import handler_of, run_sydney
from tests.app_effects.helpers import kit_ports
from tests.app_factories import AT, ports_of


async def test_queued_knob_turns_draw_one_frame() -> None:
    """Ticks and five turns queued at once draw a single frame."""
    display = FrameRecorder()

    def hook(post: Callable[[object], None]) -> None:
        """
        Post two ticks, then five turns.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        post(Tick(now=AT))
        post(Tick(now=AT + LOADING_WAIT))
        for steps in (1, -1, 1, -1, 1):
            post(KnobTurned(knob=Knob.BOTTOM, steps=steps))

    async with asyncio.timeout(5):
        await run_sydney(
            ports=ports_of(),
            kit=kit_ports(display=display),
            post_hook=hook,
            tick_every=60.0,
            max_events=7,
        )
    assert len(display.frames) == 1


async def test_app_turns_panel_frames() -> None:
    """With rotate saved, the panel is asked to turn its frames."""
    display = FrameRecorder()
    settings = Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS),
        device=DeviceSettings(rotate=True),
    )
    saved = replace(default_saved(city=SYDNEY), settings=settings)

    def hook(post: Callable[[object], None]) -> None:
        """
        Post a tick, then one a poll cycle later.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        post(Tick(now=AT))
        post(Tick(now=AT + CYCLE_WAIT))

    async with asyncio.timeout(5):
        await run_sydney(
            ports=ports_of(),
            kit=kit_ports(display=display, states=MemoryStates(stored=saved)),
            tick_every=5.0,
            post_hook=hook,
            max_events=2,
        )
    assert display.turns[-1] is True


async def test_app_passes_clean_after_to_panel() -> None:
    """The saved cleaning count reaches the panel, and a change follows."""
    display = FrameRecorder()
    settings = Settings(
        board=BoardSettings(peak_windows=PEAK_WINDOWS),
        device=DeviceSettings(clean_after=60),
    )
    saved = replace(default_saved(city=SYDNEY), settings=settings)
    handler = handler_of(
        ports=kit_ports(display=display, states=MemoryStates(stored=saved)),
    )
    for event in (Tick(now=AT), settings_posted(clean_after=15)):
        await handler.handle(event=event)
        await handler.draw(view=NoticeView(title='Frame'))
    assert display.cleans[-2:] == [60, 15]
    await stop(tasks=handler.tasks)


async def test_app_marks_frames_quick_after_a_knob_turn() -> None:
    """A knob turn's frame is quick; a tick 5 s later is not."""
    display = FrameRecorder()
    handler = handler_of(ports=kit_ports(display=display))
    later = AT + timedelta(seconds=5)
    for event in (
        Tick(now=AT),
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        Tick(now=later),
    ):
        await handler.handle(event=event)
        await handler.draw(view=NoticeView(title='Frame'))
    assert display.quicks[-2:] == [True, False]
    await stop(tasks=handler.tasks)


async def test_app_marks_frames_asleep_in_sleep_mode() -> None:
    """Frames drawn while the screen is asleep are marked asleep."""
    display = FrameRecorder()
    for asleep in (False, True):
        handler = handler_of(ports=kit_ports(display=display))
        device = handler.kit.device
        panel = replace(device.panel, asleep=asleep)
        handler.kit = replace(handler.kit, device=replace(device, panel=panel))
        state = await handler.handle(event=Tick(now=AT))
        assert handler.outputs.pending.asleep is state.panel.asleep
        await handler.draw(view=NoticeView(title='Frame'))
        await stop(tasks=handler.tasks)
    assert display.sleeps[-2:] == [False, True]
