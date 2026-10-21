"""Tests for the performers that read and write off the event loop."""

import asyncio
import errno
import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pytest

from paperroute.adapters.tiles_db import NullTiles
from paperroute.app import MapRenderer, run, ticker
from paperroute.app_effects import VersionWatch
from paperroute.core.chooser import Column, Level
from paperroute.core.map_state import area_box
from paperroute.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    Event,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadLine,
    LoadModes,
    LoadStrip,
    MatchPlaces,
    ModesLoaded,
    PlacesFailed,
    PlacesMatched,
    Render,
    SaveState,
    StripFailed,
    StripLoaded,
    TimetableSeen,
)
from paperroute.core.model import RouteKind, StopPlace
from paperroute.core.reach import ReachInput, RunTail, StripKey, build_reach
from paperroute.core.saved import Saved, seeded
from paperroute.render.chooser import draw_chooser_view
from paperroute.render.stop_view import draw_stop_view
from paperroute.render.strip_view import draw_strip_view
from tests.app_factories import (
    AT,
    MAP_PIN,
    map_view,
    ports_of,
    posted_by,
    strip_map_view,
)
from tests.core.factories import place
from tests.fakes import (
    STOP_POINT,
    BrokenTimetable,
    EmptyPlacedTimetable,
    FakeNetwork,
    FrameRecorder,
    MemoryStates,
)
from tests.render.factories import STOP_VIEW, STRIP_VIEW, groups, view_of


async def test_save_effect_writes_through_store() -> None:
    """SaveState writes the saved state to the store and posts nothing."""
    states = MemoryStates()
    saved = seeded(pin=MAP_PIN)
    posted = await posted_by(
        ports=ports_of(states=states), effects=[SaveState(saved=saved)]
    )
    assert states.saves == [saved]
    assert not posted


async def test_save_failure_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing write is logged once for the same message, never raised."""
    states = MemoryStates(error=OSError(30, 'Read-only file system'))
    save = SaveState(saved=seeded(pin=MAP_PIN))
    with caplog.at_level(logging.ERROR):
        await posted_by(ports=ports_of(states=states), effects=[save, save])
    failures = [
        record
        for record in caplog.records
        if record.message == 'saving state failed'
    ]
    assert len(failures) == 1


@dataclass
class VersionedTimetable(EmptyPlacedTimetable):
    """A timetable whose build can be swapped."""

    built: str | None = 'v1'

    def version(self) -> str | None:
        """
        Name the current build.

        Returns
        -------
        str | None
            The build.
        """
        return self.built


@dataclass
class VersionFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose version read fails."""

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
        raise sqlite3.OperationalError('disk I/O error')


async def test_version_error_does_not_quit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing version read is logged once and the app keeps running."""
    with caplog.at_level(logging.ERROR):
        state = await run(
            ports=ports_of(timetable=VersionFailingTimetable()),
            seed=MAP_PIN,
            tick_every=0.01,
            max_events=15,
        )
    assert state.running
    failures = [
        record
        for record in caplog.records
        if record.message == 'reading the timetable version failed'
    ]
    assert len(failures) == 1


async def test_timetable_version_change_posts_seen() -> None:
    """The ticker posts the version at start and again when it changes."""
    timetable = VersionedTimetable()
    queue: asyncio.Queue[Event] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(
            ports=ports_of(timetable=timetable),
            queue=queue,
            every=0.01,
            pump=None,
        )
    )
    await asyncio.sleep(0.1)
    timetable.built = 'v2'
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    seen = [event for event in posted if isinstance(event, TimetableSeen)]
    assert seen == [TimetableSeen(version='v1'), TimetableSeen(version='v2')]
    assert isinstance(posted[0], TimetableSeen)


def reach_data() -> ReachInput:
    """
    Make the runs C E1 E2 through the From C.

    Returns
    -------
    ReachInput
        The runs.
    """
    places = {
        key: StopPlace(stop_id=key, name=f'{key} Station', point=None)
        for key in ('C', 'E1', 'E2')
    }
    return ReachInput(
        origin=places['C'],
        tails=(RunTail(before=(), after=('E1', 'E2'), trips=8),),
        places=places,
        station_of={key: key for key in places},
    )


STRIP_KEY = StripKey(station='C', kind=RouteKind.TRAIN, routes=())


async def test_load_strip_posts_reach(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LoadStrip builds the reach and echoes the key and timetable."""
    data = reach_data()
    load = LoadStrip(key=STRIP_KEY, origin=data.origin, timetable='v1')
    with caplog.at_level(logging.INFO):
        posted = await posted_by(
            ports=ports_of(network=FakeNetwork(data=data)), effects=[load]
        )
    assert posted == [
        StripLoaded(
            key=STRIP_KEY, reach=build_reach(data=data), timetable='v1'
        )
    ]
    built = [r for r in caplog.records if r.message == 'strip built']
    assert len(built) == 1
    assert built[0].__dict__['ms'] >= 0


async def test_load_strip_without_runs_posts_no_reach() -> None:
    """A timetable with no run tables loads a strip with no reach."""
    load = LoadStrip(key=STRIP_KEY, origin=place('C'), timetable='v1')
    posted = await posted_by(ports=ports_of(), effects=[load])
    assert posted == [StripLoaded(key=STRIP_KEY, reach=None, timetable='v1')]


async def test_modes_load_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each LoadModes logs the modes it found."""
    box = area_box(centre=STOP_POINT, mpp=8)
    with caplog.at_level(logging.INFO):
        posted = await posted_by(
            ports=ports_of(), effects=[LoadModes(mpp=8, box=box)],
        )
    assert posted == [ModesLoaded(mpp=8, box=box, modes=frozenset())]
    [record] = [r for r in caplog.records if r.message == 'modes near']
    assert record.__dict__['mpp'] == 8
    assert record.__dict__['modes'] == []


async def test_load_strip_failure_posts_strip_failed() -> None:
    """A failed runs read posts StripFailed with the timetable echoed."""
    network = FakeNetwork(error=sqlite3.OperationalError('locked'))
    load = LoadStrip(key=STRIP_KEY, origin=place('C'), timetable='v1')
    posted = await posted_by(ports=ports_of(network=network), effects=[load])
    assert posted == [StripFailed(key=STRIP_KEY, timetable='v1')]


async def test_load_chooser_departures_posts_result() -> None:
    """The chooser's departures come back up to the end asked for."""
    end = AT + timedelta(hours=1)
    load = LoadChooserDepartures(
        pin=MAP_PIN, start=AT, end=end, timetable='v1'
    )
    posted = await posted_by(ports=ports_of(), effects=[load])
    assert posted == [
        ChooserDeparturesLoaded(
            pin=MAP_PIN, departures=(), until=end, timetable='v1'
        )
    ]


async def test_load_chooser_departures_failure_posts_failed() -> None:
    """A failed departures read posts ChooserDeparturesFailed."""
    load = LoadChooserDepartures(
        pin=MAP_PIN, start=AT, end=AT, timetable='v1'
    )
    posted = await posted_by(
        ports=ports_of(timetable=BrokenTimetable()), effects=[load]
    )
    assert posted == [ChooserDeparturesFailed(pin=MAP_PIN, timetable='v1')]


async def test_load_column_posts_column() -> None:
    """LoadColumn posts the network's column with the timetable echoed."""
    column = Column(level=Level.KIND, items=())
    network = FakeNetwork(column=column)
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadColumn(path=(), timetable='v1')],
    )
    assert posted == [ColumnLoaded(path=(), column=column, timetable='v1')]


async def test_load_column_failure_posts_column_failed() -> None:
    """A failed column read posts ColumnFailed with the timetable echoed."""
    network = FakeNetwork(error=sqlite3.OperationalError('locked'))
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadColumn(path=(), timetable='v1')],
    )
    assert posted == [ColumnFailed(path=(), timetable='v1')]


async def test_load_line_posts_line_and_failure() -> None:
    """LoadLine posts the stations, or LineFailed when the read fails."""
    line = (place('A'), place('B'))
    load = LoadLine(towards=7, timetable='v1')
    posted = await posted_by(
        ports=ports_of(network=FakeNetwork(line=line)), effects=[load]
    )
    assert posted == [LineLoaded(towards=7, line=line, timetable='v1')]
    broken = FakeNetwork(error=sqlite3.OperationalError('locked'))
    posted = await posted_by(ports=ports_of(network=broken), effects=[load])
    assert posted == [LineFailed(towards=7, timetable='v1')]


@dataclass
class MatchingTimetable(EmptyPlacedTimetable):
    """A timetable that finds stop A again and loses every other stop."""

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Find stop A at the test point.

        Parameters
        ----------
        place : StopPlace
            Place to find.

        Returns
        -------
        StopPlace | None
            Stop A placed; None for any other.
        """
        return self.stop(stop_id='A') if place.stop_id == 'A' else None


async def test_match_places_posts_found() -> None:
    """MatchPlaces posts each place's match by its old stop ID."""
    posted = await posted_by(
        ports=ports_of(timetable=MatchingTimetable()),
        effects=[MatchPlaces(places=(place('A'), place('B')))],
    )
    assert posted == [
        PlacesMatched(
            found={
                'A': StopPlace(stop_id='A', name='A', point=STOP_POINT),
                'B': None,
            }
        )
    ]


async def test_renders_each_view_type() -> None:
    """Every kind of view is drawn by its own renderer and shown."""
    display = FrameRecorder()
    chooser = view_of(groups(focused=True))
    await posted_by(
        ports=ports_of(display=display),
        effects=[
            Render(view=STOP_VIEW),
            Render(view=map_view()),
            Render(view=STRIP_VIEW),
            Render(view=strip_map_view()),
            Render(view=chooser),
        ],
    )
    maps = MapRenderer(tiles=NullTiles())
    expected = [
        draw_stop_view(view=STOP_VIEW),
        maps.frame(view=map_view()),
        draw_strip_view(view=STRIP_VIEW),
        maps.strip_map_frame(view=strip_map_view()),
        draw_chooser_view(view=chooser),
    ]
    assert len(display.frames) == len(expected)
    for frame, want in zip(display.frames, expected, strict=True):
        assert np.array_equal(frame, want)


@dataclass
class TempNamingStates(MemoryStates):
    """A store whose every save fails naming a new temporary file."""

    tries: int = 0

    def save(self, *, saved: Saved) -> None:
        """
        Fail like a read-only directory, naming a fresh temporary file.

        Parameters
        ----------
        saved : Saved
            Ignored.

        Raises
        ------
        OSError
            Always, with a different file name each time.
        """
        self.tries += 1
        raise OSError(
            errno.EROFS,
            'Read-only file system',
            f'/var/lib/paperroute/.state.json.{self.tries}',
        )


async def test_save_failure_naming_new_temp_files_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same write error is logged once whatever temp file it names."""
    states = TempNamingStates()
    save = SaveState(saved=seeded(pin=MAP_PIN))
    with caplog.at_level(logging.ERROR):
        await posted_by(
            ports=ports_of(states=states), effects=[save, save, save]
        )
    failures = [
        record
        for record in caplog.records
        if record.message == 'saving state failed'
    ]
    assert states.tries == 3
    assert len(failures) == 1


@dataclass
class MatchFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose stop look-ups fail."""

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        place : StopPlace
            Ignored.

        Returns
        -------
        StopPlace | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_match_places_failure_posts_places_failed() -> None:
    """A failed look-up posts PlacesFailed with the timetable echoed."""
    posted = await posted_by(
        ports=ports_of(timetable=MatchFailingTimetable()),
        effects=[MatchPlaces(places=(place('A'),), timetable='v1')],
    )
    assert posted == [PlacesFailed(timetable='v1')]


@dataclass
class SchemaTimetable(VersionedTimetable):
    """A timetable whose schema can change under the same build."""

    schema: str | None = '4'

    def schema_version(self) -> str | None:
        """
        Name the current schema.

        Returns
        -------
        str | None
            The schema.
        """
        return self.schema


async def test_version_watch_reports_schema() -> None:
    """The schema rides on the event, and a schema change alone is seen."""
    timetable = SchemaTimetable()
    watch = VersionWatch(timetable=timetable)
    first = await watch.check()
    timetable.schema = '5'
    second = await watch.check()
    assert (first, second, await watch.check()) == (
        TimetableSeen(version='v1', schema='4'),
        TimetableSeen(version='v1', schema='5'),
        None,
    )
