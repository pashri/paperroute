"""End-to-end test of the app loop with real data and a fake display."""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.app import Ports, perform, run
from paperroute.build.timetable import Source, Window, build_timetable
from paperroute.core.model import SYDNEY, Departure, Pin
from paperroute.core.state import Event, FeedFailed, FetchFeed
from paperroute.observers import NullObserver, Timings
from paperroute.ports import Observer

REAL = Path(__file__).parent / 'fixtures' / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=SYDNEY)
PIN = Pin(origin='215020', destination='200060')


@dataclass
class FrameRecorder:
    """A display that keeps every frame."""

    frames: list[NDArray[np.uint8]] = field(default_factory=list)

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Keep a frame.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        """
        self.frames.append(frame)


@dataclass(frozen=True)
class FixedClock:
    """A clock stuck at one moment."""

    at: datetime

    def now(self) -> datetime:
        """
        The fixed time.

        Returns
        -------
        datetime
            Time.
        """
        return self.at


@dataclass
class BrokenTimetable:
    """A timetable whose departures query always fails."""

    calls: int = 0

    def stop_name(self, *, stop_id: str) -> str:
        """
        Name any stop after its ID.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        str
            The ID.
        """
        return stop_id

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Fail like a damaged database file.

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
        sqlite3.DatabaseError
            Always.
        """
        self.calls += 1
        raise sqlite3.DatabaseError('file is not a database')


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
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    with caplog.at_level(logging.ERROR):
        await perform(
            ports=ports,
            effect=FetchFeed(feed='v1/gtfs/realtime/buses'),
            queue=queue,
            tasks=tasks,
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
    )
    with caplog.at_level(logging.ERROR):
        async with asyncio.timeout(5):
            state = await run(
                ports=ports, pin=PIN, tick_every=0.01, pump=pump
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
    )
    state = await run(ports=ports, pin=PIN, tick_every=0.01, max_events=20)
    assert timetable.calls >= 2
    assert not state.departures


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
    )
    try:
        state = await run(
            ports=ports, pin=PIN, tick_every=0.01, max_events=60
        )
    finally:
        timetable.close()
    assert display.frames
    assert state.departures
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
        observer=observer,
    )
    try:
        await run(ports=ports, pin=PIN, tick_every=0.01, max_events=60)
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


@dataclass
class CallRecorder:
    """An observer that counts feed_parsed calls."""

    feeds: int = 0

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
        """Ignore a tick."""


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
        observer=RaisingObserver(),
    )
    try:
        state = await run(
            ports=ports, pin=PIN, tick_every=0.01, max_events=60
        )
    finally:
        timetable.close()
    assert state.running
    assert display.frames
    assert 'v2/gtfs/realtime/sydneytrains' in state.snapshots


async def test_run_logs_frame_and_feed_timings_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The INFO records carry the fields the journal reader needs."""
    timetable = SqliteTimetableRepository(path=_build_db(tmp_path))
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
    )
    with caplog.at_level(logging.INFO):
        try:
            await run(ports=ports, pin=PIN, tick_every=0.01, max_events=60)
        finally:
            timetable.close()
    frames = [r for r in caplog.records if r.message == 'frame drawn']
    feeds = [r for r in caplog.records if r.message == 'feed parsed']
    assert frames
    assert feeds
    assert all(r.levelno == logging.INFO for r in frames + feeds)
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
        observer=observer,
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()
    await perform(
        ports=ports,
        effect=FetchFeed(feed='v2/gtfs/realtime/sydneytrains'),
        queue=queue,
        tasks=tasks,
    )
    await asyncio.gather(*tasks)
    assert isinstance(queue.get_nowait(), FeedFailed)
    assert observer.feeds == 0


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
    )
    with caplog.at_level(logging.ERROR):
        state = await run(ports=ports, pin=PIN, tick_every=0.01, max_events=20)
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
    )
    try:
        with caplog.at_level(logging.WARNING):
            state = await run(
                ports=ports, pin=PIN, tick_every=0.01, max_events=60
            )
    finally:
        timetable.close()
    assert display.frames
    assert 'v2/gtfs/realtime/sydneytrains' not in state.snapshots
    assert any('feed failed' in r.getMessage() for r in caplog.records)
