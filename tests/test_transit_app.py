"""Tests for transit's reducers behind the device kit's App contract."""

import asyncio
import logging
import subprocess
import sys
from argparse import Namespace
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from paperroute_device.adapters.clocks import ReplayClock
from paperroute_device.adapters.state_file import StateFile
from paperroute_device.cli import device_for, parse_args
from paperroute_device.core.device_model import DeviceStatus, ResetKind
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.device_state import ClockWait
from paperroute_device.core.kit_effects import ResetDevice
from paperroute_device.core.kit_events import Tick
from paperroute_device.logs import LastError
from paperroute_device.runtime.device_work import DevicePorts, device_work
from paperroute_device.runtime.loop import run, tick
from paperroute_device.runtime.ports import KitPorts
from paperroute_device_testkit.device_fakes import (
    FixedClock,
    RecordingResets,
    device_facts,
)
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_sydney.__main__ import SYDNEY_PARTS
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import START
from paperroute_testkit.core.kit_steps import QUIET_PARTS, device_of
from paperroute_testkit.fakes import EmptyPlacedTimetable, FakeNetwork
from paperroute_transit.adapters.board_section import BoardCodec
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.rebuild import NullBuilder
from paperroute_transit.adapters.settings_form import form_values
from paperroute_transit.app_effects import VersionWatch
from paperroute_transit.app_ports import Ports
from paperroute_transit.core.messages import HolidaysLoaded, PinsOrdered
from paperroute_transit.core.model import Pin
from paperroute_transit.core.panel_screens import TRANSIT_CONFIRM_COPY
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.rebuild import REBUILD_ROW
from paperroute_transit.core.saved import BoardSettings, Settings
from paperroute_transit.observers import Timings
from paperroute_transit.transit_app import TransitApp
from tests.app_factories import REAL
from tests.core.test_state import fresh, loaded
from tests.test_app_effects import VersionFailingTimetable

APP = TransitApp(city=SYDNEY, parts=QUIET_PARTS)
CORE_IMPORTS: Final[str] = (
    'import sys, paperroute_transit.core.reducers\n'
    "loaded = {'aiohttp', 'numpy'} & set(sys.modules)\n"
    "loaded |= {name for name in sys.modules if '.adapters' in name}\n"
    'assert not loaded, sorted(loaded)\n'
)


@dataclass
class Seen:
    """Records what the app reports to its observers."""

    events: list[str] = field(default_factory=list)

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Record the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        del settings
        self.events.append('settings')

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Record the pins.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            The pin on screen.
        """
        del pins, shown
        self.events.append('pins')


def test_hooks_match_the_spec() -> None:
    """Transit's phrase, label, rows and confirm copy are today's."""
    assert APP.offline_phrase == 'timetable times'
    assert APP.quiet_label == 'Follow the timetable'
    assert APP.menu_rows == (REBUILD_ROW,)
    assert APP.reset_hint.startswith('Reset settings clears pins')
    assert APP.confirm_copy == TRANSIT_CONFIRM_COPY


def test_render_holds_as_before() -> None:
    """A late answer, the clock wait and a drawn sleep frame keep the frame."""
    state = loaded(state=fresh())
    device = device_of(state=state)
    assert APP.render(state=state, device=device, banner=None) is not None
    held, _ = APP.on_event(
        state=state,
        device=device,
        event=HolidaysLoaded(dates=frozenset()),
    )
    assert APP.render(state=held, device=device, banner=None) is None
    waiting = replace(device, clock=ClockWait(known=False))
    assert APP.render(state=state, device=waiting, banner=None) is None
    asleep = replace(device, panel=replace(device.panel, asleep=True))
    view = APP.sleep_view(state=state, device=asleep)
    assert isinstance(view, SleepView)
    drawn = replace(asleep, last_view=replace(view, loading=False))
    assert APP.sleep_view(state=state, device=drawn) is None


def test_observe_reports_settings_and_pins() -> None:
    """A settings change, a places change and an edit are reported."""
    seen = Seen()
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS, observer=seen)
    state = fresh()
    app.observe(before=state, after=state, event=Tick(now=START))
    assert not seen.events
    board = BoardSettings(auto_rebuild=False)
    changed = replace(state, settings=Settings(board=board))
    app.observe(before=state, after=changed, event=Tick(now=START))
    unpinned = replace(state, places=replace(state.places, pins=()))
    app.observe(before=state, after=unpinned, event=Tick(now=START))
    edit = PinsOrdered(refs=())
    app.observe(before=state, after=state, event=edit)
    assert seen.events == ['settings', 'pins', 'pins']


async def test_failing_version_read_leaves_the_loop_running(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing timetable read is logged once and the ticks go on."""
    app = TransitApp(
        city=SYDNEY,
        parts=QUIET_PARTS,
        watch=VersionWatch(timetable=VersionFailingTimetable()),
    )
    ports = KitPorts(
        display=FrameRecorder(),
        clock=FixedClock(at=START),
        states=StateFile(path=tmp_path / 'state.json', app=app),
        device=DevicePorts(),
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    with caplog.at_level(logging.ERROR):
        await tick(app=app, ports=ports, queue=queue)
        await tick(app=app, ports=ports, queue=queue)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [type(event) for event in posted] == [Tick, Tick]
    failures = [
        record
        for record in caplog.records
        if record.message == 'reading the timetable version failed'
    ]
    assert len(failures) == 1


def test_core_state_loads_no_adapters() -> None:
    """Importing the reducer shim loads no adapter, aiohttp or numpy."""
    result = subprocess.run(
        [sys.executable, '-c', CORE_IMPORTS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


async def test_hooks_without_runtime_parts_do_nothing() -> None:
    """With no observer and no watch, observe and before_tick are quiet."""
    state = fresh()
    changed = replace(state, places=replace(state.places, pins=()))
    APP.observe(before=state, after=changed, event=Tick(now=START))
    assert await APP.before_tick() is None


def transit_ports(*, timetable: EmptyPlacedTimetable) -> Ports:
    """
    Make transit's own adapters on fakes.

    Parameters
    ----------
    timetable : EmptyPlacedTimetable
        The timetable.

    Returns
    -------
    Ports
        The adapters, on replayed feeds and a fixed clock.
    """
    return Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        clock=FixedClock(at=START),
        network=FakeNetwork(),
    )


async def test_sydney_runs_on_the_kit(tmp_path: Path) -> None:
    """The kit's loop runs Sydney's board and draws a frame."""
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS)
    app.attach(ports=transit_ports(timetable=EmptyPlacedTimetable()))
    display = FrameRecorder()
    ports = KitPorts(
        display=display,
        clock=FixedClock(at=START),
        states=StateFile(path=tmp_path / 'state.json', app=app),
    )
    async with asyncio.timeout(10):
        await run(
            app=app,
            ports=ports,
            zone=SYDNEY.zone,
            tick_every=0.01,
            max_events=60,
        )
    assert display.frames


def test_runtime_hooks_match_the_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sydney's zone, key and migration steps come from transit."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(tmp_path / 'key'))
    app = TransitApp(city=SYDNEY, parts=SYDNEY_PARTS)
    zone = app.zone()
    assert isinstance(zone, ZoneInfo)
    assert zone.key == 'Australia/Sydney'
    assert [key.display_name for key in app.keys()] == ['TfNSW']
    assert sorted(app.migrations()) == list(range(1, 13))
    assert isinstance(app.section(), BoardCodec)


def run_args(*, tmp_path: Path, extra: tuple[str, ...] = ()) -> Namespace:
    """
    Read a run command over replayed feeds.

    Parameters
    ----------
    tmp_path : Path
        Where the timetable and the state file go.
    extra : tuple[str, ...]
        More arguments.

    Returns
    -------
    Namespace
        The arguments.
    """
    return parse_args(
        app=TransitApp(city=SYDNEY, parts=SYDNEY_PARTS),
        argv=[
            'run',
            '--timetable', str(tmp_path / 'timetable.db'),
            '--state', str(tmp_path / 'state.json'),
            '--feeds', str(REAL),
            *extra,
        ],
    )


def test_sydney_start_wires_observer_and_watch(tmp_path: Path) -> None:
    """A start gives the app its observer and its timetable watch."""
    app = TransitApp(city=SYDNEY, parts=SYDNEY_PARTS)
    app.timings()
    with app.opened(
        args=run_args(tmp_path=tmp_path), last_error=LastError(zone=UTC),
    ):
        assert app.observer is not None
        assert app.watch is not None


def test_replay_builds_no_probe_and_uses_the_replay_clock(
    tmp_path: Path,
) -> None:
    """A replay run keeps no key, no probe and the clock it was given."""
    app = TransitApp(city=SYDNEY, parts=SYDNEY_PARTS)
    args = run_args(
        tmp_path=tmp_path, extra=('--at', '2026-10-16T06:25'),
    )
    with app.opened(args=args, last_error=LastError(zone=UTC)):
        assert app.replaying()
        clock = app.clock()
        assert isinstance(clock, ReplayClock)
        assert clock.now().replace(second=0, microsecond=0) == datetime(
            2026, 10, 16, 6, 25, tzinfo=SYDNEY.zone,
        )
        device = device_for(
            args=args, app=app, last_error=LastError(zone=UTC),
        )
    assert device.probe is None
    assert device.keys == ()


class LoggedResets(RecordingResets):
    """Resets that note each reset in a shared log."""

    def __init__(self, *, log: list[str]) -> None:
        super().__init__()
        self.log = log

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Note the reset.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.
        """
        await super().reset(kind=kind)
        self.log.append('reset')


class LoggedBuilder(NullBuilder):
    """A builder that notes when it is stopped."""

    def __init__(self, *, log: list[str]) -> None:
        self.log = log

    async def stop(self) -> None:
        """Note the stop."""
        self.log.append('stop')


async def test_reset_during_a_rebuild_stops_the_builder_first() -> None:
    """The builder stops before any file is reset."""
    log: list[str] = []
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS)
    ports = transit_ports(timetable=EmptyPlacedTimetable())
    app.attach(ports=replace(ports, builder=LoggedBuilder(log=log)))
    resets = LoggedResets(log=log)
    queue: asyncio.Queue[object] = asyncio.Queue()
    work, _ = device_work(
        ports=DevicePorts(resets=resets),
        app=app,
        effect=ResetDevice(kind=ResetKind.ERASE),
        queue=queue,
    )
    await work
    assert log == ['stop', 'reset']


def test_status_keeps_its_key_order() -> None:
    """The status page's keys come in today's order, feeds after ticks."""
    timings = Timings()
    timings.device_settings_seen(settings=DeviceSettings())
    timings.settings_seen(settings=Settings())
    timings.status_seen(
        status=DeviceStatus(missing_keys=(), facts=device_facts()),
    )
    assert list(timings.snapshot()) == [
        'frames',
        'last_render_ms',
        'max_render_ms',
        'frame_age_s',
        'tick_age_s',
        'feeds',
        'panel',
        'settings',
        'device',
    ]
    settings = timings.snapshot()['settings']
    assert settings == form_values(settings=Settings())
