"""Tests for app: run loop."""

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_testkit.core.kit_steps import app_of
from paperroute_testkit.fakes import BrokenTimetable
from paperroute_transit.core.messages import FeedFailed, FetchFeed
from paperroute_transit.observers import Timings
from tests.app.helpers import (
    CrashingFeeds,
    built_timetable,
    run_built,
    run_sydney,
)
from tests.app_factories import PIN, ports_of, posted_by


async def test_fetch_crash_posts_feed_failed_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected fetch error is logged and posted as FeedFailed."""
    ports = replace(
        ports_of(timetable=BrokenTimetable()), feeds=CrashingFeeds(),
    )
    with caplog.at_level(logging.ERROR):
        posted = await posted_by(
            ports=ports,
            effects=[FetchFeed(feed='v1/gtfs/realtime/buses')],
        )
    assert isinstance(posted[0], FeedFailed)
    assert posted[0].reason == 'unexpected error'
    assert any(r.exc_info for r in caplog.records)


async def test_run_retries_after_a_failed_load() -> None:
    """A failed departures read is retried on a later tick, not abandoned."""
    timetable = BrokenTimetable()
    kit = await run_sydney(
        ports=ports_of(timetable=timetable), seed=PIN, max_events=20,
    )
    assert timetable.calls >= 2
    assert not app_of(kit=kit).board.departures


async def test_run_renders_live_train_departures(tmp_path: Path) -> None:
    """The loop loads departures, fetches the train feed and draws frames."""
    display = FrameRecorder()
    kit = await run_built(tmp_path=tmp_path, display=display)
    state = app_of(kit=kit)
    assert display.frames
    assert state.board.departures
    assert 'v2/gtfs/realtime/sydneytrains' in state.snapshots


async def test_run_reports_frames_feeds_and_ticks(tmp_path: Path) -> None:
    """A Timings observer sees frames, the parsed train feed and ticks."""
    timings = Timings()
    with built_timetable(tmp_path=tmp_path) as timetable:
        await run_sydney(
            ports=ports_of(timetable=timetable),
            seed=PIN,
            observer=timings,
            max_events=60,
        )
    assert timings.frames >= 1
    assert 'v2/gtfs/realtime/sydneytrains' in timings.feeds
    assert timings.feeds['v2/gtfs/realtime/sydneytrains'].trips > 0
    assert timings.last_tick_monotonic is not None
