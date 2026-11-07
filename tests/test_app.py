"""End-to-end test of the app loop with real data and a fake display."""

import asyncio
import logging
import shutil
import sqlite3
from collections.abc import Callable, Collection
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.feed_guard import FeedBackingOff, JoinedBody
from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.resets import FileResets
from paperroute.adapters.state_file import JsonStateRepository
from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.app import (
    MapRenderer,
    Outputs,
    Ports,
    handle,
    perform,
    run,
    starting_state,
    stop,
    ticker,
)
from paperroute.app_device import DevicePorts
from paperroute.app_effects import StateSaver, loaded_patterns
from paperroute.app_outputs import carry_out, draw_pending
from paperroute.app_render import drawing
from paperroute.app_work import fetch
from paperroute.build.gtfs_files import Source
from paperroute.build.map_style import LIGHT
from paperroute.build.tile_store import TileWriter
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.alert_model import AlertsFetched
from paperroute.core.alerts_view import AlertRow, AlertsView, AlertText
from paperroute.core.cycle import CYCLE_WAIT
from paperroute.core.device import NOTICE, on_reset
from paperroute.core.device_model import DeviceStatus, ResetKind
from paperroute.core.feeds import FeedKind
from paperroute.core.geo import Point, Shape, make_shape
from paperroute.core.map_view import STRIP_MAP_WIDTH, MapBase, Marker
from paperroute.core.messages import (
    ApiKeyChanged,
    ControlPower,
    Event,
    FeedFailed,
    FeedFetched,
    FeedJoined,
    FetchFeed,
    LoadPatterns,
    MapData,
    MapDataFailed,
    NoticeView,
    PinDeleted,
    PositionsFetched,
    PowerAction,
    PowerFailed,
    PowerRequested,
    QuitRequested,
    RebuildTimetable,
    Render,
    ResetDevice,
    ResetDone,
    SettingsChanged,
    Tick,
    TimetableRebuilt,
    TimetableSeen,
)
from paperroute.core.model import Departure, Mode, Pin, StopPlace
from paperroute.core.panel_screens import menu_view
from paperroute.core.panel_views import MenuView, SleepView
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute.core.places import shown
from paperroute.core.saved import (
    DEFAULT_SAVED,
    TOWN_HALL,
    LiveView,
    Settings,
    seeded,
)
from paperroute.core.state_saves import SAVE_AFTER
from paperroute.core.state_views import LOADING_WAIT
from paperroute.core.stop_view import NOT_PINNED
from paperroute.core.tiles import (
    TILE,
    ZOOMS,
    LabelKind,
    StreetLabel,
    TileVariant,
    tile_of,
    to_pixels,
)
from paperroute.measures import StopMeasures
from paperroute.observers import NullObserver, Timings
from paperroute.ports import Observer
from paperroute.render.alerts_view import draw_alerts_view
from paperroute.render.keep_out import KeepOut, keep_out
from paperroute.render.map_corners import NOT_INSTALLED, credit_box, scale_box
from paperroute.render.notice import draw_notice_view
from paperroute.render.panel import draw_panel_view
from paperroute.render.sleep import with_sleep_band
from paperroute.render.tile_codec import pack_bits, pack_grey
from paperroute_device.render.frame import HEADER
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_hardware.ports import CLEAN_AFTER, PanelRefresh
from paperroute_testkit.app_factories import (
    AT,
    MAP_MPP,
    MAP_PIN,
    PIN,
    REAL,
    map_view,
    ports_of,
    strip_map_view,
)
from paperroute_testkit.core.factories import pin_of, place
from paperroute_testkit.fakes import (
    STOP_POINT,
    BrokenTimetable,
    BuiltTimetable,
    EmptyPlacedTimetable,
    FakeBuilder,
    FakeKeys,
    FakeNetwork,
    FakePower,
    FakeWifi,
    FixedClock,
    LabelCountingTiles,
    MemoryStates,
    PlacedTimetable,
    RecordingResets,
    SyncingClock,
)
from paperroute_testkit.hardware_fakes import FrameRecorder


@dataclass
class CrashingTimetable(BrokenTimetable):
    """A timetable whose departures query raises an unexpected error."""

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Fail with an error the app does not expect.

        Parameters
        ----------
        pin : Pin
            Ignored.
        start : datetime
            Ignored.
        end : datetime
            Ignored.

        Returns
        -------
        list[Departure]
            Never returns.

        Raises
        ------
        RuntimeError
            Always.
        """
        self.calls += 1
        raise RuntimeError('boom')


@dataclass
class CrashingFeeds:
    """A feeds client that fails with an unexpected error."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Fail.

        Parameters
        ----------
        feed : str
            Ignored.

        Returns
        -------
        bytes
            Never returns.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'boom {feed}')

    async def close(self) -> None:
        """Do nothing."""


async def test_fetch_crash_posts_feed_failed_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected fetch error is logged and posted as FeedFailed."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=CrashingFeeds(),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    with caplog.at_level(logging.ERROR):
        await perform(
            ports=ports,
            effect=FetchFeed(feed='v1/gtfs/realtime/buses'),
            queue=queue,
            tasks=tasks,
            maps=MapRenderer(tiles=ports.tiles),
            saver=StateSaver(states=ports.states),
        )
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
    posted = queue.get_nowait()
    assert isinstance(posted, FeedFailed)
    assert posted.reason == 'unexpected error'
    assert any(r.exc_info for r in caplog.records)


async def test_run_quits_when_the_ticker_crashes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pump that raises ends the run and logs the error."""

    def pump() -> None:
        raise RuntimeError('window gone')

    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    with caplog.at_level(logging.ERROR):
        async with asyncio.timeout(5):
            state = await run(
                ports=ports, seed=PIN, tick_every=0.01, pump=pump
            )
    assert not state.running
    errors = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert any(str(error) == 'window gone' for error in errors)


async def test_run_retries_after_a_failed_load() -> None:
    """A failed departures read is retried on a later tick, not abandoned."""
    timetable = BrokenTimetable()
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    state = await run(ports=ports, seed=PIN, tick_every=0.01, max_events=20)
    assert timetable.calls >= 2
    assert not state.board.departures


async def test_run_renders_live_train_departures(tmp_path: Path) -> None:
    """The loop loads departures, fetches the train feed and draws frames."""
    db = tmp_path / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=1),
        out=db,
    )
    display = FrameRecorder()
    timetable = SqliteTimetableRepository(path=db)
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    try:
        state = await run(
            ports=ports, seed=PIN, tick_every=0.01, max_events=60
        )
    finally:
        timetable.close()
    assert display.frames
    assert state.board.departures
    assert 'v2/gtfs/realtime/sydneytrains' in state.snapshots


async def test_run_reports_frames_feeds_and_ticks(tmp_path: Path) -> None:
    """A Timings observer sees frames, the parsed train feed and ticks."""
    db = tmp_path / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=1),
        out=db,
    )
    timings = Timings()
    observer: Observer = timings
    timetable = SqliteTimetableRepository(path=db)
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        observer=observer,
    )
    try:
        await run(ports=ports, seed=PIN, tick_every=0.01, max_events=60)
    finally:
        timetable.close()
    assert timings.frames >= 1
    assert 'v2/gtfs/realtime/sydneytrains' in timings.feeds
    assert timings.feeds['v2/gtfs/realtime/sydneytrains'].trips > 0
    assert timings.last_tick_monotonic is not None


def test_ports_default_observer_is_silent() -> None:
    """Ports built without an observer get one that ignores events."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=CrashingFeeds(),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
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


@dataclass
class CallRecorder:
    """An observer that counts feeds and ticks, keeping settings and status."""

    feeds: int = 0
    ticks: int = 0
    settings: list[Settings] = field(default_factory=list)
    statuses: list[DeviceStatus] = field(default_factory=list)
    pins: list[tuple[tuple[Pin, ...], int | None]] = field(
        default_factory=list
    )

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Ignore a frame.

        Parameters
        ----------
        render_ms : float
            Ignored.
        """

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Count a parsed feed.

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
        """
        self.feeds += 1

    def ticked(self) -> None:
        """Count a tick."""
        self.ticks += 1

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Keep the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self.settings.append(settings)

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Keep the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self.statuses.append(status)

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Keep the pins and which one is shown.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            Index of the pin shown, or None when the unpinned place is.
        """
        self.pins.append((pins, shown))

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Ignore a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            Ignored.
        """


async def test_app_reports_settings() -> None:
    """The observer sees the start settings, then each change."""
    recorder = CallRecorder()
    ports = replace(ports_of(), observer=recorder)

    def post_change(post: Callable[[Event], None]) -> None:
        """
        Post a switched-off rebuild before anything else.

        Parameters
        ----------
        post : Callable[[Event], None]
            Posts an event.
        """
        post(SettingsChanged(auto_rebuild=False))

    await run(
        ports=ports,
        seed=PIN,
        tick_every=60,
        post_hook=post_change,
        max_events=2,
    )
    assert recorder.settings == [
        Settings(auto_rebuild=True),
        Settings(auto_rebuild=False),
    ]


async def test_app_reports_status() -> None:
    """The observer sees the start status, and again after a key change."""
    recorder = CallRecorder()
    ports = replace(ports_of(), observer=recorder)

    def post_change(post: Callable[[Event], None]) -> None:
        """
        Post a missing key before anything else.

        Parameters
        ----------
        post : Callable[[Event], None]
            Posts an event.
        """
        post(ApiKeyChanged(present=False))

    await run(
        ports=ports,
        seed=PIN,
        tick_every=60,
        post_hook=post_change,
        max_events=1,
    )
    assert [status.key_set for status in recorder.statuses] == [None, False]


async def test_app_reports_pins() -> None:
    """The pins are reported at start, on a change and on a refused edit."""
    recorder = CallRecorder()
    unpinned = replace(MAP_PIN, nickname=None)
    saved = replace(seeded(pin=PIN), unpinned=unpinned)
    ports = replace(
        ports_of(states=MemoryStates(stored=saved)), observer=recorder
    )

    def post_events(post: Callable[[Event], None]) -> None:
        """
        Post a bottom hold, then a delete of a pin that is not there.

        Parameters
        ----------
        post : Callable[[Event], None]
            Posts an event.
        """
        post(KnobHeld(knob=Knob.BOTTOM))
        post(PinDeleted(ref='000000000000'))

    await run(
        ports=ports,
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
    ports = replace(
        ports_of(),
        observer=recorder,
        device=DevicePorts(key=FakeKeys(key='k')),
    )
    state = await run(ports=ports, seed=PIN, tick_every=60, max_events=1)
    assert state.panel.key_set is True
    assert recorder.statuses[-1].key_set is True


def _build_db(tmp_path: Path) -> Path:
    """
    Build the real-slice timetable database.

    Parameters
    ----------
    tmp_path : Path
        Directory to build in.

    Returns
    -------
    Path
        Database path.
    """
    db = tmp_path / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=1),
        out=db,
    )
    return db


async def test_run_survives_a_raising_observer(tmp_path: Path) -> None:
    """Observer errors do not stop frames, snapshots or ticks."""
    display = FrameRecorder()
    timetable = SqliteTimetableRepository(path=_build_db(tmp_path))
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        observer=RaisingObserver(),
    )
    try:
        state = await run(
            ports=ports, seed=PIN, tick_every=0.01, max_events=60
        )
    finally:
        timetable.close()
    assert state.running
    assert display.frames
    assert 'v2/gtfs/realtime/sydneytrains' in state.snapshots


async def test_run_logs_frame_and_feed_timings_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Per-frame and per-feed records are DEBUG, keeping the journal quiet."""
    timetable = SqliteTimetableRepository(path=_build_db(tmp_path))
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    with caplog.at_level(logging.DEBUG):
        try:
            await run(ports=ports, seed=PIN, tick_every=0.01, max_events=60)
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


async def test_fetch_failure_does_not_report_a_parsed_feed(
    tmp_path: Path,
) -> None:
    """A feed that fails to fetch never reaches feed_parsed."""
    observer = CallRecorder()
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=FileFeedsClient(directory=tmp_path),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        observer=observer,
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    await perform(
        ports=ports,
        effect=FetchFeed(feed='v2/gtfs/realtime/sydneytrains'),
        queue=queue,
        tasks=tasks,
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    await asyncio.gather(*tasks)
    posted = queue.get_nowait()
    assert isinstance(posted, FeedFailed)
    assert posted.sent
    assert observer.feeds == 0


class BackingOffFeeds:
    """A feeds client whose every feed is backing off."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Refuse without sending.

        Parameters
        ----------
        feed : str
            Feed path.

        Raises
        ------
        FeedBackingOff
            Always.
        """
        raise FeedBackingOff(f'{feed}: backing off')

    async def close(self) -> None:
        """Nothing to close."""


async def test_fetch_skipped_in_backoff_is_posted_as_not_sent() -> None:
    """A feed turned away by its backoff posts FeedFailed with sent False."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=BackingOffFeeds(),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    await fetch(
        ports=ports,
        effect=FetchFeed(feed='v2/gtfs/realtime/sydneytrains'),
        queue=queue,
    )
    posted = queue.get_nowait()
    assert isinstance(posted, FeedFailed)
    assert not posted.sent


class JoiningFeeds(FileFeedsClient):
    """A feeds client whose every body came from another caller's request."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Read a recording and mark it as shared.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            The recording, as a shared body.
        """
        return JoinedBody(await super().fetch(feed=feed))


async def test_fetch_joining_another_posts_feed_joined() -> None:
    """A fetch that shared a request posts its feed, then FeedJoined."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=JoiningFeeds(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    feed = 'v2/gtfs/realtime/sydneytrains'
    await fetch(ports=ports, effect=FetchFeed(feed=feed), queue=queue)
    posted = [queue.get_nowait(), queue.get_nowait()]
    assert isinstance(posted[0], FeedFetched)
    assert posted[1] == FeedJoined(feed=feed)


async def test_run_survives_unexpected_load_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A crashing load is logged and retried; the loop keeps running."""
    timetable = CrashingTimetable()
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    with caplog.at_level(logging.ERROR):
        state = await run(
            ports=ports, seed=PIN, tick_every=0.01, max_events=20
        )
    assert state.running
    assert timetable.calls >= 2
    errors = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert any(str(error) == 'boom' for error in errors)


async def test_run_draws_when_every_feed_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing recordings are logged as failed feeds; frames still draw."""
    db = tmp_path / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=1),
        out=db,
    )
    empty = tmp_path / 'empty'
    empty.mkdir()
    display = FrameRecorder()
    timetable = SqliteTimetableRepository(path=db)
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=empty),
        display=display,
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    try:
        with caplog.at_level(logging.WARNING):
            state = await run(
                ports=ports, seed=PIN, tick_every=0.01, max_events=60
            )
    finally:
        timetable.close()
    assert display.frames
    assert 'v2/gtfs/realtime/sydneytrains' not in state.snapshots
    assert any('feed failed' in r.getMessage() for r in caplog.records)


async def test_a_queued_tick_that_is_never_handled_sends_no_watchdog(
) -> None:
    """The watchdog follows handled ticks, not queued ones."""
    observer = CallRecorder()
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        observer=observer,
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(ports=ports, queue=queue, every=60.0, pump=None)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [type(event) for event in posted] == [TimetableSeen, Tick]
    assert observer.ticks == 0


async def test_ticker_passes_synced_flag() -> None:
    """Each Tick carries whether the clock was synchronised."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=SyncingClock(at=AT, unsynced=2),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(ports=ports, queue=queue, every=0.05, pump=None)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    ticks = [event for event in posted if isinstance(event, Tick)]
    assert [tick.synced for tick in ticks[:3]] == [False, False, True]


@dataclass
class SteppingClock:
    """
    A clock that steps forward when it reports itself synchronised.

    Attributes
    ----------
    at : datetime
        The time before the step.
    step : timedelta
        How far the clock steps.
    """

    at: datetime
    step: timedelta

    def now(self) -> datetime:
        """
        The current time.

        Returns
        -------
        datetime
            Time.
        """
        return self.at

    def synced(self) -> bool:
        """
        Step the clock and report it synchronised.

        Returns
        -------
        bool
            Always True.
        """
        self.at += self.step
        self.step = timedelta()
        return True


async def test_ticker_reads_synced_before_now() -> None:
    """A synced Tick never carries the time from before the step."""
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=SteppingClock(at=AT, step=timedelta(hours=3)),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(ports=ports, queue=queue, every=60.0, pump=None)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    ticks = [event for event in posted if isinstance(event, Tick)]
    assert [tick.now for tick in ticks] == [AT + timedelta(hours=3)]


async def test_run_sends_a_watchdog_for_each_handled_tick(
    tmp_path: Path,
) -> None:
    """Every Tick the main loop handles reaches the observer."""
    observer = CallRecorder()
    timetable = SqliteTimetableRepository(path=_build_db(tmp_path))
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        observer=observer,
    )
    try:
        await run(ports=ports, seed=PIN, tick_every=0.01, max_events=20)
    finally:
        timetable.close()
    assert observer.ticks >= 1


BUS_POSITIONS = 'v1/gtfs/vehiclepos/buses'


@dataclass
class RecordingFeeds:
    """Replayed feeds that remember which feeds were fetched."""

    directory: Path
    fetched: list[str] = field(default_factory=list)

    async def fetch(self, *, feed: str) -> bytes:
        """
        Note the feed and read its recording.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            Recorded body.
        """
        self.fetched.append(feed)
        return await FileFeedsClient(directory=self.directory).fetch(
            feed=feed
        )

    async def close(self) -> None:
        """Release nothing."""


@dataclass
class PressingDisplay(FrameRecorder):
    """A display that presses the bottom knob when it first shows."""

    post: Callable[[Event], None] | None = None

    def show(
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
        Keep a frame; after the first, press the bottom knob.

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
        if not self.frames and self.post is not None:
            self.post(KnobPressed(knob=Knob.BOTTOM))
        super().show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )

    def hook(self, post: Callable[[Event], None]) -> None:
        """
        Keep the function that posts events.

        Parameters
        ----------
        post : Callable[[Event], None]
            Posts an event to the app.
        """
        self.post = post


def light_tiles(*, out: Path, point: Point) -> SqliteTileRepository:
    """
    Write a tiles file with a LIGHT tile under a point at each zoom.

    Parameters
    ----------
    out : Path
        Destination file.
    point : Point
        Point in metres.

    Returns
    -------
    SqliteTileRepository
        The opened file.
    """
    grey = np.full((TILE, TILE), LIGHT, dtype=np.uint8)
    with TileWriter(out=out) as writer:
        for mpp in ZOOMS:
            col, row = tile_of(pixel=to_pixels(point=point, mpp=mpp))
            writer.add_tile(
                zoom=mpp,
                col=col,
                row=row,
                grey=pack_grey(grey=grey),
                bits=pack_bits(paper=grey >= 128),
            )
        writer.finish(meta={})
    return SqliteTileRepository(path=out)


async def test_run_draws_map_after_bottom_press(tmp_path: Path) -> None:
    """A bottom press shows the Map over the tiles and polls positions."""
    timetable = SqliteTimetableRepository(path=_build_db(tmp_path))
    place = timetable.stop(stop_id=MAP_PIN.origin.stop_id)
    assert place.point is not None
    tiles = light_tiles(out=tmp_path / 'tiles.db', point=place.point)
    display = PressingDisplay()
    feeds = RecordingFeeds(directory=REAL)
    ports = Ports(
        timetable=timetable,
        feeds=feeds,
        display=display,
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        tiles=tiles,
    )
    try:
        await run(
            ports=ports,
            seed=MAP_PIN,
            tick_every=0.01,
            post_hook=display.hook,
            max_events=80,
        )
    finally:
        timetable.close()
        tiles.close()
    assert LIGHT in display.frames[-1]
    assert BUS_POSITIONS in feeds.fetched


@dataclass
class CountingTiles:
    """A tile repository that serves paper tiles and counts reads."""

    built: str = 'v1'
    reads: int = 0
    variants: set[TileVariant] = field(default_factory=set)

    def tile(
        self, *, zoom: int, col: int, row: int, variant: TileVariant
    ) -> NDArray[np.uint8] | None:
        """
        Count a read and give a LIGHT tile.

        Parameters
        ----------
        zoom : int
            Ignored.
        col : int
            Ignored.
        row : int
            Ignored.
        variant : TileVariant
            Recorded.

        Returns
        -------
        NDArray[np.uint8] | None
            A LIGHT tile.
        """
        self.reads += 1
        self.variants.add(variant)
        return np.full((TILE, TILE), LIGHT, dtype=np.uint8)

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Know no labels.

        Parameters
        ----------
        zoom : int
            Ignored.
        box : tuple[float, float, float, float]
            Ignored.
        kind : LabelKind
            Ignored.

        Returns
        -------
        list[StreetLabel]
            Nothing.
        """
        return []

    def version(self) -> str | None:
        """
        Say which build this is.

        Returns
        -------
        str | None
            The build name.
        """
        return self.built


def test_map_base_is_cached_between_overlay_frames() -> None:
    """A second frame with the same base reads no more tiles."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.reads
    bus = Marker(
        route='606', x=200.0, y=200.0, bearing=90.0, live=True, pinned=True
    )
    maps.frame(view=map_view(markers=(bus,)))
    assert once > 0
    assert tiles.reads == once


def test_renderer_caches_boxes_with_the_base() -> None:
    """A second frame with the same base reads no more labels."""
    tiles = LabelCountingTiles(tiles=CountingTiles())
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.label_reads
    bus = Marker(
        route='606', x=200.0, y=200.0, bearing=90.0, live=True, pinned=True
    )
    maps.frame(view=map_view(markers=(bus,)))
    assert once > 0
    assert tiles.label_reads == once
    assert maps.boxes(base=map_view().base) is maps.boxes(
        base=map_view().base,
    )


def test_unreadable_tiles_give_corner_boxes_only(tmp_path: Path) -> None:
    """Tiles that fail a read keep out only the corner boxes, as drawn."""
    base = map_view().base
    with closing(tableless_tiles(out=tmp_path / 'tiles.db')) as tiles:
        broken = MapRenderer(tiles=tiles).boxes(base=base)
    versionless = MapRenderer(tiles=VersionlessTiles()).boxes(base=base)
    edged = tuple(
        (left - 1, top - 1, right + 1, bottom + 1)
        for left, top, right, bottom in (
            scale_box(mpp=base.mpp),
            credit_box(width=base.width, text=NOT_INSTALLED),
        )
    )
    assert broken == KeepOut(places=edged, streets=(), corners=edged)
    assert versionless == broken
    assert broken == keep_out(base=base, tiles=NullTiles())


def test_drawing_picks_panel_and_sleep() -> None:
    """Panel views draw as panel screens; sleep bands a plain map."""
    maps = MapRenderer(tiles=CountingTiles())
    menu = MenuView(clock='9:00', rows=('Wi-Fi',), cursor=0, first=0)
    panel = drawing(view=menu, maps=maps)()
    assert (panel == draw_panel_view(view=menu)).all()
    asleep = drawing(
        view=SleepView(map=map_view(), band='Asleep'),
        maps=maps,
    )()
    banded = with_sleep_band(frame=maps.frame(view=map_view()), band='Asleep')
    assert (asleep == banded).all()


def test_tiles_replaced_rebuilds_base() -> None:
    """A new tiles build composes the base again."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.reads
    tiles.built = 'v2'
    maps.frame(view=map_view())
    assert tiles.reads == 2 * once


def test_strip_map_reuses_base_cache() -> None:
    """A second Strip + map frame reads no more tiles than the first."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    first = maps.strip_map_frame(view=strip_map_view())
    once = tiles.reads
    bus = Marker(
        route='606', x=200.0, y=200.0, bearing=90.0, live=True, pinned=True
    )
    maps.strip_map_frame(view=strip_map_view(markers=(bus,)))
    assert once > 0
    assert tiles.reads == once
    assert first.shape == (480, 800)
    assert (first[HEADER:, :STRIP_MAP_WIDTH] == LIGHT).any()


def test_strip_map_and_map_keep_separate_bases() -> None:
    """Flipping between Map and Strip + map draws each width's base."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    wide = tiles.reads
    maps.strip_map_frame(view=strip_map_view())
    assert tiles.reads > wide


class RelabellingTimetable(SqliteTimetableRepository):
    """The real timetable, with the 606 bus's trip renamed 606X."""

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Label trip 2706990 '606X' when asked for it.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, str]
            '606X' for trip 2706990 when asked; nothing else.
        """
        return {'2706990': '606X'} if '2706990' in trip_ids else {}


async def test_positions_are_labelled_from_the_timetable(
    tmp_path: Path,
) -> None:
    """Vehicles take the route label the timetable gives their trip."""
    timetable = RelabellingTimetable(path=_build_db(tmp_path))
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    try:
        await perform(
            ports=ports,
            effect=FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS),
            queue=queue,
            tasks=tasks,
            maps=MapRenderer(tiles=ports.tiles),
            saver=StateSaver(states=ports.states),
        )
        await asyncio.gather(*tasks)
    finally:
        timetable.close()
    posted = queue.get_nowait()
    assert isinstance(posted, PositionsFetched)
    labels = {
        vehicle.trip_id: vehicle.label
        for vehicle in posted.snapshot.vehicles
    }
    assert labels['2706990'] == '606X'


@dataclass
class PatternlessTimetable(BrokenTimetable):
    """A timetable whose patterns query fails."""

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.Error('file is not a database')


async def test_map_data_failure_posts_event() -> None:
    """A failed patterns read posts MapDataFailed for that request."""
    ports = Ports(
        timetable=PatternlessTimetable(),
        feeds=CrashingFeeds(),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    request = LoadPatterns(trip_ids=frozenset({'t1'}), day=AT.date())
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    await perform(
        ports=ports,
        effect=request,
        queue=queue,
        tasks=tasks,
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    await asyncio.gather(*tasks)
    posted = queue.get_nowait()
    assert posted == MapDataFailed(request=request)
    assert posted.kind == MapData.PATTERNS


def test_starting_state_fills_the_pins_places() -> None:
    """The pin's places get their names and points from the timetable."""
    ports = Ports(
        timetable=PlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        tiles=NullTiles(),
    )
    state = starting_state(ports=ports, seed=MAP_PIN)
    assert shown(state=state).origin == place(
        '2145615', name='2145615', point=STOP_POINT,
    )
    assert shown(state=state).destination == place(
        '215020', name='215020', point=STOP_POINT,
    )
    assert state.map.centre == STOP_POINT
    assert state.now == AT


def test_starting_state_awaits_the_first_tick() -> None:
    """The app starts not yet knowing whether the clock is set."""
    ports = Ports(
        timetable=PlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        tiles=NullTiles(),
    )
    assert not starting_state(ports=ports, seed=MAP_PIN).clock_known


def test_starting_state_keeps_no_destination() -> None:
    """A pin without a Destination starts with its From filled in."""
    ports = Ports(
        timetable=PlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        tiles=NullTiles(),
    )
    state = starting_state(ports=ports, seed=pin_of('2145615', None))
    assert shown(state=state).origin == place(
        '2145615', name='2145615', point=STOP_POINT,
    )
    assert shown(state=state).destination is None
    assert state.map.centre == STOP_POINT


async def test_run_draws_the_map_in_the_variant_asked_for() -> None:
    """The tiles variant given to run is the one the Map reads."""
    tiles = CountingTiles()
    display = PressingDisplay()
    ports = Ports(
        timetable=BuiltTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        tiles=tiles,
    )
    await run(
        ports=ports,
        seed=MAP_PIN,
        tick_every=0.01,
        post_hook=display.hook,
        max_events=10,
        tiles_variant=TileVariant.BITS,
    )
    assert tiles.reads > 0
    assert tiles.variants == {TileVariant.BITS}


@dataclass
class LabelFailingTimetable(BrokenTimetable):
    """A timetable whose route labels query fails."""

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, str]
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.Error('file is not a database')


async def test_label_failure_posts_feed_failed_for_that_feed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A labels read error fails that positions feed without a crash."""
    ports = Ports(
        timetable=LabelFailingTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    with caplog.at_level(logging.WARNING):
        await perform(
            ports=ports,
            effect=FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS),
            queue=queue,
            tasks=tasks,
            maps=MapRenderer(tiles=ports.tiles),
            saver=StateSaver(states=ports.states),
        )
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
    posted = queue.get_nowait()
    assert isinstance(posted, FeedFailed)
    assert posted.feed == BUS_POSITIONS
    assert posted.reason != 'unexpected error'
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@dataclass
class PatternedTimetable(BrokenTimetable):
    """A timetable that knows one trip, t1, on shape s1."""

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Know trips t1 and t3, which share a shape and stops.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.
        around : date
            Service date.

        Returns
        -------
        dict[str, TripPattern]
            Those asked for.
        """
        trip = TripPattern(
            trip_id='t1',
            route='606',
            mode=Mode.BUS,
            shape_id='s1',
            stops=(
                PatternStop(arrives=0, departs=0, point=(0.0, 0.0)),
                PatternStop(arrives=60, departs=60, point=(100.0, 0.0)),
            ),
            days=(around,),
        )
        return {
            trip_id: replace(trip, trip_id=trip_id)
            for trip_id in ('t1', 't3')
            if trip_id in trip_ids
        }

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Know shape s1.

        Parameters
        ----------
        shape_ids : Collection[str]
            Shape IDs.

        Returns
        -------
        dict[str, Shape]
            s1 when asked for.
        """
        shape = make_shape(shape_id='s1', points=[(0.0, 0.0), (100.0, 0.0)])
        return {'s1': shape} if 's1' in shape_ids else {}


def test_loaded_patterns_measures_found_trips_and_nones_the_rest() -> None:
    """Found trips are placed on their shapes; unknown ones are None."""
    loaded = loaded_patterns(
        timetable=PatternedTimetable(),
        effect=LoadPatterns(trip_ids=frozenset({'t1', 't2'}), day=AT.date()),
        measures=StopMeasures(),
    )
    assert loaded.trip_ids == {'t1', 't2'}
    assert set(loaded.patterns) == {'t1', 't2'}
    assert loaded.patterns['t2'] is None
    found = loaded.patterns['t1']
    assert found is not None
    assert found.along == pytest.approx((0.0, 100.0))
    assert set(loaded.shapes) == {'s1'}


def test_loaded_patterns_measure_a_shape_and_stops_once() -> None:
    """Trips sharing a shape and stops share one measurement."""
    measures = StopMeasures()
    loaded = loaded_patterns(
        timetable=PatternedTimetable(),
        effect=LoadPatterns(trip_ids=frozenset({'t1', 't3'}), day=AT.date()),
        measures=measures,
    )
    first, third = loaded.patterns['t1'], loaded.patterns['t3']
    assert first is not None
    assert third is not None
    assert first.along is third.along
    later = loaded_patterns(
        timetable=PatternedTimetable(),
        effect=LoadPatterns(trip_ids=frozenset({'t1'}), day=AT.date()),
        measures=measures,
    ).patterns['t1']
    assert later is not None
    assert later.along is first.along


def tableless_tiles(*, out: Path) -> SqliteTileRepository:
    """
    Write a tiles file that has a meta table but no tiles, and open it.

    Parameters
    ----------
    out : Path
        Destination file.

    Returns
    -------
    SqliteTileRepository
        The opened file; it passes the open check.
    """
    with closing(sqlite3.connect(out)) as conn, conn:
        conn.execute('CREATE TABLE meta (key TEXT, value TEXT)')
        conn.execute("INSERT INTO meta VALUES ('built_at', 'broken')")
    return SqliteTileRepository(path=out)


@dataclass
class ZoomingDisplay(PressingDisplay):
    """A display that shows the Map, then zooms in on the next frames."""

    def show(
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
        Keep a frame; press the bottom knob, then turn it twice.

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
        if self.post is not None and 1 <= len(self.frames) <= 2:
            self.post(KnobTurned(knob=Knob.BOTTOM, steps=-1))
        super().show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )


def test_tiles_read_error_draws_map_not_installed(tmp_path: Path) -> None:
    """A tiles file that fails a read draws as if no tiles were there."""
    view = map_view()
    with closing(tableless_tiles(out=tmp_path / 'tiles.db')) as tiles:
        broken = MapRenderer(tiles=tiles).frame(view=view)
    missing = MapRenderer(tiles=NullTiles()).frame(view=view)
    assert np.array_equal(broken, missing)


async def test_run_survives_a_tiles_read_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Map frames keep coming, with one warning, when tile reads fail."""
    display = ZoomingDisplay()
    tiles = tableless_tiles(out=tmp_path / 'tiles.db')
    ports = Ports(
        timetable=BuiltTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=FixedClock(at=AT),
        network=FakeNetwork(),
        states=MemoryStates(),
        tiles=tiles,
    )
    with caplog.at_level(logging.WARNING), closing(tiles):
        state = await run(
            ports=ports,
            seed=MAP_PIN,
            tick_every=0.01,
            post_hook=display.hook,
            max_events=30,
        )
    assert state.running
    assert state.map.step != 0
    assert len(display.frames) >= 4
    unusable = [r for r in caplog.records if r.message == 'tiles file unusable']
    assert len(unusable) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@dataclass
class VersionlessTiles(CountingTiles):
    """Tiles whose build can't be read."""

    def version(self) -> str | None:
        """
        Fail like a damaged database file.

        Returns
        -------
        str | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.Error('file is not a database')


def test_tiles_version_error_draws_map_not_installed() -> None:
    """A tiles build that can't be read draws as if no tiles were there."""
    view = map_view()
    broken = MapRenderer(tiles=VersionlessTiles()).frame(view=view)
    missing = MapRenderer(tiles=NullTiles()).frame(view=view)
    assert np.array_equal(broken, missing)


def corrupt_tiles(*, out: Path, blob: bytes | None) -> SqliteTileRepository:
    """
    Write a tiles file whose tile under the test point is corrupt.

    Parameters
    ----------
    out : Path
        Destination file.
    blob : bytes | None
        The stored tile, or None for NULL.

    Returns
    -------
    SqliteTileRepository
        The opened file; it passes the open check.
    """
    col, row = tile_of(pixel=to_pixels(point=STOP_POINT, mpp=MAP_MPP))
    with closing(sqlite3.connect(out)) as conn, conn:
        conn.execute('CREATE TABLE meta (key TEXT, value TEXT)')
        conn.execute("INSERT INTO meta VALUES ('built_at', 'corrupt')")
        conn.execute(
            'CREATE TABLE tiles (zoom INTEGER, col INTEGER, row INTEGER,'
            ' grey BLOB, bits BLOB)'
        )
        conn.execute(
            'INSERT INTO tiles VALUES (?, ?, ?, ?, ?)',
            (MAP_MPP, col, row, blob, blob),
        )
    return SqliteTileRepository(path=out)


@pytest.mark.parametrize(
    'blob',
    [pack_grey(grey=np.full((TILE, TILE), LIGHT, dtype=np.uint8))[:10], None],
    ids=['truncated', 'null'],
)
def test_corrupt_tile_draws_map_not_installed_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, blob: bytes | None
) -> None:
    """A tile that can't be decoded draws as no tiles, warning once."""
    tiles = corrupt_tiles(out=tmp_path / 'tiles.db', blob=blob)
    maps = MapRenderer(tiles=tiles)
    with caplog.at_level(logging.WARNING), closing(tiles):
        first = maps.frame(view=map_view())
        moved = MapBase(centre=STOP_POINT, mpp=MAP_MPP, route=((STOP_POINT,),))
        maps.frame(view=replace(map_view(), base=moved))
    missing = MapRenderer(tiles=NullTiles()).frame(view=map_view())
    assert np.array_equal(first, missing)
    unusable = [r for r in caplog.records if r.message == 'tiles file unusable']
    assert len(unusable) == 1


async def test_run_starts_on_town_hall_map_without_state() -> None:
    """With no saved state and no seed, Town Hall shows on the Map."""
    states = MemoryStates()
    state = await run(
        ports=ports_of(states=states), tick_every=0.01, max_events=5
    )
    assert states.saves
    first = replace(states.saves[0], quota=DEFAULT_SAVED.quota)
    assert first == DEFAULT_SAVED
    assert state.places.pins == ()
    assert shown(state=state).origin.stop_id == TOWN_HALL.stop_id
    assert state.view is LiveView.MAP


async def test_run_seeds_pin_when_no_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A seed with no saved state becomes the one pin, and is saved."""
    states = MemoryStates()
    with caplog.at_level(logging.INFO):
        state = await run(
            ports=ports_of(states=states),
            seed=MAP_PIN,
            tick_every=0.01,
            max_events=5,
        )
    origin = place('2145615', name='2145615', point=STOP_POINT)
    assert shown(state=state).origin == origin
    assert state.view is LiveView.STOP
    assert states.saves
    assert states.saves[0].pins[0].origin == origin
    assert (
        'state seeded from the command line or environment' in caplog.text
    )


async def test_run_ignores_seed_when_state_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Saved state wins over a seed, and the seed is logged as ignored."""
    stored = seeded(pin=pin_of('2145615', None))
    with caplog.at_level(logging.INFO):
        state = await run(
            ports=ports_of(states=MemoryStates(stored=stored)),
            seed=PIN,
            tick_every=0.01,
            max_events=5,
        )
    assert state.places.pins == stored.pins
    assert 'seed ignored' in caplog.text


async def test_quit_writes_unsaved_state() -> None:
    """A change followed at once by a quit is saved before the run ends."""
    states = MemoryStates(stored=seeded(pin=MAP_PIN))

    def hook(post: Callable[[Event], None]) -> None:
        post(KnobPressed(knob=Knob.BOTTOM))
        post(QuitRequested())

    async with asyncio.timeout(5):
        state = await run(
            ports=ports_of(states=states), post_hook=hook, tick_every=60.0
        )
    assert not state.running
    assert states.stored is not None
    assert states.stored.view is LiveView.MAP


@dataclass
class StoplessTimetable(EmptyPlacedTimetable):
    """A timetable that knows no stops."""

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        Find no stop.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace
            Never returns.

        Raises
        ------
        KeyError
            Always.
        """
        raise KeyError(stop_id)


def test_starting_state_keeps_unknown_seed_places_bare(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A seed stop the timetable lacks stays bare, with a warning."""
    with caplog.at_level(logging.WARNING):
        state = starting_state(
            ports=ports_of(timetable=StoplessTimetable()), seed=MAP_PIN
        )
    assert shown(state=state) == MAP_PIN
    assert 'stop 2145615 not in the timetable' in caplog.text


async def test_queued_knob_turns_draw_one_frame() -> None:
    """Ticks and five turns queued at once draw a single frame."""
    display = FrameRecorder()

    def hook(post: Callable[[Event], None]) -> None:
        post(Tick(now=AT))
        post(Tick(now=AT + LOADING_WAIT))
        for steps in (1, -1, 1, -1, 1):
            post(KnobTurned(knob=Knob.BOTTOM, steps=steps))

    async with asyncio.timeout(5):
        await run(
            ports=ports_of(display=display),
            post_hook=hook,
            tick_every=60.0,
            max_events=7,
        )
    assert len(display.frames) == 1


async def test_app_turns_panel_frames() -> None:
    """With rotate saved, the panel is asked to turn its frames."""
    display = FrameRecorder()
    saved = replace(DEFAULT_SAVED, settings=Settings(rotate=True))
    ports = ports_of(display=display, states=MemoryStates(stored=saved))

    def hook(post: Callable[[Event], None]) -> None:
        post(Tick(now=AT))
        post(Tick(now=AT + CYCLE_WAIT))

    async with asyncio.timeout(5):
        await run(ports=ports, post_hook=hook, max_events=2)
    assert display.turns[-1] is True


async def test_app_passes_clean_after_to_panel() -> None:
    """The saved cleaning count reaches the panel, and a change follows."""
    display = FrameRecorder()
    saved = replace(DEFAULT_SAVED, settings=Settings(clean_after=60))
    ports = ports_of(display=display, states=MemoryStates(stored=saved))
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    state = starting_state(ports=ports, seed=PIN)
    for event in (Tick(now=AT), SettingsChanged(clean_after=15)):
        state = await handle(
            state=state, event=event, ports=ports, queue=queue,
            tasks=tasks, outputs=outputs,
        )
        outputs.pending.view = NoticeView(title='Frame')
        await draw_pending(ports=ports, outputs=outputs)
    assert display.cleans[-2:] == [60, 15]
    await stop(tasks=tasks)


async def test_app_marks_frames_quick_after_a_knob_turn() -> None:
    """A knob turn's frame is quick; a tick 5 s later is not."""
    display = FrameRecorder()
    ports = ports_of(display=display)
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    state = starting_state(ports=ports, seed=PIN)
    later = AT + timedelta(seconds=5)
    for event in (
        Tick(now=AT),
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        Tick(now=later),
    ):
        state = await handle(
            state=state, event=event, ports=ports, queue=queue,
            tasks=tasks, outputs=outputs,
        )
        outputs.pending.view = NoticeView(title='Frame')
        await draw_pending(ports=ports, outputs=outputs)
    assert display.quicks[-2:] == [True, False]
    await stop(tasks=tasks)


async def test_app_marks_frames_asleep_in_sleep_mode() -> None:
    """Frames drawn while the screen is asleep are marked asleep."""
    display = FrameRecorder()
    ports = ports_of(display=display)
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    awake = starting_state(ports=ports, seed=PIN)
    asleep = replace(awake, panel=replace(awake.panel, asleep=True))
    for state in (awake, asleep):
        state = await handle(
            state=state, event=Tick(now=AT), ports=ports, queue=queue,
            tasks=tasks, outputs=outputs,
        )
        assert outputs.pending.asleep is state.panel.asleep
        outputs.pending.view = NoticeView(title='Frame')
        await draw_pending(ports=ports, outputs=outputs)
    assert display.sleeps[-2:] == [False, True]
    await stop(tasks=tasks)


class Ordered(FrameRecorder):
    """A display that logs its shows and settles."""

    def __init__(self, *, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def show(
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
        """Log the show and keep the frame."""
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
        """Log the settle."""
        self.log.append('settle')
        return super().settle(timeout=timeout)


@dataclass
class LoggedPower(FakePower):
    """A power control that logs its call."""

    log: list[str] = field(default_factory=list)

    async def control(self, *, action: PowerAction) -> bool:
        """Log the call."""
        self.log.append('power')
        return await super().control(action=action)


async def test_power_waits_for_panel() -> None:
    """The notice is shown and settled before the power action runs."""
    log: list[str] = []
    display = Ordered(log=log)
    ports = replace(
        ports_of(display=display), power=LoggedPower(log=log),
    )
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    effects = (
        Render(view=NoticeView(title='Off'), full=True),
        ControlPower(action=PowerAction.OFF),
    )
    tasks: set[asyncio.Task[None]] = set()
    await carry_out(
        effects=effects, ports=ports, queue=asyncio.Queue(),
        tasks=tasks, outputs=outputs,
    )
    await asyncio.gather(*tasks)
    assert log == ['show', 'settle', 'power']


@dataclass
class LoggedResets(RecordingResets):
    """Resets that log their call."""

    log: list[str] = field(default_factory=list)

    async def reset(self, *, kind: ResetKind) -> None:
        """Log the call."""
        self.log.append('reset')
        await super().reset(kind=kind)


async def test_reset_waits_for_panel() -> None:
    """The notice is shown and settled before the reset runs."""
    log: list[str] = []
    ports = replace(
        ports_of(display=Ordered(log=log)),
        device=DevicePorts(resets=LoggedResets(log=log)),
    )
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    effects = (
        Render(view=NoticeView(title='Resetting'), full=True),
        ResetDevice(kind=ResetKind.ERASE),
    )
    tasks: set[asyncio.Task[None]] = set()
    await carry_out(
        effects=effects, ports=ports, queue=asyncio.Queue(),
        tasks=tasks, outputs=outputs,
    )
    await asyncio.gather(*tasks)
    assert log == ['show', 'settle', 'reset']


async def test_full_survives_burst() -> None:
    """A full render replaced by a partial one is still drawn full."""
    display = FrameRecorder()
    ports = ports_of(display=display)
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    effects = (
        Render(view=NoticeView(title='One'), full=True),
        Render(view=NoticeView(title='Two')),
    )
    await carry_out(
        effects=effects, ports=ports, queue=asyncio.Queue(), tasks=tasks,
        outputs=outputs,
    )
    await draw_pending(ports=ports, outputs=outputs)
    assert display.fulls == [True]
    assert outputs.pending.full is False


async def test_clear_survives_burst() -> None:
    """A clear render replaced by a plain one is still drawn clear."""
    display = FrameRecorder()
    ports = ports_of(display=display)
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    effects = (
        Render(view=NoticeView(title='One'), clear=True),
        Render(view=NoticeView(title='Two')),
    )
    await carry_out(
        effects=effects, ports=ports, queue=asyncio.Queue(), tasks=tasks,
        outputs=outputs,
    )
    await draw_pending(ports=ports, outputs=outputs)
    assert display.clears == [True]
    assert outputs.pending.clear is False


async def test_app_performs_rebuild() -> None:
    """A RebuildTimetable posts how it went; stopping the app stops it."""
    builder = FakeBuilder(result=True)
    ports = replace(
        ports_of(timetable=BuiltTimetable()), builder=builder,
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    await perform(
        ports=ports,
        effect=RebuildTimetable(),
        queue=queue,
        tasks=tasks,
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    await asyncio.gather(*tasks)
    assert queue.get_nowait() == TimetableRebuilt(ok=True)
    await run(ports=ports, seed=PIN, tick_every=0.01, max_events=1)
    assert (builder.rebuilds, builder.stops) == (1, 1)


async def test_app_draws_notice_before_power() -> None:
    """The notice is on screen before the power action; a failure redraws."""
    display = FrameRecorder()
    power = FakePower(result=False, display=display)
    ports = replace(ports_of(display=display), power=power)
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    state = starting_state(ports=ports, seed=PIN)
    for event in (Tick(now=AT), PowerRequested(action=PowerAction.OFF)):
        state = await handle(
            state=state, event=event, ports=ports, queue=queue,
            tasks=tasks, outputs=outputs,
        )
    await asyncio.gather(*tasks)
    title = NOTICE[PowerAction.OFF]
    notice = draw_notice_view(view=NoticeView(title=title))
    [(action, shown)] = power.calls
    assert action is PowerAction.OFF
    assert np.array_equal(display.frames[shown - 1], notice)
    failed = next_of(queue=queue, kind=PowerFailed)
    state = await handle(
        state=state, event=failed, ports=ports, queue=queue,
        tasks=tasks, outputs=outputs,
    )
    assert state.power is None
    assert not np.array_equal(display.frames[-1], notice)
    await stop(tasks=tasks)


async def test_app_reset_leaves_no_state_file(tmp_path: Path) -> None:
    """A reset deletes state.json, nothing writes it again, and it stops."""
    path = tmp_path / 'state.json'
    resets = FileResets(
        data_dir=tmp_path,
        state=path,
        key=FakeKeys(key='k' * 32),
        wifi=FakeWifi(),
        timetable=tmp_path / 'timetable.db',
    )
    ports = replace(
        ports_of(),
        states=JsonStateRepository(path=path),
        device=DevicePorts(resets=resets),
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    state = starting_state(ports=ports, seed=PIN)
    for event in (Tick(now=AT), SettingsChanged(auto_rebuild=False)):
        state = await handle(
            state=state, event=event, ports=ports, queue=queue,
            tasks=tasks, outputs=outputs,
        )
    assert path.exists()
    state, effects = on_reset(state=state, kind=ResetKind.SETTINGS)
    await carry_out(
        effects=effects, ports=ports, queue=queue, tasks=tasks,
        outputs=outputs,
    )
    await asyncio.gather(*tasks)
    after: tuple[Event, ...] = (
        Tick(now=AT + SAVE_AFTER * 2),
        next_of(queue=queue, kind=ResetDone),
    )
    for later in after:
        state = await handle(
            state=state, event=later, ports=ports, queue=queue,
            tasks=tasks, outputs=outputs,
        )
    assert not state.running
    assert not path.exists()
    await stop(tasks=tasks)


def next_of[T](*, queue: asyncio.Queue[Event], kind: type[T]) -> T:
    """
    Take events off a queue until one of a kind turns up.

    Parameters
    ----------
    queue : asyncio.Queue[Event]
        Event queue.
    kind : type[T]
        The kind wanted.

    Returns
    -------
    T
        The first event of that kind.
    """
    while True:
        event = queue.get_nowait()
        if isinstance(event, kind):
            return event


async def test_app_starts_without_a_timetable(tmp_path: Path) -> None:
    """With no timetable file the app runs and ticks without crashing."""
    missing = tmp_path / 'timetable.db'
    timetable = SqliteTimetableRepository(path=missing)
    network = SqliteNetworkRepository(path=missing)
    ports = replace(ports_of(), timetable=timetable, network=network)
    try:
        state = await run(
            ports=ports, seed=PIN, tick_every=0.01, max_events=5,
        )
    finally:
        timetable.close()
        network.close()
    assert state.running
    assert not missing.exists()


@dataclass
class ScriptedDisplay(FrameRecorder):
    """
    A display that posts a script of events once a frame is shown.

    Attributes
    ----------
    trigger : NDArray[np.uint8] | None
        The frame that starts the script.
    script : tuple[Event, ...]
        The events posted.
    post : Callable[[Event], None] | None
        Posts an event to the app.
    """

    trigger: NDArray[np.uint8] | None = None
    script: tuple[Event, ...] = ()
    post: Callable[[Event], None] | None = None

    def show(
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
        Keep a frame; when it is the trigger, post the script once.

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
        super().show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )
        trigger = self.trigger
        if trigger is None or self.post is None:
            return
        if (frame == trigger).all():
            for event in self.script:
                self.post(event)
            self.trigger = None

    def hook(self, post: Callable[[Event], None]) -> None:
        """
        Keep the function that posts events.

        Parameters
        ----------
        post : Callable[[Event], None]
            Posts an event to the app.
        """
        self.post = post


def frame_index(
    *, frames: list[NDArray[np.uint8]], frame: NDArray[np.uint8]
) -> int:
    """
    Find the first frame equal to one given.

    Parameters
    ----------
    frames : list[NDArray[np.uint8]]
        Frames shown.
    frame : NDArray[np.uint8]
        The frame looked for.

    Returns
    -------
    int
        Its index, or -1 when it was never shown.
    """
    return next(
        (
            index
            for index, shown_frame in enumerate(frames)
            if (shown_frame == frame).all()
        ),
        -1,
    )


async def test_app_runs_with_no_timetable(tmp_path: Path) -> None:
    """With no timetable the Building screen shows, then the menu opens."""
    missing = tmp_path / 'timetable.db'
    timetable = SqliteTimetableRepository(path=missing)
    network = SqliteNetworkRepository(path=missing)
    building = draw_notice_view(
        view=NoticeView(
            title='Building the timetable',
            detail='Both knobs: hold to open Settings',
        ),
    )
    display = ScriptedDisplay(
        trigger=building,
        script=(
            Tick(now=AT),
            KnobTurned(knob=Knob.BOTTOM, steps=2),
            KnobTurned(knob=Knob.TOP, steps=-1),
            KnobPressed(knob=Knob.TOP),
            Tick(now=AT),
            KnobsHeld(),
            Tick(now=AT),
        ),
    )
    ports = replace(
        ports_of(),
        timetable=timetable,
        network=network,
        display=display,
        builder=FakeBuilder(result=True),
    )
    try:
        state = await run(
            ports=ports,
            seed=PIN,
            tick_every=0.01,
            post_hook=display.hook,
            max_events=20,
        )
    finally:
        timetable.close()
        network.close()
    menu = draw_panel_view(view=menu_view(state=state))
    first = frame_index(frames=display.frames, frame=building)
    assert 0 <= first < frame_index(frames=display.frames, frame=menu)
    assert state.rebuild.missing


@dataclass
class ArrivingDisplay(FrameRecorder):
    """
    A display that puts a timetable in place once a frame is shown.

    Attributes
    ----------
    source : Path | None
        The built timetable copied in, once.
    target : Path | None
        Where it goes.
    """

    source: Path | None = None
    target: Path | None = None

    def show(
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
        Keep a frame; after the first, copy the timetable in.

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
        super().show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )
        if self.source is not None and self.target is not None:
            shutil.copyfile(self.source, self.target)
            self.source = None


async def test_app_goes_live_when_the_timetable_arrives(
    tmp_path: Path,
) -> None:
    """A timetable built while running ends the Building screen."""
    (tmp_path / 'built').mkdir()
    built = _build_db(tmp_path / 'built')
    target = tmp_path / 'timetable.db'
    timetable = SqliteTimetableRepository(path=target)
    network = SqliteNetworkRepository(path=target)
    display = ArrivingDisplay(source=built, target=target)
    feeds = RecordingFeeds(directory=REAL)
    ports = replace(
        ports_of(),
        timetable=timetable,
        network=network,
        feeds=feeds,
        display=display,
        builder=FakeBuilder(result=True),
    )
    try:
        state = await run(
            ports=ports, seed=PIN, tick_every=0.01, max_events=60,
        )
    finally:
        timetable.close()
        network.close()
    assert not state.rebuild.missing
    assert state.board.departures
    assert feeds.fetched
    assert not isinstance(state.last_view, NoticeView)


async def test_alert_fetch_uses_feeds_port() -> None:
    """An alerts fetch goes through the feeds port once and posts alerts."""
    feeds = RecordingFeeds(directory=REAL)
    ports = replace(ports_of(), feeds=feeds)
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    await perform(
        ports=ports,
        effect=FetchFeed(
            feed='v2/gtfs/alerts/sydneytrains', kind=FeedKind.ALERTS
        ),
        queue=queue,
        tasks=tasks,
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    await asyncio.gather(*tasks)
    assert feeds.fetched == ['v2/gtfs/alerts/sydneytrains']
    posted = queue.get_nowait()
    assert isinstance(posted, AlertsFetched)
    assert len(posted.snapshot.alerts) == 5


async def test_app_draws_alerts_view() -> None:
    """A Render of an AlertsView reaches the display as a full frame."""
    display = FrameRecorder()
    ports = replace(ports_of(timetable=BuiltTimetable()), display=display)
    view = AlertsView(
        origin='Strathfield',
        destination=None,
        clock='09:39',
        tag=NOT_PINNED,
        rows=(AlertRow(headline='One', when='Ongoing', active=True),),
        selected=0,
        text=AlertText(title='One', when='Ongoing', body='Text', also=()),
        page=0,
    )
    tasks: set[asyncio.Task[None]] = set()
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles),
        saver=StateSaver(states=ports.states),
    )
    await carry_out(
        effects=(Render(view=view, full=True),),
        ports=ports,
        queue=asyncio.Queue(),
        tasks=tasks,
        outputs=outputs,
    )
    await draw_pending(ports=ports, outputs=outputs)
    [frame] = display.frames
    assert frame.shape == (480, 800)
    assert np.array_equal(frame, draw_alerts_view(view=view))
