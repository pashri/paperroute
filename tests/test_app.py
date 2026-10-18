"""End-to-end test of the app loop with real data and a fake display."""

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Collection
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.app import (
    MapRenderer,
    Ports,
    loaded_patterns,
    perform,
    run,
    starting_state,
    ticker,
)
from paperroute.build.gtfs_files import Source
from paperroute.build.map_style import LIGHT
from paperroute.build.tile_store import TileWriter
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.feeds import FeedKind
from paperroute.core.geo import MetreBox, Point, Shape, make_shape, to_metres
from paperroute.core.map_view import MapBase, MapView, Marker
from paperroute.core.messages import (
    Event,
    FeedFailed,
    FetchFeed,
    Knob,
    KnobPressed,
    KnobTurned,
    LoadPatterns,
    MapData,
    MapDataFailed,
    PositionsFetched,
)
from paperroute.core.model import SYDNEY, Departure, Mode, Pin, StopPlace
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute.core.tiles import (
    TILE,
    StreetLabel,
    TileVariant,
    tile_of,
    to_pixels,
)
from paperroute.observers import NullObserver, Timings
from paperroute.ports import Observer
from paperroute.render.tile_codec import pack_bits, pack_grey
from tests.core.factories import pin_of, place

REAL = Path(__file__).parent / 'fixtures' / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=SYDNEY)
PIN = pin_of('215020', '200060')


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

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        Place any stop with no position, named after its ID.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace
            The stop, without a point.
        """
        return StopPlace(stop_id=stop_id, name=stop_id, point=None)

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Find no place.

        Parameters
        ----------
        place : StopPlace
            Place to find.

        Returns
        -------
        StopPlace | None
            Always None.
        """
        return None

    def version(self) -> str | None:
        """
        Name no build.

        Returns
        -------
        str | None
            Always None.
        """
        return None

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Know no shapes.

        Parameters
        ----------
        shape_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, Shape]
            Nothing.
        """
        return {}

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Know no trips.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            Nothing.
        """
        return {}

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Know no routes.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, str]
            Nothing.
        """
        return {}

    def modes_near(self, *, box: MetreBox) -> frozenset[Mode]:
        """
        Know no shapes.

        Parameters
        ----------
        box : MetreBox
            Ignored.

        Returns
        -------
        frozenset[Mode]
            Nothing.
        """
        return frozenset()

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
            maps=MapRenderer(tiles=ports.tiles),
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
    """An observer that counts feed_parsed and ticked calls."""

    feeds: int = 0
    ticks: int = 0

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
        maps=MapRenderer(tiles=ports.tiles),
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


async def test_a_queued_tick_that_is_never_handled_sends_no_watchdog(
) -> None:
    """The watchdog follows handled ticks, not queued ones."""
    observer = CallRecorder()
    ports = Ports(
        timetable=BrokenTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        observer=observer,
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(ports=ports, queue=queue, every=60.0, pump=None)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert queue.qsize() == 1
    assert observer.ticks == 0


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
        observer=observer,
    )
    try:
        await run(ports=ports, pin=PIN, tick_every=0.01, max_events=20)
    finally:
        timetable.close()
    assert observer.ticks >= 1


MAP_PIN = pin_of('2145615', '215020')
MAP_MPP = 8
BUS_POSITIONS = 'v1/gtfs/vehiclepos/buses'
STOP_POINT: Point = to_metres(lon=151.0036, lat=-33.8170)


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

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Keep a frame; after the first, press the bottom knob.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        """
        if not self.frames and self.post is not None:
            self.post(KnobPressed(knob=Knob.BOTTOM))
        super().show(frame=frame)

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
    Write a tiles file with one LIGHT tile under a point and open it.

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
    col, row = tile_of(pixel=to_pixels(point=point, mpp=MAP_MPP))
    grey = np.full((TILE, TILE), LIGHT, dtype=np.uint8)
    with TileWriter(out=out) as writer:
        writer.add_tile(
            zoom=MAP_MPP,
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
        tiles=tiles,
    )
    try:
        await run(
            ports=ports,
            pin=MAP_PIN,
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
        self, *, zoom: int, box: tuple[float, float, float, float]
    ) -> list[StreetLabel]:
        """
        Know no labels.

        Parameters
        ----------
        zoom : int
            Ignored.
        box : tuple[float, float, float, float]
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


def map_view(*, markers: tuple[Marker, ...] = ()) -> MapView:
    """
    Make a Map view at the 606 stop's area.

    Parameters
    ----------
    markers : tuple[Marker, ...]
        Vehicles drawn.

    Returns
    -------
    MapView
        The view.
    """
    return MapView(
        origin='Here',
        destination='There',
        clock='6:25',
        base=MapBase(centre=STOP_POINT, mpp=MAP_MPP, route=()),
        zoom=2,
        markers=markers,
        sidebar=(),
        banner=None,
        empty=None,
    )


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


def test_tiles_replaced_rebuilds_base() -> None:
    """A new tiles build composes the base again."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.reads
    tiles.built = 'v2'
    maps.frame(view=map_view())
    assert tiles.reads == 2 * once


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
    )
    await asyncio.gather(*tasks)
    posted = queue.get_nowait()
    assert posted == MapDataFailed(request=request)
    assert posted.kind == MapData.PATTERNS


@dataclass
class PlacedTimetable(BrokenTimetable):
    """A failing timetable whose stops all sit at the test point."""

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        Place any stop at the test point.

        Parameters
        ----------
        stop_id : str
            Stop ID.

        Returns
        -------
        StopPlace
            The stop, named after its ID.
        """
        return StopPlace(stop_id=stop_id, name=stop_id, point=STOP_POINT)


def test_starting_state_fills_the_pins_places() -> None:
    """The pin's places get their names and points from the timetable."""
    ports = Ports(
        timetable=PlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        tiles=NullTiles(),
    )
    state = starting_state(ports=ports, pin=MAP_PIN)
    assert state.pin.origin == place(
        '2145615', name='2145615', point=STOP_POINT,
    )
    assert state.pin.destination == place(
        '215020', name='215020', point=STOP_POINT,
    )
    assert state.map.centre == STOP_POINT
    assert state.now == AT


def test_starting_state_keeps_no_destination() -> None:
    """A pin without a Destination starts with its From filled in."""
    ports = Ports(
        timetable=PlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=FrameRecorder(),
        clock=FixedClock(at=AT),
        tiles=NullTiles(),
    )
    state = starting_state(ports=ports, pin=pin_of('2145615', None))
    assert state.pin.origin == place(
        '2145615', name='2145615', point=STOP_POINT,
    )
    assert state.pin.destination is None
    assert state.map.centre == STOP_POINT


async def test_run_draws_the_map_in_the_variant_asked_for() -> None:
    """The tiles variant given to run is the one the Map reads."""
    tiles = CountingTiles()
    display = PressingDisplay()
    ports = Ports(
        timetable=PlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=FixedClock(at=AT),
        tiles=tiles,
    )
    await run(
        ports=ports,
        pin=MAP_PIN,
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
        Know trip t1.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.
        around : date
            Service date.

        Returns
        -------
        dict[str, TripPattern]
            t1 when asked for.
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
        return {'t1': trip} if 't1' in trip_ids else {}

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
    )
    assert loaded.trip_ids == {'t1', 't2'}
    assert set(loaded.patterns) == {'t1', 't2'}
    assert loaded.patterns['t2'] is None
    found = loaded.patterns['t1']
    assert found is not None
    assert found.along == pytest.approx((0.0, 100.0))
    assert set(loaded.shapes) == {'s1'}


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
class EmptyPlacedTimetable(PlacedTimetable):
    """A placed timetable with no departures."""

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Find nothing.

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
            Nothing.
        """
        return []


@dataclass
class ZoomingDisplay(PressingDisplay):
    """A display that shows the Map, then zooms in on the next frames."""

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Keep a frame; press the bottom knob, then turn it twice.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        """
        if self.post is not None and 1 <= len(self.frames) <= 2:
            self.post(KnobTurned(knob=Knob.BOTTOM, steps=-1))
        super().show(frame=frame)


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
        timetable=EmptyPlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=FixedClock(at=AT),
        tiles=tiles,
    )
    with caplog.at_level(logging.WARNING), closing(tiles):
        state = await run(
            ports=ports,
            pin=MAP_PIN,
            tick_every=0.01,
            post_hook=display.hook,
            max_events=30,
        )
    assert state.running
    assert state.map.zoom != 2
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
