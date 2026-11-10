"""The clock app runs on the device kit with no transit code."""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import numpy as np
import skia
from numpy.typing import NDArray

from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.device_model import (
    DeviceKey,
    Menu,
    MenuRow,
    PowerAction,
    menu_rows,
)
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_events import (
    DeviceChange,
    QuitRequested,
    SettingsPosted,
    Tick,
)
from paperroute_device.core.kit_state import SAVE_WAIT, KitState
from paperroute_device.core.panel_views import MenuView, NoticeView
from paperroute_device.render.canvas import encode_png
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.loop import run
from paperroute_device.runtime.ports import KitPorts
from paperroute_device_testkit.device_fakes import (
    FakeKeys,
    FakePower,
    FakeWifi,
    FixedClock,
)
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from tests.clock.clock_app import ClockApp, ClockState, ClockView

START: Final[datetime] = datetime(2026, 11, 10, 8, 30, tzinfo=UTC)
FRAMES: Final[Path] = Path(__file__).parent / 'frames' / sys.platform
UPDATING: Final[bool] = os.getenv('GOLDEN_UPDATE') == '1'
PACKAGE: Final[Path] = Path(__file__).parents[2]
ROWS: Final[tuple[str, ...]] = (
    'Wi-Fi',
    'Device',
    'Open on your phone',
    'Sleep now',
    'Restart',
    'Shut down',
    'Reset settings',
)
BOTH: Final[KnobsHeld] = KnobsHeld()
DOWN: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
NO_KEY: Final[str] = 'No Demo key · set it on your phone'
# The kit's own first tick, which lands somewhere among the posted events.
FIRST_TICK: Final[int] = 1
# Runs the clock in a fresh interpreter and lists any transit module loaded.
CHILD: Final[str] = textwrap.dedent(
    """
    import asyncio
    import sys
    import tempfile
    from pathlib import Path

    from tests.clock.clock_app import ClockApp
    from tests.clock.test_clock_app import ticked

    with tempfile.TemporaryDirectory() as folder:
        kit, frames = asyncio.run(ticked(path=Path(folder) / 'state.json'))
    assert kit.app.now is not None and frames == 1
    loaded = sorted(
        name
        for name in sys.modules
        if name.split('.')[0] in {'paperroute_transit', 'paperroute_testkit'}
    )
    print(','.join(loaded))
    """,
)


def ports_of(
    *,
    path: Path,
    app: ClockApp,
    display: FrameRecorder | None = None,
    keys: tuple[DeviceKey, ...] = (),
    wifi: FakeWifi | None = None,
    power: FakePower | None = None,
) -> KitPorts:
    """
    Make the kit's ports on the device test kit's fakes.

    Parameters
    ----------
    path : Path
        Where the kit's state file goes.
    app : ClockApp
        The app the state file is read for.
    display : FrameRecorder | None
        The display; a new recorder when None.
    keys : tuple[DeviceKey, ...]
        The keys the device keeps.
    wifi : FakeWifi | None
        The Wi-Fi control; a new fake when None.
    power : FakePower | None
        The power control; a new fake when None.

    Returns
    -------
    KitPorts
        The ports.
    """
    return KitPorts(
        display=display or FrameRecorder(),
        clock=FixedClock(at=START),
        states=StateFile(path=path, app=app),
        device=DevicePorts(keys=keys, wifi=wifi or FakeWifi()),
        power=power or FakePower(),
    )


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


@dataclass
class Quitter:
    """
    Posts events at start, and a quit when a fake is done.

    Attributes
    ----------
    events : tuple[object, ...]
        The events posted at start, in order.
    post : Callable[[object], None] | None
        Posts an event, once the run has started.
    """

    events: tuple[object, ...]
    post: Callable[[object], None] | None = None

    def __call__(self, post: Callable[[object], None]) -> None:
        """
        Keep the poster and post the events.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        self.post = post
        for event in self.events:
            post(event)

    def quit(self) -> None:
        """Ask the run to quit."""
        assert self.post is not None
        self.post(QuitRequested())


@dataclass
class QuittingPower(FakePower):
    """
    A power control that ends the run once it is called.

    Attributes
    ----------
    quitter : Quitter
        Ends the run.
    """

    quitter: Quitter = field(default_factory=lambda: Quitter(events=()))

    async def control(self, *, action: PowerAction) -> bool:
        """
        Note the call, then end the run.

        Parameters
        ----------
        action : PowerAction
            The action asked for.

        Returns
        -------
        bool
            What the fake says.
        """
        done = await super().control(action=action)
        self.quitter.quit()
        return done


@dataclass
class QuittingWifi(FakeWifi):
    """
    A Wi-Fi control that ends the run once the hotspot is asked for.

    Attributes
    ----------
    quitter : Quitter
        Ends the run.
    """

    quitter: Quitter = field(default_factory=lambda: Quitter(events=()))

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Note the start, then end the run.

        Parameters
        ----------
        password : str
            Hotspot password.

        Returns
        -------
        bool
            What the fake says.
        """
        started = await super().start_hotspot(password=password)
        self.quitter.quit()
        return started


async def ran(
    *,
    app: ClockApp,
    ports: KitPorts,
    events: tuple[object, ...] = (),
    count: int | None,
    quitter: Quitter | None = None,
) -> KitState[ClockState]:
    """
    Run the clock on the kit for a number of events.

    Parameters
    ----------
    app : ClockApp
        The app.
    ports : KitPorts
        The ports.
    events : tuple[object, ...]
        Events posted before the kit's own.
    count : int | None
        How many events the kit handles before it stops; None to run
        until quit.
    quitter : Quitter | None
        Posts the events and the quit instead, when set.

    Returns
    -------
    KitState[ClockState]
        The final kit state.
    """
    async with asyncio.timeout(5):
        return await run(
            app=app,
            ports=ports,
            zone=UTC,
            post_hook=quitter or posting(*events),
            tick_every=60.0,
            max_events=count,
        )


async def ticked(*, path: Path) -> tuple[KitState[ClockState], int]:
    """
    Run the clock to its first tick, with its key set.

    Parameters
    ----------
    path : Path
        Where the kit's state file goes.

    Returns
    -------
    tuple[KitState[ClockState], int]
        The final kit state and how many frames were shown.
    """
    app = ClockApp()
    display = FrameRecorder()
    keys = (replace(app.keys()[0], store=FakeKeys(key='demo-key')),)
    ports = ports_of(path=path, app=app, display=display, keys=keys)
    kit = await ran(app=app, ports=ports, events=(Tick(now=START),), count=2)
    return kit, len(display.frames)


def decoded(*, data: bytes) -> NDArray[np.uint8]:
    """
    Read an 8-bit grey PNG.

    Parameters
    ----------
    data : bytes
        PNG file contents.

    Returns
    -------
    NDArray[np.uint8]
        Height by width grey levels.
    """
    image = skia.Image.MakeFromEncoded(data)
    pixels: NDArray[np.uint8] = image.toarray(
        colorType=skia.ColorType.kGray_8_ColorType,
    )
    return pixels


def baseline(*, path: Path, frame: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """
    Read a baseline frame, first writing it when updating baselines.

    Parameters
    ----------
    path : Path
        The baseline PNG.
    frame : NDArray[np.uint8]
        The frame drawn today.

    Returns
    -------
    NDArray[np.uint8]
        The baseline's pixels.
    """
    if UPDATING:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode_png(grey=frame))
    return decoded(data=path.read_bytes())


def no_transit_loaded() -> bool:
    """
    Say whether this process has loaded no transit module.

    Returns
    -------
    bool
        True when no module under `paperroute_transit` is loaded.
    """
    return not any(
        name.split('.')[0] == 'paperroute_transit' for name in sys.modules
    )


async def test_clock_draws_the_reference_frame(tmp_path: Path) -> None:
    """The first tick draws the time, pixel for pixel as the baseline."""
    app = ClockApp()
    display = FrameRecorder()
    keys = (replace(app.keys()[0], store=FakeKeys(key='demo-key')),)
    ports = ports_of(
        path=tmp_path / 'state.json', app=app, display=display, keys=keys,
    )
    kit = await ran(app=app, ports=ports, events=(Tick(now=START),), count=2)
    assert kit.device.last_view == ClockView(text='08:30', banner=None)
    assert len(display.frames) == 1
    frame = display.frames[-1]
    assert (frame == 0).any() and (frame == 255).any()
    expected = baseline(path=FRAMES / 'clock.png', frame=frame)
    assert np.array_equal(frame, expected), (
        f'{int((frame != expected).sum())} pixels differ from the baseline'
    )


async def test_no_transit_module_is_loaded(tmp_path: Path) -> None:
    """Neither this run nor a fresh one that runs the clock loads transit."""
    kit, frames = await ticked(path=tmp_path / 'state.json')
    assert frames == 1 and kit.app.now == START
    assert no_transit_loaded()
    child = subprocess.run(
        [sys.executable, '-c', CHILD],
        cwd=PACKAGE,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == ''


async def test_both_knobs_held_opens_the_menu(tmp_path: Path) -> None:
    """Holding both knobs opens the kit's menu, with the kit's rows only."""
    app = ClockApp()
    ports = ports_of(path=tmp_path / 'state.json', app=app)
    kit = await ran(
        app=app,
        ports=ports,
        events=(Tick(now=START), BOTH),
        count=2 + FIRST_TICK,
    )
    assert kit.device.panel.menu == Menu()
    view = kit.device.last_view
    assert isinstance(view, MenuView)
    assert view.rows == ROWS
    assert view.clock == '8:30'


async def test_power_off_is_reachable(tmp_path: Path) -> None:
    """Shut down on the menu, confirmed, asks the power control for OFF."""
    app = ClockApp()
    shut_down = menu_rows(app_rows=app.menu_rows).index(MenuRow.SHUT_DOWN)
    quitter = Quitter(
        events=(
            Tick(now=START),
            BOTH,
            KnobTurned(knob=Knob.BOTTOM, steps=shut_down),
            DOWN,
            DOWN,
        ),
    )
    power = QuittingPower(quitter=quitter)
    ports = ports_of(path=tmp_path / 'state.json', app=app, power=power)
    kit = await ran(app=app, ports=ports, count=None, quitter=quitter)
    assert [action for action, _ in power.calls] == [PowerAction.OFF]
    assert kit.device.last_view == NoticeView(title='Shutting down')


async def test_wifi_setup_is_reachable(tmp_path: Path) -> None:
    """The menu's Wi-Fi row scans for networks and starts the hotspot."""
    app = ClockApp()
    quitter = Quitter(events=(Tick(now=START), BOTH, DOWN))
    wifi = QuittingWifi(quitter=quitter)
    ports = ports_of(path=tmp_path / 'state.json', app=app, wifi=wifi)
    kit = await ran(app=app, ports=ports, count=None, quitter=quitter)
    asked = [call for call in wifi.calls if call != 'saved']
    assert asked == ['active', 'scan', 'start']
    assert kit.device.panel.wifi is not None


async def test_missing_key_shows_its_banner(tmp_path: Path) -> None:
    """A missing Demo key names itself under the time; a set one doesn't."""
    app = ClockApp()
    missing = ports_of(
        path=tmp_path / 'missing.json', app=app, keys=app.keys(),
    )
    kit = await ran(app=app, ports=missing, count=2)
    assert kit.device.last_view == ClockView(text='08:30', banner=NO_KEY)
    kit, _ = await ticked(path=tmp_path / 'present.json')
    assert kit.device.last_view == ClockView(text='08:30', banner=None)


async def test_rotate_persists_through_the_kit_state_file(
    tmp_path: Path,
) -> None:
    """A posted rotate is saved on a later tick and read by the next run."""
    app = ClockApp()
    path = tmp_path / 'state.json'
    states = StateFile(path=path, app=app)
    states.save(saved=KitSaved(device=DeviceSettings(), app=None))
    posted = SettingsPosted(device=DeviceChange(rotate=True), app=None)
    early = await ran(
        app=app, ports=ports_of(path=path, app=app), events=(posted,), count=1,
    )
    assert early.device.settings.rotate is True
    assert states.load().device.rotate is False
    later = Tick(now=START + SAVE_WAIT)
    events = (posted, later)
    await ran(
        app=app, ports=ports_of(path=path, app=app), events=events, count=2,
    )
    assert states.load().device.rotate is True
    kit = await ran(app=app, ports=ports_of(path=path, app=app), count=0)
    assert kit.device.settings.rotate is True
    assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 1


async def test_clock_file_moves_past_a_missing_step(tmp_path: Path) -> None:
    """A version-1 file reaches version 4 through the clock's one step."""
    path = tmp_path / 'state.json'
    StateFile(path=path, app=ClockApp()).save(
        saved=KitSaved(device=DeviceSettings(), app=None),
    )
    seen: list[object] = []

    def stamp(data: dict[str, object]) -> dict[str, object]:
        """
        Note the version the step was handed.

        Parameters
        ----------
        data : dict[str, object]
            The file's data.

        Returns
        -------
        dict[str, object]
            The same data.
        """
        seen.append(data['schema_version'])
        return data

    app = ClockApp(steps={3: stamp})
    assert app.migrations() == {3: stamp}
    kit = await ran(
        app=app,
        ports=ports_of(path=path, app=app),
        events=(
            SettingsPosted(device=DeviceChange(rotate=True), app=None),
            QuitRequested(),
        ),
        count=2,
    )
    assert seen == [3]
    assert kit.stored is True
    assert not kit.running
    assert StateFile(path=path, app=app).load().device.rotate is True
    assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 4
