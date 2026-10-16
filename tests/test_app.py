"""End-to-end test of the app loop with real data and a fake display."""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.app import Ports, run
from paperroute.build.timetable import Source, Window, build_timetable
from paperroute.core.model import SYDNEY, Departure, Pin

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
