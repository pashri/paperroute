"""Tests for where the kit's effects draw and write."""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.device_model import PowerAction, ResetKind
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import (
    ControlPower,
    Render,
    ResetDevice,
    SaveState,
    Stop,
)
from paperroute_device.core.kit_events import PowerFailed
from paperroute_device.core.panel_views import (
    MenuView,
    NoticeView,
    PlainSleepView,
)
from paperroute_device.render.notice import draw_notice_view
from paperroute_device.render.panel import draw_panel_view
from paperroute_device.render.sleep import draw_plain_sleep
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.outputs import (
    Outputs,
    carry_out,
    draw_pending,
    finished,
)
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.saver import StateSaver
from paperroute_device.runtime.timings import KitTimings
from paperroute_device_testkit.device_fakes import (
    FakePower,
    FixedClock,
    RecordingResets,
)
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from tests.core.fake_app import START, FakeApp
from tests.runtime.test_loop import Ordered

MENU = MenuView(clock='08:00', rows=('Wi-Fi', 'Device'), cursor=0, first=0)


def ports_of(
    *, path: Path, display: FrameRecorder, observer: KitTimings | None = None,
) -> KitPorts:
    """
    Make ports on fakes.

    Parameters
    ----------
    path : Path
        Where the state file goes.
    display : FrameRecorder
        The display.
    observer : KitTimings | None
        The observer; a new timings when None.

    Returns
    -------
    KitPorts
        The ports.
    """
    return KitPorts(
        display=display,
        clock=FixedClock(at=START),
        states=StateFile(path=path, app=FakeApp()),
        observer=observer or KitTimings(),
    )


async def drawn(*, tmp_path: Path, view: object) -> np.ndarray:
    """
    Draw one view through the kit and give the frame shown.

    Parameters
    ----------
    tmp_path : Path
        Where the state file goes.
    view : object
        The view.

    Returns
    -------
    np.ndarray
        The frame.
    """
    display = FrameRecorder()
    ports = ports_of(path=tmp_path / 'state.json', display=display)
    outputs = Outputs(saver=StateSaver(states=ports.states))
    outputs.pending.view = view
    await draw_pending(ports=ports, app=FakeApp(), outputs=outputs)
    return display.frames[-1]


@pytest.mark.parametrize(
    ('view', 'draw'),
    [
        (MENU, lambda: draw_panel_view(view=MENU)),
        (
            NoticeView(title='Off'),
            lambda: draw_notice_view(view=NoticeView(title='Off')),
        ),
        (
            PlainSleepView(band='Asleep'),
            lambda: draw_plain_sleep(view=PlainSleepView(band='Asleep')),
        ),
    ],
)
async def test_kit_views_are_drawn_by_the_kit(
    tmp_path: Path, view: object, draw: object,
) -> None:
    """The kit's own screens and plain sleep screen use the kit's drawing."""
    assert callable(draw)
    frame = await drawn(tmp_path=tmp_path, view=view)
    assert np.array_equal(frame, draw())


async def test_app_views_are_drawn_by_the_app(tmp_path: Path) -> None:
    """Any other view goes to the app's draw."""
    frame = await drawn(tmp_path=tmp_path, view='view:live')
    assert np.array_equal(frame, FakeApp().draw(view='view:live'))


async def test_draw_reports_the_frame(tmp_path: Path) -> None:
    """A drawn frame reaches the observer; nothing waiting draws nothing."""
    display = FrameRecorder()
    timings = KitTimings()
    ports = ports_of(
        path=tmp_path / 'state.json', display=display, observer=timings,
    )
    outputs = Outputs(saver=StateSaver(states=ports.states))
    await draw_pending(ports=ports, app=FakeApp(), outputs=outputs)
    assert not display.frames
    outputs.pending.view = 'view:live'
    await draw_pending(ports=ports, app=FakeApp(), outputs=outputs)
    assert (len(display.frames), timings.frames) == (1, 1)


async def test_full_survives_burst(tmp_path: Path) -> None:
    """A full render replaced by a partial one is still drawn full."""
    display = FrameRecorder()
    ports = ports_of(path=tmp_path / 'state.json', display=display)
    outputs = Outputs(saver=StateSaver(states=ports.states))
    effects = (
        Render(view='one', full=True),
        Render(view='two', clear=True),
        Render(view='three'),
        Stop(),
    )
    await carry_out(
        effects=effects,
        ports=ports,
        app=FakeApp(),
        queue=asyncio.Queue(),
        tasks=set(),
        outputs=outputs,
    )
    assert outputs.pending.view == 'three'
    await draw_pending(ports=ports, app=FakeApp(), outputs=outputs)
    assert (display.fulls, display.clears) == ([True], [True])


async def test_draw_ends_a_burst_full_and_clear(tmp_path: Path) -> None:
    """The frame after a burst's draw is neither full nor clear."""
    display = FrameRecorder()
    ports = ports_of(path=tmp_path / 'state.json', display=display)
    outputs = Outputs(saver=StateSaver(states=ports.states))
    for effects in (
        (Render(view='one', full=True), Render(view='two', clear=True)),
        (Render(view='three'),),
    ):
        await carry_out(
            effects=effects,
            ports=ports,
            app=FakeApp(),
            queue=asyncio.Queue(),
            tasks=set(),
            outputs=outputs,
        )
        await draw_pending(ports=ports, app=FakeApp(), outputs=outputs)
    assert (outputs.pending.full, outputs.pending.clear) == (False, False)
    assert (display.fulls, display.clears) == ([True, False], [True, False])


async def test_save_is_written_before_carry_out_returns(
    tmp_path: Path,
) -> None:
    """A SaveState is in the file once its effects are carried out."""
    ports = ports_of(path=tmp_path / 'state.json', display=FrameRecorder())
    outputs = Outputs(saver=StateSaver(states=ports.states))
    saved = KitSaved(device=DeviceSettings(rotate=True), app=None)
    await carry_out(
        effects=(SaveState(saved=saved),),
        ports=ports,
        app=FakeApp(),
        queue=asyncio.Queue(),
        tasks=set(),
        outputs=outputs,
    )
    assert ports.states.load().device.rotate is True


async def test_failed_save_is_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The same write error twice is logged once, and never raised."""
    blocked = tmp_path / 'blocked'
    blocked.write_text('a file, not a directory', encoding='utf-8')
    saver = StateSaver(
        states=StateFile(path=blocked / 'state.json', app=FakeApp()),
    )
    effect = SaveState(saved=KitSaved(device=DeviceSettings(), app=None))
    with caplog.at_level(logging.ERROR):
        await saver.save(effect=effect)
        await saver.save(effect=effect)
    assert caplog.text.count('saving state failed') == 1


@dataclass
class LoggedResets(RecordingResets):
    """Resets that log their call."""

    log: list[str] = field(default_factory=list)

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Log the call.

        Parameters
        ----------
        kind : ResetKind
            The reset.
        """
        self.log.append('reset')
        await super().reset(kind=kind)


async def test_reset_waits_for_panel(tmp_path: Path) -> None:
    """The notice is shown and settled before the reset runs."""
    log: list[str] = []
    ports = KitPorts(
        display=Ordered(log=log),
        clock=FixedClock(at=START),
        states=StateFile(path=tmp_path / 'state.json', app=FakeApp()),
        device=DevicePorts(resets=LoggedResets(log=log)),
    )
    outputs = Outputs(saver=StateSaver(states=ports.states))
    effects = (
        Render(view=NoticeView(title='Resetting'), full=True),
        ResetDevice(kind=ResetKind.ERASE),
    )
    tasks: set[asyncio.Task[None]] = set()
    await carry_out(
        effects=effects,
        ports=ports,
        app=FakeApp(),
        queue=asyncio.Queue(),
        tasks=tasks,
        outputs=outputs,
    )
    await asyncio.gather(*tasks)
    assert log == ['show', 'settle', 'reset']


async def test_crashed_kit_work_posts_its_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A crashed kit task posts its recovery event; a cancelled one doesn't."""

    async def broken() -> None:
        raise RuntimeError('radio gone')

    queue: asyncio.Queue[object] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    task = asyncio.create_task(broken())
    tasks.add(task)
    await asyncio.gather(task, return_exceptions=True)
    with caplog.at_level(logging.ERROR):
        finished(task, tasks=tasks, queue=queue, failed='recover')
    assert queue.get_nowait() == 'recover'
    assert not tasks
    cancelled = asyncio.create_task(asyncio.sleep(10))
    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    finished(cancelled, tasks=tasks, queue=queue, failed='recover')
    assert queue.empty()


class Busy(FrameRecorder):
    """A display that never finishes drawing."""

    def settle(self, *, timeout: float) -> bool:
        """
        Report the panel still busy.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            False.
        """
        del timeout
        return False


async def test_refused_power_posts_failed_after_a_busy_panel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A panel that stays busy is warned of; a refused action posts."""
    ports = KitPorts(
        display=Busy(),
        clock=FixedClock(at=START),
        states=StateFile(path=tmp_path / 'state.json', app=FakeApp()),
        power=FakePower(result=False),
    )
    outputs = Outputs(saver=StateSaver(states=ports.states))
    queue: asyncio.Queue[object] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    with caplog.at_level(logging.WARNING):
        await carry_out(
            effects=(ControlPower(action=PowerAction.RESTART),),
            ports=ports,
            app=FakeApp(),
            queue=queue,
            tasks=tasks,
            outputs=outputs,
        )
        await asyncio.gather(*tasks)
    assert 'panel still busy' in caplog.text
    assert queue.get_nowait() == PowerFailed(action=PowerAction.RESTART)
