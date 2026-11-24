"""Tests for app: observers."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from paperroute_device.core.device_events import ApiKeyChanged
from paperroute_device.core.device_model import DeviceKey, DeviceStatus
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device_testkit.device_fakes import FakeKeys, FixedClock
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.core.knob_events import Knob, KnobHeld
from paperroute_hardware.ports import PanelRefresh
from paperroute_sydney.adapters.tfnsw import TFNSW_KEY
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY
from paperroute_testkit.core.kit_steps import app_of, settings_posted
from paperroute_testkit.fakes import BrokenTimetable, FakeNetwork, MemoryStates
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_ports import Ports
from paperroute_transit.core.messages import PinDeleted
from paperroute_transit.core.model import Pin
from paperroute_transit.core.saved import BoardSettings, Settings, seeded
from paperroute_transit.observers import NullObserver
from tests.app.helpers import (
    CallRecorder,
    CrashingFeeds,
    _build_db,
    run_built,
    run_sydney,
)
from tests.app_effects.helpers import kit_ports
from tests.app_factories import AT, MAP_PIN, PIN, ports_of


def test_ports_default_observer_is_silent() -> None:
    """Ports built without an observer get one that ignores events."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=CrashingFeeds(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
    )
    assert isinstance(ports.observer, NullObserver)


@dataclass
class RaisingObserver:
    """An observer whose every method raises."""

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Fail.

        Parameters
        ----------
        render_ms : float
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'frame {render_ms}')

    def work_done(self, *, name: str, ms: float) -> None:
        """
        Fail.

        Parameters
        ----------
        name : str
            Ignored.
        ms : float
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'work {name} {ms}')

    def app_seen(self, *, status: Mapping[str, object]) -> None:
        """
        Fail.

        Parameters
        ----------
        status : Mapping[str, object]
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'app {status}')

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Fail.

        Parameters
        ----------
        feed : str
            Ignored.
        fetch_ms : float
            Ignored.
        parse_ms : float
            Ignored.
        trips : int
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'feed {feed} {fetch_ms} {parse_ms} {trips}')

    def ticked(self) -> None:
        """
        Fail.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError('tick')

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Fail.

        Parameters
        ----------
        settings : Settings
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'settings {settings}')

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Fail.

        Parameters
        ----------
        status : DeviceStatus
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'status {status}')

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Fail.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            Ignored.
        shown : int | None
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'pins {pins} {shown}')

    def device_settings_seen(self, *, settings: DeviceSettings) -> None:
        """
        Fail.

        Parameters
        ----------
        settings : DeviceSettings
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'device settings {settings}')

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Fail.

        Parameters
        ----------
        refresh : PanelRefresh
            Ignored.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'panel {refresh}')


async def test_app_reports_settings() -> None:
    """The observer sees the start settings, then each change."""
    recorder = CallRecorder()

    def post_change(post: Callable[[object], None]) -> None:
        """
        Post a switched-off rebuild before anything else.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        post(settings_posted(auto_rebuild=False))

    await run_sydney(
        ports=ports_of(),
        seed=PIN,
        observer=recorder,
        tick_every=60,
        post_hook=post_change,
        max_events=2,
    )
    assert recorder.settings == [
        Settings(
            board=BoardSettings(auto_rebuild=True, peak_windows=PEAK_WINDOWS),
        ),
        Settings(
            board=BoardSettings(auto_rebuild=False, peak_windows=PEAK_WINDOWS),
        ),
    ]


async def test_app_reports_status() -> None:
    """The observer sees the start status, and again after a key change."""
    recorder = CallRecorder()

    def post_change(post: Callable[[object], None]) -> None:
        """
        Post a missing key before anything else.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        post(ApiKeyChanged(missing=('TfNSW',)))

    await run_sydney(
        ports=ports_of(),
        seed=PIN,
        observer=recorder,
        tick_every=60,
        post_hook=post_change,
        max_events=1,
    )
    assert [status.missing_keys for status in recorder.statuses] == [
        None, ('TfNSW',),
    ]


async def test_app_reports_pins() -> None:
    """The pins are reported at start, on a change and on a refused edit."""
    recorder = CallRecorder()
    unpinned = replace(MAP_PIN, nickname=None)
    saved = replace(seeded(pin=PIN, city=SYDNEY), unpinned=unpinned)

    def post_events(post: Callable[[object], None]) -> None:
        """
        Post a bottom hold, then a delete of a pin that is not there.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event.
        """
        post(KnobHeld(knob=Knob.BOTTOM))
        post(PinDeleted(ref='000000000000'))

    await run_sydney(
        ports=ports_of(),
        kit=kit_ports(states=MemoryStates(stored=saved)),
        observer=recorder,
        tick_every=60,
        post_hook=post_events,
        max_events=2,
    )
    pins = (PIN, unpinned)
    assert [shown for _, shown in recorder.pins] == [None, 1, 1]
    assert recorder.pins[0][0] == (PIN,)
    assert recorder.pins[1][0] == recorder.pins[2][0]
    assert [pin.origin for pin in recorder.pins[1][0]] == [
        pin.origin for pin in pins
    ]


async def test_run_reads_the_key_before_the_first_tick() -> None:
    """With a key store, the key is known before any feed is fetched."""
    recorder = CallRecorder()
    keys = (
        DeviceKey(
            name='k',
            credential=TFNSW_KEY,
            store=FakeKeys(key='k'),
            display_name='K',
        ),
    )
    kit = await run_sydney(
        ports=ports_of(),
        kit=replace(kit_ports(), device=DevicePorts(keys=keys)),
        seed=PIN,
        observer=recorder,
        tick_every=60,
        max_events=1,
    )
    assert app_of(kit=kit).panel.key_set is True
    assert recorder.statuses[-1].missing_keys == ()


async def test_run_survives_a_raising_observer(tmp_path: Path) -> None:
    """Observer errors do not stop frames, snapshots or ticks."""
    display = FrameRecorder()
    kit = await run_built(
        tmp_path=tmp_path, display=display, observer=RaisingObserver(),
    )
    assert kit.running
    assert display.frames
    assert 'v2/gtfs/realtime/sydneytrains' in app_of(kit=kit).snapshots


async def test_run_logs_frame_and_feed_timings_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Per-frame and per-feed records are DEBUG, keeping the journal quiet."""
    timetable = SqliteTimetableRepository(
        path=_build_db(tmp_path),
        city=SYDNEY,
    )
    with caplog.at_level(logging.DEBUG):
        try:
            await run_sydney(
                ports=ports_of(timetable=timetable), seed=PIN, max_events=60,
            )
        finally:
            timetable.close()
    frames = [r for r in caplog.records if r.message == 'frame drawn']
    feeds = [r for r in caplog.records if r.message == 'feed parsed']
    assert frames
    assert feeds
    assert all(r.levelno == logging.DEBUG for r in frames + feeds)
    assert all(r.__dict__['render_ms'] >= 0 for r in frames)
    for record in feeds:
        assert record.__dict__['feed']
        assert record.__dict__['fetch_ms'] >= 0
        assert record.__dict__['parse_ms'] >= 0
        assert record.__dict__['trips'] > 0
