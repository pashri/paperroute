"""Tests for the kit's event loop."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.app import MigrationStep
from paperroute_device.core.device_events import ApiKeyChanged
from paperroute_device.core.device_model import (
    Credential,
    DeviceKey,
    PowerAction,
)
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import ControlPower, Render
from paperroute_device.core.kit_events import (
    DeviceChange,
    QuitRequested,
    SettingsPosted,
    Tick,
)
from paperroute_device.core.panel_views import NoticeView
from paperroute_device.runtime import loop
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.loop import handle, run, stop
from paperroute_device.runtime.outputs import Outputs, carry_out, draw_pending
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.saver import StateSaver
from paperroute_device.runtime.timings import KitTimings
from paperroute_device_testkit.device_fakes import (
    FakeKeys,
    FakePower,
    FakeProbe,
    FixedClock,
)
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_hardware.ports import CLEAN_AFTER
from tests.core.fake_app import ASK, START, ZONE, FakeApp, fresh

CREDENTIAL = Credential(
    label='Test key', refusal='Bad test key.', not_saved='Not saved.',
)


def ports_of(
    *,
    path: Path,
    app: FakeApp,
    display: FrameRecorder | None = None,
    device: DevicePorts | None = None,
    observer: KitTimings | None = None,
) -> KitPorts:
    """
    Make the loop's ports on fakes, keeping state in a file.

    Parameters
    ----------
    path : Path
        Where the state file goes.
    app : FakeApp
        The app the state file is read for.
    display : FrameRecorder | None
        The display; a new recorder when None.
    device : DevicePorts | None
        The device adapters; the defaults when None.
    observer : KitTimings | None
        The observer; the kit's null one when None.

    Returns
    -------
    KitPorts
        The ports.
    """
    ports = KitPorts(
        display=display or FrameRecorder(),
        clock=FixedClock(at=START),
        states=StateFile(path=path, app=app),
        device=device or DevicePorts(),
    )
    if observer is None:
        return ports
    return replace(ports, observer=observer)


def posting(*events: object) -> Callable[[Callable[[object], None]], None]:
    """
    Make a post hook that posts events at start.

    Parameters
    ----------
    *events : object
        The events, in order.

    Returns
    -------
    Callable[[Callable[[object], None]], None]
        The hook.
    """

    def hook(post: Callable[[object], None]) -> None:
        """
        Post the events.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        for event in events:
            post(event)

    return hook


async def test_run_reads_the_keys_before_the_first_tick(
    tmp_path: Path,
) -> None:
    """With a key store, the keys are known before the first tick."""
    app = FakeApp()
    timings = KitTimings()
    key = DeviceKey(
        name='k',
        credential=CREDENTIAL,
        store=FakeKeys(key='k'),
        display_name='K',
    )
    ports = ports_of(
        path=tmp_path / 'state.json',
        app=app,
        device=DevicePorts(keys=(key,)),
        observer=timings,
    )
    kit = await run(app=app, ports=ports, zone=ZONE, max_events=1)
    assert kit.device.panel.key_set is True
    assert timings.device is not None
    assert timings.device.missing_keys == ()
    assert isinstance(app.observed[0], ApiKeyChanged)


async def test_run_tells_the_app_it_stopped(tmp_path: Path) -> None:
    """Once the loop ends, the app closes what it holds."""
    app = FakeApp()
    ports = ports_of(path=tmp_path / 'state.json', app=app)
    await run(app=app, ports=ports, zone=ZONE, max_events=1)
    assert app.hooks == ['stopped']


async def test_run_quits_when_the_ticker_crashes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A pump that raises ends the run and logs the error."""
    pumped: list[int] = []

    def pump() -> None:
        pumped.append(len(pumped))
        if len(pumped) > 2:
            raise RuntimeError('window gone')

    app = FakeApp()
    ports = ports_of(path=tmp_path / 'state.json', app=app)
    with caplog.at_level(logging.ERROR):
        async with asyncio.timeout(5):
            kit = await run(
                app=app, ports=ports, zone=ZONE, tick_every=0.01, pump=pump,
            )
    assert not kit.running
    assert len(pumped) == 3
    errors = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert any(str(error) == 'window gone' for error in errors)


async def test_queued_knob_turns_draw_one_frame(tmp_path: Path) -> None:
    """A tick and five turns queued at once draw a single frame."""
    app = FakeApp()
    display = FrameRecorder()
    turns = [
        KnobTurned(knob=Knob.BOTTOM, steps=steps)
        for steps in (1, -1, 1, -1, 1)
    ]
    async with asyncio.timeout(5):
        kit = await run(
            app=app,
            ports=ports_of(
                path=tmp_path / 'state.json', app=app, display=display,
            ),
            zone=ZONE,
            post_hook=posting(Tick(now=START), *turns),
            tick_every=60.0,
            max_events=6,
        )
    assert len(display.frames) == 1
    assert kit.app.view == 'live+1+-1+1+-1+1'


async def test_app_turns_panel_frames(tmp_path: Path) -> None:
    """With rotate saved, the panel is asked to turn its frames."""
    app = FakeApp()
    path = tmp_path / 'state.json'
    StateFile(path=path, app=app).save(
        saved=KitSaved(device=DeviceSettings(rotate=True), app=None),
    )
    display = FrameRecorder()
    async with asyncio.timeout(5):
        await run(
            app=app,
            ports=ports_of(path=path, app=app, display=display),
            zone=ZONE,
            tick_every=60.0,
            max_events=1,
        )
    assert display.turns[-1] is True


async def test_app_marks_frames_asleep_in_sleep_mode(tmp_path: Path) -> None:
    """Frames drawn while the screen is asleep are marked asleep."""
    app = FakeApp()
    display = FrameRecorder()
    ports = ports_of(path=tmp_path / 'state.json', app=app, display=display)
    queue: asyncio.Queue[object] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(saver=StateSaver(states=ports.states))
    awake = fresh()
    asleep = replace(
        awake,
        device=replace(
            awake.device,
            panel=replace(awake.device.panel, asleep=True),
        ),
    )
    for kit in (awake, asleep):
        kit = await handle(
            kit=kit,
            app=app,
            zone=ZONE,
            event=Tick(now=START),
            ports=ports,
            queue=queue,
            tasks=tasks,
            outputs=outputs,
        )
        assert outputs.pending.asleep is kit.device.panel.asleep
        outputs.pending.view = NoticeView(title='Frame')
        await draw_pending(ports=ports, app=app, outputs=outputs)
    assert display.sleeps[-2:] == [False, True]
    await stop(tasks=tasks)


class Ordered(FrameRecorder):
    """A display that logs its shows and settles."""

    def __init__(self, *, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def show(  # pylint: disable=too-many-arguments
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
    ) -> None:
        """
        Log the show and keep the frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        full : bool
            Whether a full refresh was asked for.
        turned : bool
            Whether the frame is turned upside down.
        quick : bool
            Whether a knob was turned just now.
        asleep : bool
            Whether the device is in Sleep mode.
        clear : bool
            Whether a clear refresh was asked for.
        clean_after : int
            Quick patches before a cleaning full refresh.
        """
        self.log.append('show')
        super().show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )

    def settle(self, *, timeout: float) -> bool:
        """
        Log the settle.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            True.
        """
        self.log.append('settle')
        return super().settle(timeout=timeout)


@dataclass
class LoggedPower(FakePower):
    """A power control that logs its call."""

    log: list[str] = field(default_factory=list)

    async def control(self, *, action: PowerAction) -> bool:
        """
        Log the call.

        Parameters
        ----------
        action : PowerAction
            The action.

        Returns
        -------
        bool
            What the fake says.
        """
        self.log.append('power')
        return await super().control(action=action)


async def test_power_waits_for_panel(tmp_path: Path) -> None:
    """The notice is shown and settled before the power action runs."""
    log: list[str] = []
    app = FakeApp()
    ports = replace(
        ports_of(
            path=tmp_path / 'state.json', app=app, display=Ordered(log=log),
        ),
        power=LoggedPower(log=log),
    )
    outputs = Outputs(saver=StateSaver(states=ports.states))
    effects = (
        Render(view=NoticeView(title='Off'), full=True),
        ControlPower(action=PowerAction.OFF),
    )
    tasks: set[asyncio.Task[None]] = set()
    await carry_out(
        effects=effects,
        ports=ports,
        app=app,
        queue=asyncio.Queue(),
        tasks=tasks,
        outputs=outputs,
    )
    await asyncio.gather(*tasks)
    assert log == ['show', 'settle', 'power']


async def test_quit_writes_unsaved_state(tmp_path: Path) -> None:
    """A change followed at once by a quit is saved before the run ends."""
    app = FakeApp()
    path = tmp_path / 'state.json'
    states = StateFile(path=path, app=app)
    states.save(saved=KitSaved(device=DeviceSettings(), app=None))
    change = SettingsPosted(device=DeviceChange(rotate=True), app=None)
    async with asyncio.timeout(5):
        kit = await run(
            app=app,
            ports=ports_of(path=path, app=app),
            zone=ZONE,
            post_hook=posting(change, QuitRequested()),
            tick_every=60.0,
        )
    assert not kit.running
    assert states.load().device.rotate is True


async def test_app_effect_runs_through_perform(tmp_path: Path) -> None:
    """An app's effect is performed by the app, which can post events."""
    app = FakeApp()
    async with asyncio.timeout(5):
        await run(
            app=app,
            ports=ports_of(path=tmp_path / 'state.json', app=app),
            zone=ZONE,
            post_hook=posting(ASK),
            tick_every=60.0,
            max_events=3,
        )
    assert app.performed[0] == 'work'
    assert 'worked' in app.observed


async def test_before_tick_event_precedes_the_tick(tmp_path: Path) -> None:
    """The app's event from before_tick is handled before the tick."""
    app = FakeApp(early='early')
    async with asyncio.timeout(5):
        kit = await run(
            app=app,
            ports=ports_of(path=tmp_path / 'state.json', app=app),
            zone=ZONE,
            tick_every=60.0,
            max_events=2,
        )
    assert app.observed[0] == 'early'
    assert isinstance(app.observed[1], Tick)
    assert kit.app.view == 'early'


async def test_perform_crash_is_logged_and_the_loop_lives(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A crash in the app's work is logged and later events still run."""
    app = FakeApp(crash=True)
    with caplog.at_level(logging.ERROR):
        async with asyncio.timeout(5):
            kit = await run(
                app=app,
                ports=ports_of(path=tmp_path / 'state.json', app=app),
                zone=ZONE,
                post_hook=posting('go', 'after'),
                tick_every=60.0,
                max_events=3,
            )
    assert kit.running
    assert kit.app.view == 'after'
    assert app.performed[:2] == ['kept', 'kept']
    errors = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert any(str(error) == 'perform broke' for error in errors)


async def test_probe_carries_the_apps_part(tmp_path: Path) -> None:
    """The first tick's probe posts facts holding the app's part."""
    app = FakeApp()
    ports = ports_of(
        path=tmp_path / 'state.json',
        app=app,
        device=DevicePorts(probe=FakeProbe()),
    )
    async with asyncio.timeout(5):
        kit = await run(
            app=app, ports=ports, zone=ZONE, tick_every=60.0, max_events=2,
        )
    facts = kit.device.panel.facts
    assert facts is not None
    assert facts.app == 'probed'


def keep(data: dict[str, object]) -> dict[str, object]:
    """
    Keep a state file's data as it is.

    Parameters
    ----------
    data : dict[str, object]
        The data.

    Returns
    -------
    dict[str, object]
        The same data.
    """
    return data


class ClashingApp(FakeApp):
    """An app that registers the kit's first migration step again."""

    def migrations(self) -> Mapping[int, MigrationStep]:
        """
        Register step 1.

        Returns
        -------
        Mapping[int, MigrationStep]
            Step 1.
        """
        return {1: keep}


async def test_run_refuses_a_repeated_migration_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step number the kit and the app share stops the run at start-up."""
    app = ClashingApp()
    display = FrameRecorder()
    ports = ports_of(path=tmp_path / 'state.json', app=FakeApp(), display=display)
    monkeypatch.setattr(loop, 'KIT_MIGRATIONS', {1: keep})
    with pytest.raises(ValueError, match='step 1 is registered twice'):
        await run(app=app, ports=ports, zone=ZONE, max_events=1)
    assert not display.frames
    assert not (tmp_path / 'state.json').exists()


async def test_start_is_written_now(tmp_path: Path) -> None:
    """At start the kit was last written now; a stored file is kept as read."""
    app = FakeApp()
    path = tmp_path / 'state.json'
    kit = await run(
        app=app, ports=ports_of(path=path, app=app), zone=ZONE, max_events=0,
    )
    assert (kit.written_at, kit.saved, kit.stored) == (START, None, False)
    assert kit.device.clock.known is False
    StateFile(path=path, app=app).save(
        saved=KitSaved(device=DeviceSettings(rotate=True), app=None),
    )
    kit = await run(
        app=app, ports=ports_of(path=path, app=app), zone=ZONE, max_events=0,
    )
    assert kit.written_at == START
    assert kit.saved == KitSaved(
        device=DeviceSettings(rotate=True), app='live',
    )
    assert kit.stored is True
