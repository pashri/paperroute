"""Tests for the performers that read and write off the event loop."""

import asyncio
import errno
import logging
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from google.transit import gtfs_realtime_pb2

from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import Render, SaveState
from paperroute_device.runtime.loop import run, ticker
from paperroute_device.runtime.outputs import Outputs, carry_out, draw_pending
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.saver import StateSaver
from paperroute_device_testkit.device_fakes import FixedClock
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_sydney.city import (
    SYDNEY,
    SYDNEY_PROJECTION,
    SydneyFeed,
    SydneyMode,
)
from paperroute_sydney.sydney_feeds import SydneyFeeds
from paperroute_testkit.core.factories import pin_of, place
from paperroute_testkit.core.kit_steps import QUIET_PARTS
from paperroute_testkit.fakes import (
    STOP_POINT,
    BrokenTimetable,
    EmptyPlacedTimetable,
    FakeNetwork,
    MemoryStates,
)
from paperroute_testkit.render.factories import (
    STOP_VIEW,
    STRIP_VIEW,
    groups,
    view_of,
)
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.app_effects import VersionWatch, parse_feed
from paperroute_transit.app_ports import Ports
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.core.alert_model import (
    AlertPlaces,
    AlertsFetched,
    AlertsSnapshot,
)
from paperroute_transit.core.chooser import Column, Level
from paperroute_transit.core.feed_plan import FeedKind, FeedParsers
from paperroute_transit.core.geo import MetreBox
from paperroute_transit.core.map_state import area_box
from paperroute_transit.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    FetchFeed,
    HolidaysLoaded,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadHolidays,
    LoadLine,
    LoadModes,
    LoadServiceSpan,
    LoadStrip,
    MatchPlaces,
    ModesLoaded,
    PlacesFailed,
    PlacesMatched,
    ServiceSpanLoaded,
    StripFailed,
    StripLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import Pin, ServiceSpan, StopPlace
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.reach import (
    ReachInput,
    RunTail,
    StripKey,
    build_reach,
)
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.saved import seeded
from paperroute_transit.render.chooser import draw_chooser_view
from paperroute_transit.render.stop_view import draw_stop_view
from paperroute_transit.render.strip_view import draw_strip_view
from paperroute_transit.transit_app import TransitApp
from tests.app_factories import (
    AT,
    MAP_PIN,
    REAL,
    map_view,
    ports_of,
    posted_by,
    strip_map_view,
)


def kit_ports(
    *,
    display: FrameRecorder | None = None,
    states: MemoryStates | None = None,
) -> KitPorts:
    """
    Make the kit's ports on fakes, at the shared test time.

    Parameters
    ----------
    display : FrameRecorder | None
        Display; a new recorder by default.
    states : MemoryStates | None
        Saved state store; an empty one by default.

    Returns
    -------
    KitPorts
        The ports.
    """
    return KitPorts(
        display=display or FrameRecorder(),
        clock=FixedClock(at=AT),
        states=states or MemoryStates(),
    )


async def rendered(
    *, app: TransitApp, ports: KitPorts, outputs: Outputs, render: Render,
) -> None:
    """
    Carry out a Render through the kit's outputs, then draw it.

    Parameters
    ----------
    app : TransitApp
        The board, which draws its own views.
    ports : KitPorts
        The kit's adapters.
    outputs : Outputs
        What the kit's effects draw and write through.
    render : Render
        The render.
    """
    await carry_out(
        effects=(render,),
        ports=ports,
        app=app,
        queue=asyncio.Queue(),
        tasks=set(),
        outputs=outputs,
    )
    await draw_pending(ports=ports, app=app, outputs=outputs)


def sydney_app(*, ports: Ports, seed: Pin | None = None) -> TransitApp:
    """
    Make Sydney's board attached to transit's ports.

    Parameters
    ----------
    ports : Ports
        Transit's adapters.
    seed : Pin | None
        Pin to start with when nothing is saved.

    Returns
    -------
    TransitApp
        The attached app.
    """
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS)
    app.attach(ports=ports, seed=seed)
    return app


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
    app = sydney_app(
        ports=ports_of(timetable=VersionFailingTimetable()), seed=MAP_PIN,
    )
    with caplog.at_level(logging.ERROR):
        kit = await run(
            app=app,
            ports=kit_ports(),
            zone=SYDNEY.zone,
            tick_every=0.01,
            max_events=15,
        )
    assert kit.running
    failures = [
        record
        for record in caplog.records
        if record.message == 'reading the timetable version failed'
    ]
    assert len(failures) == 1


async def test_timetable_version_change_posts_seen() -> None:
    """The ticker posts the version at start and again when it changes."""
    timetable = VersionedTimetable()
    queue: asyncio.Queue[object] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(
            app=sydney_app(ports=ports_of(timetable=timetable)),
            ports=kit_ports(),
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


STRIP_KEY = StripKey(station='C', kind=SydneyMode.TRAIN, routes=())


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


@dataclass
class MixedModesTimetable(EmptyPlacedTimetable):
    """A placed timetable with trains and buses near everywhere."""

    def modes_near(self, *, box: MetreBox) -> frozenset[SydneyFeed]:
        """
        Find a train and a bus.

        Parameters
        ----------
        box : MetreBox
            Ignored.

        Returns
        -------
        frozenset[SydneyFeed]
            Trains and buses.
        """
        return frozenset({SydneyFeed.TRAIN, SydneyFeed.BUS})


async def test_modes_load_logs_modes_sorted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-empty modes set is logged as a sorted list."""
    box = area_box(centre=STOP_POINT, mpp=8)
    with caplog.at_level(logging.INFO):
        posted = await posted_by(
            ports=ports_of(timetable=MixedModesTimetable()),
            effects=[LoadModes(mpp=8, box=box)],
        )
    assert posted == [
        ModesLoaded(
            mpp=8, box=box, modes=frozenset({SydneyFeed.TRAIN, SydneyFeed.BUS}),
        ),
    ]
    [record] = [r for r in caplog.records if r.message == 'modes near']
    assert record.__dict__['modes'] == ['bus', 'train']


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
    ports = kit_ports(display=display)
    app = sydney_app(ports=ports_of())
    outputs = Outputs(saver=StateSaver(states=ports.states))
    chooser = view_of(groups(focused=True))
    views = (STOP_VIEW, map_view(), STRIP_VIEW, strip_map_view(), chooser)
    for view in views:
        await rendered(
            app=app, ports=ports, outputs=outputs, render=Render(view=view),
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

    def save(self, *, saved: KitSaved) -> None:
        """
        Fail like a read-only directory, naming a fresh temporary file.

        Parameters
        ----------
        saved : KitSaved
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
    saved = seeded(pin=MAP_PIN, city=SYDNEY)
    save = SaveState(
        saved=KitSaved(device=saved.settings.device, app=saved),
    )
    saver = StateSaver(states=states)
    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            await saver.save(effect=save)
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


@dataclass
class EndingTimetable(VersionedTimetable):
    """A timetable whose window can change under the same build."""

    ends: date | None = date(2026, 10, 29)

    def last_day(self) -> date | None:
        """
        Name the last day covered.

        Returns
        -------
        date | None
            The day.
        """
        return self.ends


async def test_version_watch_reports_the_last_day() -> None:
    """The last day rides on the event, and a change alone is seen."""
    timetable = EndingTimetable()
    watch = VersionWatch(timetable=timetable)
    first = await watch.check()
    timetable.ends = date(2026, 10, 30)
    second = await watch.check()
    assert (first, second, await watch.check()) == (
        TimetableSeen(version='v1', last_day=date(2026, 10, 29)),
        TimetableSeen(version='v1', last_day=date(2026, 10, 30)),
        None,
    )


@dataclass
class HolidaysFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose holidays can't be read."""

    def holidays(self) -> frozenset[date]:
        """
        Fail like a damaged database file.

        Returns
        -------
        frozenset[date]
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_holidays_read_failure_is_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed holidays read posts no holidays and logs one error."""
    with caplog.at_level(logging.ERROR):
        posted = await posted_by(
            ports=ports_of(timetable=HolidaysFailingTimetable()),
            effects=[LoadHolidays(timetable='v1')],
        )
    assert posted == [HolidaysLoaded(dates=frozenset(), timetable='v1')]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1


@dataclass
class SpanFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose service spans can't be read."""

    def service_span(self, *, pin: Pin, day: date) -> ServiceSpan | None:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        pin : Pin
            Ignored.
        day : date
            Ignored.

        Returns
        -------
        ServiceSpan | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_service_span_read() -> None:
    """A span read posts the timetable's answer for the pin and day."""
    pin = pin_of('2145615', None)
    posted = await posted_by(
        ports=ports_of(timetable=EmptyPlacedTimetable()),
        effects=[LoadServiceSpan(pin=pin, day=date(2026, 10, 16))],
    )
    assert posted == [
        ServiceSpanLoaded(pin=pin, day=date(2026, 10, 16), span=None),
    ]


async def test_service_span_failure_is_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed span read posts no span and logs one error."""
    pin = pin_of('2145615', None)
    with caplog.at_level(logging.ERROR):
        posted = await posted_by(
            ports=ports_of(timetable=SpanFailingTimetable()),
            effects=[
                LoadServiceSpan(pin=pin, day=date(2026, 10, 16), timetable='v'),
            ],
        )
    assert posted == [
        ServiceSpanLoaded(
            pin=pin, day=date(2026, 10, 16), span=None, timetable='v',
        ),
    ]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1


ALERTS_FEED = 'v2/gtfs/alerts/sydneytrains'
METRO_FEED = 'v2/gtfs/alerts/metro'
WESTERN = '5ef3cb29-30b3-5e60-bd7a-343519598275'


@dataclass
class PlacesTimetable(EmptyPlacedTimetable):
    """A timetable that answers alert lookups from fixed places."""

    places: AlertPlaces = AlertPlaces(routes={}, stops={})
    asked: int = 0
    route_types: list[int | None] = field(default_factory=list)

    def alert_places(
        self,
        *,
        route_ids: Collection[str],
        stop_ids: Collection[str],
        route_type: int | None = None,
    ) -> AlertPlaces:
        """
        Give the fixed places and count the question.

        Parameters
        ----------
        route_ids : Collection[str]
            Ignored.
        stop_ids : Collection[str]
            Ignored.
        route_type : int | None
            Noted; the fixed only line is given for type 401 alone.

        Returns
        -------
        AlertPlaces
            The fixed places, with no only line unless `route_type` is
            401.
        """
        self.asked += 1
        self.route_types.append(route_type)
        if route_type == 401:
            return self.places
        return replace(self.places, only_line=None)


def alert_event(
    *, timetable: PlacesTimetable, feed: str, data: bytes
) -> AlertsFetched:
    """
    Parse an alerts feed through `parse_feed`.

    Parameters
    ----------
    timetable : PlacesTimetable
        Answers the lookups.
    feed : str
        Feed path.
    data : bytes
        Feed body.

    Returns
    -------
    AlertsFetched
        The event.
    """
    event, count = parse_feed(
        timetable=timetable,
        plan=SYDNEY.feeds,
        effect=FetchFeed(feed=feed, kind=FeedKind.ALERTS),
        data=data,
        fetched_at=AT,
    )
    assert isinstance(event, AlertsFetched)
    assert count == len(event.snapshot.alerts)
    return event


@dataclass
class RecordingParsers:
    """Readers that note each body they are given and find nothing."""

    read: list[tuple[str, bytes]] = field(default_factory=list)

    def trips(self, *, data: bytes, fetched_at: datetime) -> RealtimeSnapshot:
        """
        Note a trip-updates body.

        Parameters
        ----------
        data : bytes
            Feed body.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        RealtimeSnapshot
            No trips.
        """
        self.read.append(('trips', data))
        return RealtimeSnapshot(fetched_at=fetched_at, trips={})

    def positions(
        self, *, data: bytes, fetched_at: datetime,
    ) -> PositionsSnapshot:
        """
        Note a positions body.

        Parameters
        ----------
        data : bytes
            Feed body.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        PositionsSnapshot
            No vehicles.
        """
        self.read.append(('positions', data))
        return PositionsSnapshot(
            fetched_at=fetched_at, header_time=0, vehicles=(),
        )

    def alerts(
        self, *, data: bytes, feed: str, fetched_at: datetime,
    ) -> AlertsSnapshot:
        """
        Note an alerts body.

        Parameters
        ----------
        data : bytes
            Feed body.
        feed : str
            Feed path.
        fetched_at : datetime
            When it was downloaded.

        Returns
        -------
        AlertsSnapshot
            No alerts.
        """
        self.read.append(('alerts', data))
        return AlertsSnapshot(feed=feed, fetched_at=fetched_at, alerts=())


class RecordingPlan(SydneyFeeds):
    """Sydney's feeds, read by recording readers."""

    def __init__(self, *, recorder: RecordingParsers) -> None:
        super().__init__(projection=SYDNEY_PROJECTION)
        self.recorder = recorder

    def parsers(self, *, feed: str) -> FeedParsers:
        """
        Give the recording readers for any feed.

        Parameters
        ----------
        feed : str
            Feed path, ignored.

        Returns
        -------
        FeedParsers
            The recorder's readers.
        """
        del feed
        return FeedParsers(
            trips=self.recorder.trips,
            positions=self.recorder.positions,
            alerts=self.recorder.alerts,
        )


@pytest.mark.parametrize(('feed', 'kind'), [
    (ALERTS_FEED, FeedKind.ALERTS),
    ('v1/gtfs/vehiclepos/buses', FeedKind.POSITIONS),
    ('v1/gtfs/realtime/buses', FeedKind.TRIPS),
])
def test_parse_uses_feed_parsers(feed: str, kind: FeedKind) -> None:
    """Each feed's body goes to the plan's reader for what it carries."""
    recorder = RecordingParsers()
    event, count = parse_feed(
        timetable=PlacesTimetable(),
        plan=RecordingPlan(recorder=recorder),
        effect=FetchFeed(feed=feed, kind=kind),
        data=b'body',
        fetched_at=AT,
    )
    assert recorder.read == [(kind.value, b'body')]
    assert count == 0
    assert event.feed == feed


def test_alert_feed_parsed_to_event() -> None:
    """An alerts fetch gives AlertsFetched with the routes resolved."""
    timetable = PlacesTimetable(
        places=AlertPlaces(
            routes={'WST_2c': 'T1'},
            stops={},
            lines={'WST_2c': 'sydneytrains:WST_2c'},
        )
    )
    data = (REAL / 'v2_gtfs_alerts_sydneytrains.pb').read_bytes()
    event = alert_event(timetable=timetable, feed=ALERTS_FEED, data=data)
    assert event.feed == ALERTS_FEED
    assert len(event.snapshot.alerts) == 5
    western = next(
        found
        for found in event.snapshot.alerts
        if found.alert_id == WESTERN
    )
    routes = {item.route_id: item.route for item in western.informed}
    assert routes['WST_2c'] == 'T1'
    assert routes['WST_1a'] is None
    lines = {item.route_id: item.line for item in western.informed}
    assert lines['WST_2c'] == 'sydneytrains:WST_2c'
    assert lines['WST_1a'] is None
    assert timetable.asked == 1
    assert timetable.route_types == [None]


def metro_alert(
    *, routes: tuple[str, ...] = ('4T.C.2301', 'SMNW_M1'),
) -> bytes:
    """
    Build a metro alerts body naming some route IDs.

    Parameters
    ----------
    routes : tuple[str, ...]
        Route IDs of its informed entities.

    Returns
    -------
    bytes
        Feed body.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    entity = feed.entity.add()
    entity.id = 'metro-1'
    for route in routes:
        entity.alert.informed_entity.add().route_id = route
    return bytes(feed.SerializeToString())


def test_metro_feed_routes_resolve_to_the_line() -> None:
    """Every route of the metro feed is the single metro line."""
    timetable = PlacesTimetable(
        places=AlertPlaces(routes={}, stops={}, only_line='M1')
    )
    metro = alert_event(
        timetable=timetable, feed=METRO_FEED, data=metro_alert()
    )
    assert [item.route for item in metro.snapshot.alerts[0].informed] == [
        'M1',
        'M1',
    ]
    trains = alert_event(
        timetable=timetable, feed=ALERTS_FEED, data=metro_alert()
    )
    assert [item.route for item in trains.snapshot.alerts[0].informed] == [
        None,
        None,
    ]
    assert timetable.route_types == [401, None]


def test_metro_feed_keeps_resolved_routes() -> None:
    """A metro-feed route the timetable resolves keeps its own name."""
    timetable = PlacesTimetable(
        places=AlertPlaces(routes={'WST_2c': 'T1'}, stops={}, only_line='M1')
    )
    metro = alert_event(
        timetable=timetable,
        feed=METRO_FEED,
        data=metro_alert(routes=('WST_2c', '4T.C.2301')),
    )
    assert [item.route for item in metro.snapshot.alerts[0].informed] == [
        'T1',
        'M1',
    ]


def test_alert_stops_filled_from_families() -> None:
    """An informed stop carries its family; an unknown one carries none."""
    family = frozenset({'200060', '2000321'})
    timetable = PlacesTimetable(
        places=AlertPlaces(routes={}, stops={'200060': family})
    )
    data = (REAL / 'v2_gtfs_alerts_sydneytrains.pb').read_bytes()
    event = alert_event(timetable=timetable, feed=ALERTS_FEED, data=data)
    stops = {
        item.stop_id: item.stops
        for found in event.snapshot.alerts
        for item in found.informed
        if item.stop_id
    }
    assert stops['200060'] == family
    assert stops['213510'] == frozenset()


@dataclass
class AlertFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose alert lookup fails."""

    def alert_places(
        self,
        *,
        route_ids: Collection[str],
        stop_ids: Collection[str],
        route_type: int | None = None,
    ) -> AlertPlaces:
        """
        Fail.

        Parameters
        ----------
        route_ids : Collection[str]
            Ignored.
        stop_ids : Collection[str]
            Ignored.
        route_type : int | None
            Ignored.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_alert_fetch_failure_posts_feed_failed() -> None:
    """A failing timetable lookup posts FeedFailed for the feed."""
    ports = ports_of(timetable=AlertFailingTimetable())
    posted = await posted_by(
        ports=ports,
        effects=[FetchFeed(feed=ALERTS_FEED, kind=FeedKind.ALERTS)],
    )
    assert [type(event).__name__ for event in posted] == ['FeedFailed']
