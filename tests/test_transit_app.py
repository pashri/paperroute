"""Tests for transit's reducers behind the device kit's App contract."""

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.device_state import ClockWait
from paperroute_device.core.kit_events import Tick
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.loop import tick
from paperroute_device.runtime.ports import KitPorts
from paperroute_device_testkit.device_fakes import FixedClock
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import START
from paperroute_transit.app_effects import VersionWatch
from paperroute_transit.core.messages import HolidaysLoaded, PinsOrdered
from paperroute_transit.core.model import Pin
from paperroute_transit.core.panel_screens import TRANSIT_CONFIRM_COPY
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.rebuild import REBUILD_ROW
from paperroute_transit.core.saved import BoardSettings, Settings
from paperroute_transit.core.state import REDUCER_PARTS, device_of
from paperroute_transit.transit_app import TransitApp
from tests.core.test_state import fresh, loaded
from tests.test_app_effects import VersionFailingTimetable

APP = TransitApp(city=SYDNEY, parts=REDUCER_PARTS)
CORE_IMPORTS: Final[str] = (
    'import sys, paperroute_transit.core.state\n'
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
    app = TransitApp(city=SYDNEY, parts=REDUCER_PARTS, observer=seen)
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
        parts=REDUCER_PARTS,
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
