"""The reads and writes behind the effects, done off the event loop."""

import asyncio
import functools
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Final

from paperroute_transit.app_connections import between_read
from paperroute_transit.core.alert_model import (
    AlertPlaces,
    AlertsFetched,
    AlertsSnapshot,
    BetweenLoaded,
    Informed,
    LoadBetween,
)
from paperroute_transit.core.feed_plan import FeedKind, FeedParsers, FeedPlan
from paperroute_transit.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    Event,
    FeedFetched,
    FetchFeed,
    HolidaysLoaded,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadHolidays,
    LoadLine,
    LoadModes,
    LoadPatterns,
    LoadServiceSpan,
    LoadShapes,
    LoadStrip,
    MapRequest,
    MatchPlaces,
    ModesLoaded,
    PatternsLoaded,
    PlacesFailed,
    PlacesMatched,
    PositionsFetched,
    SaveState,
    ServiceSpanLoaded,
    ShapesLoaded,
    StripFailed,
    StripLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.positions import PositionsSnapshot, with_labels
from paperroute_transit.core.reach import build_reach
from paperroute_transit.measures import StopMeasures
from paperroute_transit.ports import (
    NetworkRepository,
    StateRepository,
    TimetableRepository,
)

logger = logging.getLogger(__name__)

MS_PER_SECOND: Final[float] = 1000.0

WorkerEffect = (
    MatchPlaces
    | LoadStrip
    | LoadColumn
    | LoadLine
    | LoadChooserDepartures
    | LoadHolidays
    | LoadServiceSpan
    | LoadBetween
)


async def timed_async[T](*, call: Awaitable[T]) -> tuple[T, float]:
    """
    Await a call and measure how long it took.

    Parameters
    ----------
    call : Awaitable[T]
        Awaitable to run.

    Returns
    -------
    tuple[T, float]
        The result and the elapsed milliseconds.
    """
    started = time.perf_counter()
    result = await call
    return result, (time.perf_counter() - started) * MS_PER_SECOND


class StateSaver:
    """
    Write the saved state, logging each kind of write error once.

    Errors of the same type, number and text are one kind, whatever file
    they name, since each save writes a new temporary file.

    Parameters
    ----------
    states : StateRepository
        Where the state is kept.
    """

    def __init__(self, *, states: StateRepository) -> None:
        self.states = states
        self.logged: set[tuple[type[OSError], int | None, str | None]] = (
            set()
        )

    async def save(self, *, effect: SaveState) -> None:
        """
        Write the state in a worker thread; a write error is never raised.

        Parameters
        ----------
        effect : SaveState
            What to write.
        """
        try:
            await asyncio.to_thread(self.states.save, saved=effect.saved)
        except OSError as error:
            kind = (type(error), error.errno, error.strerror)
            if kind not in self.logged:
                self.logged.add(kind)
                logger.exception('saving state failed')


class VersionWatch:
    """
    Notice when the timetable build changes.

    A read error is logged once until a read works again.

    Parameters
    ----------
    timetable : TimetableRepository
        The timetable watched.
    """

    def __init__(self, *, timetable: TimetableRepository) -> None:
        self.timetable = timetable
        self.seen: TimetableSeen | None = None
        self.failing = False

    async def check(self) -> TimetableSeen | None:
        """
        Read what the timetable records, giving an event on a change.

        Returns
        -------
        TimetableSeen | None
            The version, schema, origin and last day the first time and
            on a change; None when unchanged or unreadable.
        """
        try:
            seen = await asyncio.to_thread(self.read)
        except sqlite3.Error:
            if not self.failing:
                logger.exception('reading the timetable version failed')
            self.failing = True
            return None
        self.failing = False
        if self.seen == seen:
            return None
        self.seen = seen
        return seen

    def read(self) -> TimetableSeen:
        """
        Read the build time, schema version, projection origin and window.

        Returns
        -------
        TimetableSeen
            What the file records.
        """
        return TimetableSeen(
            version=self.timetable.version(),
            schema=self.timetable.schema_version(),
            origin=self.timetable.origin(),
            last_day=self.timetable.last_day(),
        )


def matches(
    *, timetable: TimetableRepository, places: Sequence[StopPlace]
) -> PlacesMatched:
    """
    Look places up in the current timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    places : Sequence[StopPlace]
        Places from an earlier timetable.

    Returns
    -------
    PlacesMatched
        Each place's match, or None, by its old stop ID.
    """
    return PlacesMatched(
        found={
            place.stop_id: timetable.match_stop(place=place)
            for place in places
        }
    )


def strip_built(
    *, network: NetworkRepository, effect: LoadStrip
) -> StripLoaded:
    """
    Gather a strip's runs and lay out its reach, logging how long it took.

    Parameters
    ----------
    network : NetworkRepository
        Runs.
    effect : LoadStrip
        Request.

    Returns
    -------
    StripLoaded
        The reach, or None for a timetable without run tables.
    """
    started = time.perf_counter()
    data = network.reach_input(key=effect.key, origin=effect.origin)
    reach = None if data is None else build_reach(data=data)
    elapsed = (time.perf_counter() - started) * MS_PER_SECOND
    logger.info(
        'strip built',
        extra={'ms': elapsed, 'station': effect.key.station},
    )
    return StripLoaded(key=effect.key, reach=reach, timetable=effect.timetable)


def chooser_departures(
    *, timetable: TimetableRepository, effect: LoadChooserDepartures
) -> ChooserDeparturesLoaded:
    """
    Read the departures from the chooser's From.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadChooserDepartures
        Request.

    Returns
    -------
    ChooserDeparturesLoaded
        The departures, up to the end asked for.
    """
    found = timetable.departures(
        pin=effect.pin, start=effect.start, end=effect.end
    )
    return ChooserDeparturesLoaded(
        pin=effect.pin,
        departures=tuple(found),
        until=effect.end,
        timetable=effect.timetable,
    )


def column_loaded(
    *, network: NetworkRepository, effect: LoadColumn
) -> ColumnLoaded:
    """
    Read the chooser column after a path.

    Parameters
    ----------
    network : NetworkRepository
        Chooser catalogue.
    effect : LoadColumn
        Request.

    Returns
    -------
    ColumnLoaded
        The column.
    """
    return ColumnLoaded(
        path=effect.path,
        column=network.choices(path=effect.path),
        timetable=effect.timetable,
    )


def line_loaded(*, network: NetworkRepository, effect: LoadLine) -> LineLoaded:
    """
    Read a towards row's stations.

    Parameters
    ----------
    network : NetworkRepository
        Chooser catalogue.
    effect : LoadLine
        Request.

    Returns
    -------
    LineLoaded
        The stations.
    """
    return LineLoaded(
        towards=effect.towards,
        line=network.towards_line(towards=effect.towards),
        timetable=effect.timetable,
    )


def holidays_read(
    *, timetable: TimetableRepository, version: str | None
) -> HolidaysLoaded:
    """
    Read the public holidays a timetable records.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    version : str | None
        The version asked for, echoed back.

    Returns
    -------
    HolidaysLoaded
        The holidays.
    """
    return HolidaysLoaded(dates=timetable.holidays(), timetable=version)


def span_read(
    *, timetable: TimetableRepository, effect: LoadServiceSpan
) -> ServiceSpanLoaded:
    """
    Read when a pin's trips run on a service day.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadServiceSpan
        Request, echoed back.

    Returns
    -------
    ServiceSpanLoaded
        The span.
    """
    return ServiceSpanLoaded(
        pin=effect.pin,
        day=effect.day,
        span=timetable.service_span(pin=effect.pin, day=effect.day),
        timetable=effect.timetable,
    )


def calendar_read(
    *,
    timetable: TimetableRepository,
    effect: LoadHolidays | LoadServiceSpan,
) -> Callable[[], Event]:
    """
    Give the read a calendar request asks for.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadHolidays | LoadServiceSpan
        Request.

    Returns
    -------
    Callable[[], Event]
        The read, giving its result event.
    """
    if isinstance(effect, LoadServiceSpan):
        return functools.partial(span_read, timetable=timetable, effect=effect)
    return functools.partial(
        holidays_read, timetable=timetable, version=effect.timetable
    )


def calendar_failure(*, effect: LoadHolidays | LoadServiceSpan) -> Event:
    """
    Give the event posted when a calendar read fails.

    Parameters
    ----------
    effect : LoadHolidays | LoadServiceSpan
        Request.

    Returns
    -------
    Event
        No holidays, or no span, tagged with the request's timetable.
    """
    if isinstance(effect, LoadServiceSpan):
        return ServiceSpanLoaded(
            pin=effect.pin,
            day=effect.day,
            span=None,
            timetable=effect.timetable,
        )
    return HolidaysLoaded(dates=frozenset(), timetable=effect.timetable)


def timetable_read(
    *,
    timetable: TimetableRepository,
    effect: LoadHolidays | LoadServiceSpan | LoadBetween,
) -> Callable[[], Event]:
    """
    Give the read a calendar or stations-between request asks for.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadHolidays | LoadServiceSpan | LoadBetween
        Request.

    Returns
    -------
    Callable[[], Event]
        The read, giving its result event.
    """
    if isinstance(effect, LoadBetween):
        return functools.partial(
            between_read, timetable=timetable, effect=effect
        )
    return calendar_read(timetable=timetable, effect=effect)


def timetable_failure(
    *, effect: LoadHolidays | LoadServiceSpan | LoadBetween
) -> Event:
    """
    Give the event posted when a calendar or stations-between read fails.

    Parameters
    ----------
    effect : LoadHolidays | LoadServiceSpan | LoadBetween
        Request.

    Returns
    -------
    Event
        No holidays, no span or no names, tagged with the request's
        timetable.
    """
    if isinstance(effect, LoadBetween):
        return BetweenLoaded(
            pin=effect.pin, names=frozenset(), timetable=effect.timetable
        )
    return calendar_failure(effect=effect)


def read_for(
    *,
    timetable: TimetableRepository,
    network: NetworkRepository,
    effect: WorkerEffect,
) -> Callable[[], Event]:
    """
    Give the read an effect asks for.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    network : NetworkRepository
        Chooser catalogue and runs.
    effect : WorkerEffect
        Request.

    Returns
    -------
    Callable[[], Event]
        The read, giving its result event.
    """
    match effect:
        case MatchPlaces(places=places):
            return functools.partial(
                matches, timetable=timetable, places=places
            )
        case LoadStrip():
            return functools.partial(
                strip_built, network=network, effect=effect
            )
        case LoadColumn():
            return functools.partial(
                column_loaded, network=network, effect=effect
            )
        case LoadLine():
            return functools.partial(
                line_loaded, network=network, effect=effect
            )
        case LoadHolidays() | LoadServiceSpan() | LoadBetween():
            return timetable_read(timetable=timetable, effect=effect)
    return functools.partial(
        chooser_departures, timetable=timetable, effect=effect
    )


def failure_of(*, effect: WorkerEffect) -> Event:
    """
    Give the event posted when an effect's read fails.

    Parameters
    ----------
    effect : WorkerEffect
        Request.

    Returns
    -------
    Event
        Its failure event, tagged with the request's timetable version.
    """
    match effect:
        case MatchPlaces(timetable=version):
            return PlacesFailed(timetable=version)
        case LoadStrip(key=key, timetable=version):
            return StripFailed(key=key, timetable=version)
        case LoadColumn(path=path, timetable=version):
            return ColumnFailed(path=path, timetable=version)
        case LoadLine(towards=towards, timetable=version):
            return LineFailed(towards=towards, timetable=version)
        case LoadHolidays() | LoadServiceSpan() | LoadBetween():
            return timetable_failure(effect=effect)
    return ChooserDeparturesFailed(pin=effect.pin, timetable=effect.timetable)


def work_for(
    *,
    timetable: TimetableRepository,
    network: NetworkRepository,
    effect: WorkerEffect,
) -> tuple[Callable[[], Event], Event]:
    """
    Give the read an effect asks for, and the event posted if it fails.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    network : NetworkRepository
        Chooser catalogue and runs.
    effect : WorkerEffect
        Request.

    Returns
    -------
    tuple[Callable[[], Event], Event]
        The read, giving its result event, and the failure event.
    """
    read = read_for(timetable=timetable, network=network, effect=effect)
    return read, failure_of(effect=effect)


async def worked(
    *,
    call: Callable[[], Event],
    failed: Event,
    queue: asyncio.Queue[Event],
) -> None:
    """
    Run a read in a worker thread and post its result or its failure.

    Parameters
    ----------
    call : Callable[[], Event]
        The read.
    failed : Event
        Posted, after logging, when the read fails.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    try:
        event = await asyncio.to_thread(call)
    except sqlite3.Error:
        logger.exception('reading the timetable failed')
        await queue.put(failed)
        return
    await queue.put(event)


def labelled_positions(
    *,
    timetable: TimetableRepository,
    parsers: FeedParsers,
    data: bytes,
    fetched_at: datetime,
) -> PositionsSnapshot:
    """
    Parse a positions feed and label its vehicles from the timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Where route labels come from.
    parsers : FeedParsers
        The feed's readers.
    data : bytes
        Feed body.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    PositionsSnapshot
        Vehicles labelled with their routes' short names where known.
    """
    snapshot = parsers.positions(data=data, fetched_at=fetched_at)
    labels = timetable.route_labels(
        trip_ids={vehicle.trip_id for vehicle in snapshot.vehicles}
    )
    return with_labels(snapshot=snapshot, labels=labels)


def informed_with(
    *, item: Informed, places: AlertPlaces, line: str | None
) -> Informed:
    """
    Fill in an informed entity's route and stops.

    Parameters
    ----------
    item : Informed
        The entity as the feed gave it.
    places : AlertPlaces
        What the timetable knows about the feed's IDs.
    line : str | None
        The one line of a feed that serves a single line, which its
        routes the timetable does not resolve are; None otherwise.

    Returns
    -------
    Informed
        The entity with its route short name, timetable route ID and
        stop family, where known.
    """
    route = places.routes.get(item.route_id or '')
    if route is None and item.route_id:
        route = line
    return replace(
        item,
        route=route,
        stops=places.stops.get(item.stop_id or '', frozenset()),
        line=places.lines.get(item.route_id or ''),
    )


def labelled_alerts(
    *,
    timetable: TimetableRepository,
    plan: FeedPlan,
    data: bytes,
    feed: str,
    fetched_at: datetime,
) -> AlertsSnapshot:
    """
    Parse an alerts feed and fill in its routes and stops.

    Parameters
    ----------
    timetable : TimetableRepository
        Where route short names and stop families come from.
    plan : FeedPlan
        The city's feeds: the feed's reader, and whether it serves one
        line.
    data : bytes
        Feed body.
    feed : str
        Feed path.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    AlertsSnapshot
        The alerts, each entity carrying its route short name and stop
        family where the timetable knows them.
    """
    snapshot = plan.parsers(feed=feed).alerts(
        data=data, feed=feed, fetched_at=fetched_at,
    )
    entities = [item for alert in snapshot.alerts for item in alert.informed]
    route_type = plan.single_line_type(feed=feed)
    places = timetable.alert_places(
        route_ids={item.route_id for item in entities if item.route_id},
        stop_ids={item.stop_id for item in entities if item.stop_id},
        route_type=route_type,
    )
    line = None if route_type is None else places.only_line
    return replace(
        snapshot,
        alerts=tuple(
            replace(
                alert,
                informed=tuple(
                    informed_with(item=item, places=places, line=line)
                    for item in alert.informed
                ),
            )
            for alert in snapshot.alerts
        ),
    )


def parse_feed(
    *,
    timetable: TimetableRepository,
    plan: FeedPlan,
    effect: FetchFeed,
    data: bytes,
    fetched_at: datetime,
) -> tuple[FeedFetched | PositionsFetched | AlertsFetched, int]:
    """
    Parse a downloaded feed into the event that carries it.

    Parameters
    ----------
    timetable : TimetableRepository
        Where vehicles' route labels come from.
    plan : FeedPlan
        The city's feeds, which give each feed's readers.
    effect : FetchFeed
        The fetch request.
    data : bytes
        Feed body.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    tuple[FeedFetched | PositionsFetched | AlertsFetched, int]
        The event, and how many trips, vehicles or alerts it holds.
    """
    if effect.kind is FeedKind.ALERTS:
        alerts = labelled_alerts(
            timetable=timetable,
            plan=plan,
            data=data,
            feed=effect.feed,
            fetched_at=fetched_at,
        )
        return AlertsFetched(feed=effect.feed, snapshot=alerts), len(
            alerts.alerts
        )
    parsers = plan.parsers(feed=effect.feed)
    if effect.kind is FeedKind.POSITIONS:
        positions = labelled_positions(
            timetable=timetable,
            parsers=parsers,
            data=data,
            fetched_at=fetched_at,
        )
        fetched = PositionsFetched(feed=effect.feed, snapshot=positions)
        return fetched, len(positions.vehicles)
    trips = parsers.trips(data=data, fetched_at=fetched_at)
    return FeedFetched(feed=effect.feed, snapshot=trips), len(trips.trips)


def loaded_patterns(
    *,
    timetable: TimetableRepository,
    effect: LoadPatterns,
    measures: StopMeasures,
) -> PatternsLoaded:
    """
    Read trip patterns and their shapes, and place the stops on them.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadPatterns
        Request.
    measures : StopMeasures
        Stop places already worked out, reused per shape and stops.

    Returns
    -------
    PatternsLoaded
        Every requested trip; None for trips the timetable doesn't know.
    """
    found = timetable.patterns(trip_ids=effect.trip_ids, around=effect.day)
    shapes = timetable.shapes(
        shape_ids={
            pattern.shape_id
            for pattern in found.values()
            if pattern.shape_id
        }
    )
    patterns = {
        trip_id: (
            measures.measure(
                pattern=found[trip_id],
                shape=shapes.get(found[trip_id].shape_id or ''),
            )
            if trip_id in found
            else None
        )
        for trip_id in effect.trip_ids
    }
    return PatternsLoaded(
        trip_ids=effect.trip_ids, patterns=patterns, shapes=shapes
    )


def map_data(
    *,
    timetable: TimetableRepository,
    effect: MapRequest,
    measures: StopMeasures,
) -> Event:
    """
    Read one kind of map data from the timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : MapRequest
        Request.
    measures : StopMeasures
        Stop places already worked out.

    Returns
    -------
    Event
        ShapesLoaded, PatternsLoaded or ModesLoaded.
    """
    match effect:
        case LoadShapes(shape_ids=shape_ids):
            return ShapesLoaded(shapes=timetable.shapes(shape_ids=shape_ids))
        case LoadPatterns():
            return loaded_patterns(
                timetable=timetable, effect=effect, measures=measures,
            )
        case LoadModes(mpp=mpp, box=box):
            modes = timetable.modes_near(box=box)
            logger.info(
                'modes near',
                extra={'mpp': mpp, 'modes': sorted(modes)},
            )
            return ModesLoaded(mpp=mpp, box=box, modes=modes)


def placed(*, timetable: TimetableRepository, place: StopPlace) -> StopPlace:
    """
    Give a place its name and point from the timetable, when it has them.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    place : StopPlace
        A place, possibly bare.

    Returns
    -------
    StopPlace
        The timetable's stop; the place unchanged when it can't be read.
    """
    try:
        return timetable.stop(stop_id=place.stop_id)
    except (LookupError, sqlite3.Error):
        logger.warning('stop %s not in the timetable', place.stop_id)
        return place


def filled(*, timetable: TimetableRepository, pin: Pin) -> Pin:
    """
    Fill a pin's places in from the timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    pin : Pin
        Pin between bare places.

    Returns
    -------
    Pin
        The pin with its places' names and points.
    """
    destination = pin.destination
    return replace(
        pin,
        origin=placed(timetable=timetable, place=pin.origin),
        destination=(
            None
            if destination is None
            else placed(timetable=timetable, place=destination)
        ),
    )
