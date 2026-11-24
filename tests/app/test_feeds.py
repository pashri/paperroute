"""Tests for app: feeds."""

import asyncio
import logging
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from paperroute_device.core.kit_events import Tick
from paperroute_device.runtime.loop import ticker
from paperroute_device_testkit.device_fakes import SyncingClock
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.kit_steps import app_of
from paperroute_testkit.fakes import BrokenTimetable
from paperroute_transit.adapters.feed_errors import FeedThrottled
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.feed_guard import FeedBackingOff, JoinedBody
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_work import fetch
from paperroute_transit.core.messages import (
    Event,
    FeedFailed,
    FeedFetched,
    FeedJoined,
    FetchFeed,
    TimetableSeen,
)
from paperroute_transit.core.model import Departure, Pin
from tests.app.helpers import (
    CallRecorder,
    _build_db,
    built_timetable,
    run_shown,
    run_sydney,
)
from tests.app_effects.helpers import kit_ports, sydney_app
from tests.app_factories import AT, PIN, REAL, ports_of, posted_by


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


async def test_fetch_failure_does_not_report_a_parsed_feed(
    tmp_path: Path,
) -> None:
    """A feed that fails to fetch never reaches feed_parsed."""
    observer = CallRecorder()
    ports = replace(
        ports_of(timetable=BrokenTimetable()),
        feeds=FileFeedsClient(directory=tmp_path),
        observer=observer,
    )
    posted = await posted_by(
        ports=ports,
        effects=[FetchFeed(feed='v2/gtfs/realtime/sydneytrains')],
    )
    assert isinstance(posted[0], FeedFailed)
    assert posted[0].sent
    assert observer.feeds == 0


async def test_feed_parse_is_reported_as_work_done(
    tmp_path: Path,
) -> None:
    """Each parsed feed is also reported as a piece of work by its name."""
    recorder = CallRecorder()
    with built_timetable(tmp_path=tmp_path) as timetable:
        await run_sydney(
            ports=ports_of(timetable=timetable),
            seed=PIN,
            observer=recorder,
            max_events=60,
        )
    assert recorder.feeds > 0
    assert len(recorder.work) == recorder.feeds
    assert 'v2/gtfs/realtime/sydneytrains' in {
        name for name, _ in recorder.work
    }
    assert all(ms >= 0 for _, ms in recorder.work)


class RefusingFeeds:
    """A feeds client that TfNSW turns away with a reason."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Refuse with the reason the server gave.

        Parameters
        ----------
        feed : str
            Feed path.

        Raises
        ------
        FeedThrottled
            Always.
        """
        raise FeedThrottled(
            feed=feed,
            retry_after_s=None,
            detail='X-Error-Detail: Account Over Rate Limit',
        )

    async def close(self) -> None:
        """Nothing to close."""


async def test_fetch_logs_why_the_feed_was_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The feed failed line carries the server's reason for refusing."""
    ports = replace(
        ports_of(timetable=BrokenTimetable()), feeds=RefusingFeeds(),
    )
    posted: list[Event] = []
    with caplog.at_level(logging.WARNING):
        await fetch(
            ports=ports,
            effect=FetchFeed(feed='v1/gtfs/realtime/buses'),
            post=posted.append,
        )
    [record] = [r for r in caplog.records if 'feed failed' in r.message]
    assert getattr(record, 'refusal') == (
        'X-Error-Detail: Account Over Rate Limit'
    )
    assert isinstance(posted[0], FeedFailed)
    assert posted[0].reason == 'v1/gtfs/realtime/buses: HTTP 429'


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
    ports = replace(
        ports_of(timetable=BrokenTimetable()), feeds=BackingOffFeeds(),
    )
    posted: list[Event] = []
    await fetch(
        ports=ports,
        effect=FetchFeed(feed='v2/gtfs/realtime/sydneytrains'),
        post=posted.append,
    )
    assert isinstance(posted[0], FeedFailed)
    assert not posted[0].sent


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
    ports = replace(
        ports_of(timetable=BrokenTimetable()),
        feeds=JoiningFeeds(directory=REAL),
    )
    posted: list[Event] = []
    feed = 'v2/gtfs/realtime/sydneytrains'
    await fetch(ports=ports, effect=FetchFeed(feed=feed), post=posted.append)
    assert isinstance(posted[0], FeedFetched)
    assert posted[1] == FeedJoined(feed=feed)


async def test_run_survives_unexpected_load_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A crashing load is logged and retried; the loop keeps running."""
    timetable = CrashingTimetable()
    with caplog.at_level(logging.ERROR):
        kit = await run_sydney(
            ports=ports_of(timetable=timetable), seed=PIN, max_events=20,
        )
    assert kit.running
    assert timetable.calls >= 2
    errors = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert any(str(error) == 'boom' for error in errors)


async def test_run_draws_when_every_feed_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing recordings are logged as failed feeds; frames still draw."""
    db = _build_db(tmp_path)
    empty = tmp_path / 'empty'
    empty.mkdir()
    display = FrameRecorder()
    timetable = SqliteTimetableRepository(
        path=db,
        city=SYDNEY,
    )
    ports = replace(
        ports_of(timetable=timetable),
        feeds=FileFeedsClient(directory=empty),
    )
    with closing(timetable), caplog.at_level(logging.WARNING):
        kit = await run_shown(ports=ports, display=display)
    assert display.frames
    assert 'v2/gtfs/realtime/sydneytrains' not in app_of(kit=kit).snapshots
    assert any('feed failed' in r.getMessage() for r in caplog.records)


async def test_a_queued_tick_that_is_never_handled_sends_no_watchdog(
) -> None:
    """The watchdog follows handled ticks, not queued ones."""
    observer = CallRecorder()
    app = sydney_app(ports=ports_of(timetable=BrokenTimetable()))
    ports = replace(kit_ports(), observer=observer)
    queue: asyncio.Queue[object] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(app=app, ports=ports, queue=queue, every=60.0, pump=None)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [type(event) for event in posted] == [TimetableSeen, Tick]
    assert observer.ticks == 0


async def test_ticker_passes_synced_flag() -> None:
    """Each Tick carries whether the clock was synchronised."""
    app = sydney_app(ports=ports_of(timetable=BrokenTimetable()))
    ports = replace(kit_ports(), clock=SyncingClock(at=AT, unsynced=2))
    queue: asyncio.Queue[object] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(app=app, ports=ports, queue=queue, every=0.05, pump=None)
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
    app = sydney_app(ports=ports_of(timetable=BrokenTimetable()))
    ports = replace(
        kit_ports(), clock=SteppingClock(at=AT, step=timedelta(hours=3)),
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(app=app, ports=ports, queue=queue, every=60.0, pump=None)
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
    with built_timetable(tmp_path=tmp_path) as timetable:
        await run_sydney(
            ports=ports_of(timetable=timetable),
            seed=PIN,
            observer=observer,
            max_events=20,
        )
    assert observer.ticks >= 1
