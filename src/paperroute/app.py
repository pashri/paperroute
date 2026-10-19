"""Wire the ports to the reducer through one event queue."""

import asyncio
import functools
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.gtfs_rt import parse_positions, parse_trip_updates
from paperroute.adapters.tfnsw import FeedError
from paperroute.adapters.tiles_db import NullTiles
from paperroute.core.feeds import FeedKind
from paperroute.core.map_view import MapBase, MapView
from paperroute.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    LoadChooserDepartures,
    LoadColumn,
    LoadDepartures,
    LoadLine,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    LoadStrip,
    MapDataFailed,
    MapRequest,
    MatchPlaces,
    ModesLoaded,
    PatternsLoaded,
    PositionsFetched,
    QuitRequested,
    Render,
    SaveState,
    ShapesLoaded,
    Stop,
    Tick,
)
from paperroute.core.model import Pin
from paperroute.core.positions import PositionsSnapshot, with_labels
from paperroute.core.saved import seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import StripMapView
from paperroute.core.tiles import TileVariant
from paperroute.measures import StopMeasures
from paperroute.observers import NullObserver, ObserverGroup
from paperroute.ports import (
    Clock,
    Display,
    FeedsClient,
    Observer,
    TileRepository,
    TimetableRepository,
)
from paperroute.render.map_base import draw_map_base
from paperroute.render.map_view import draw_map_view
from paperroute.render.stop_view import draw_stop_view
from paperroute.render.strip_view import draw_strip_map_view

logger = logging.getLogger(__name__)

MS_PER_SECOND: Final[float] = 1000.0


@dataclass(frozen=True)
class Ports:
    """The adapters the app runs on."""

    timetable: TimetableRepository
    feeds: FeedsClient
    display: Display
    clock: Clock
    observer: Observer = field(default_factory=NullObserver)
    tiles: TileRepository = field(default_factory=NullTiles)
    measures: StopMeasures = field(default_factory=StopMeasures)


class MapRenderer:
    """
    Draw Map view frames, keeping the last map base for the next frame.

    A tiles file that fails a read is drawn as no tiles at all.

    Parameters
    ----------
    tiles : TileRepository
        Where the tiles come from.
    variant : TileVariant
        Which tile rendering to draw.
    """

    def __init__(
        self,
        *,
        tiles: TileRepository,
        variant: TileVariant = TileVariant.GREY,
    ) -> None:
        self.tiles = tiles
        self.variant = variant
        self.key: tuple[MapBase, str | None] | None = None
        self.base: NDArray[np.uint8] | None = None
        self.unusable: set[str | None] = set()

    def frame(self, *, view: MapView) -> NDArray[np.uint8]:
        """
        Draw one frame, drawing the map base again only when it changed.

        Parameters
        ----------
        view : MapView
            View to draw.

        Returns
        -------
        NDArray[np.uint8]
            480 by 800 pixels.
        """
        return draw_map_view(view=view, base=self.cached(base=view.base))

    def strip_map_frame(self, *, view: StripMapView) -> NDArray[np.uint8]:
        """
        Draw one Strip + map frame, sharing the cached map base.

        Parameters
        ----------
        view : StripMapView
            View to draw.

        Returns
        -------
        NDArray[np.uint8]
            480 by 800 pixels.
        """
        return draw_strip_map_view(
            view=view, base=self.cached(base=view.map.base),
        )

    def cached(self, *, base: MapBase) -> NDArray[np.uint8]:
        """
        Give the map base, drawing it again only when it changed.

        Parameters
        ----------
        base : MapBase
            The base map key, which carries the width.

        Returns
        -------
        NDArray[np.uint8]
            The map base for the installed tiles.
        """
        key = (base, self.version())
        if self.base is None or key != self.key:
            self.base = self.drawn_base(base=base, version=key[1])
            self.key = key
        return self.base

    def version(self) -> str | None:
        """
        Say which tiles build is installed.

        Returns
        -------
        str | None
            The build time, or None when there is none or it can't be read.
        """
        try:
            return self.tiles.version()
        except sqlite3.Error:
            return None

    def drawn_base(
        self, *, base: MapBase, version: str | None
    ) -> NDArray[np.uint8]:
        """
        Draw the map base, without tiles when the tiles can't be read.

        A read error is logged once for each tiles build.

        Parameters
        ----------
        base : MapBase
            The base map key.
        version : str | None
            The tiles build drawn from.

        Returns
        -------
        NDArray[np.uint8]
            The map base; it says "Map not installed" after a read error.
        """
        try:
            return draw_map_base(
                base=base, tiles=self.tiles, variant=self.variant
            )
        except sqlite3.Error:
            if version not in self.unusable:
                logger.warning('tiles file unusable', exc_info=True)
                self.unusable.add(version)
        return draw_map_base(
            base=base, tiles=NullTiles(), variant=self.variant
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


async def load(
    *, ports: Ports, effect: LoadDepartures, queue: asyncio.Queue[Event]
) -> None:
    """
    Load departures off the event loop and post the result or the failure.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : LoadDepartures
        Request.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    try:
        found = await asyncio.to_thread(
            ports.timetable.departures,
            pin=effect.pin,
            start=effect.start,
            end=effect.end,
        )
    except sqlite3.Error:
        logger.exception('reading departures failed')
        await queue.put(
            DeparturesFailed(pin=effect.pin, timetable=effect.timetable)
        )
        return
    await queue.put(
        DeparturesLoaded(
            pin=effect.pin,
            departures=tuple(found),
            until=effect.end,
            timetable=effect.timetable,
        )
    )


def labelled_positions(
    *,
    timetable: TimetableRepository,
    data: bytes,
    fetched_at: datetime,
) -> PositionsSnapshot:
    """
    Parse a positions feed and label its vehicles from the timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Where route labels come from.
    data : bytes
        Feed body.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    PositionsSnapshot
        Vehicles labelled with their routes' short names where known.
    """
    snapshot = parse_positions(data=data, fetched_at=fetched_at)
    labels = timetable.route_labels(
        trip_ids={vehicle.trip_id for vehicle in snapshot.vehicles}
    )
    return with_labels(snapshot=snapshot, labels=labels)


def parse_feed(
    *,
    timetable: TimetableRepository,
    effect: FetchFeed,
    data: bytes,
    fetched_at: datetime,
) -> tuple[FeedFetched | PositionsFetched, int]:
    """
    Parse a downloaded feed into the event that carries it.

    Parameters
    ----------
    timetable : TimetableRepository
        Where vehicles' route labels come from.
    effect : FetchFeed
        The fetch request.
    data : bytes
        Feed body.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    tuple[FeedFetched | PositionsFetched, int]
        The event, and how many trips or vehicles it holds.
    """
    if effect.kind is FeedKind.POSITIONS:
        positions = labelled_positions(
            timetable=timetable, data=data, fetched_at=fetched_at
        )
        fetched = PositionsFetched(feed=effect.feed, snapshot=positions)
        return fetched, len(positions.vehicles)
    trips = parse_trip_updates(data=data, fetched_at=fetched_at)
    return FeedFetched(feed=effect.feed, snapshot=trips), len(trips.trips)


async def fetch(
    *, ports: Ports, effect: FetchFeed, queue: asyncio.Queue[Event]
) -> None:
    """
    Fetch and parse a feed, posting success or failure.

    A failed download, a bad body or a failed route labels read all post
    FeedFailed for the feed.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : FetchFeed
        Request.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    try:
        data, fetch_ms = await timed_async(
            call=ports.feeds.fetch(feed=effect.feed)
        )
        (event, count), parse_ms = await timed_async(
            call=asyncio.to_thread(
                parse_feed,
                timetable=ports.timetable,
                effect=effect,
                data=data,
                fetched_at=ports.clock.now(),
            )
        )
    except (FeedError, sqlite3.Error) as error:
        logger.warning('feed failed: %s', error)
        failed = FeedFailed(
            feed=effect.feed, at=ports.clock.now(), reason=str(error)
        )
        await queue.put(failed)
        return
    logger.info(
        'feed parsed',
        extra={
            'feed': effect.feed,
            'fetch_ms': fetch_ms,
            'parse_ms': parse_ms,
            'trips': count,
        },
    )
    ports.observer.feed_parsed(
        feed=effect.feed, fetch_ms=fetch_ms, parse_ms=parse_ms, trips=count
    )
    await queue.put(event)


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
            return ModesLoaded(mpp=mpp, box=box, modes=modes)


async def load_map(
    *, ports: Ports, effect: MapRequest, queue: asyncio.Queue[Event]
) -> None:
    """
    Load map data off the event loop and post the result or the failure.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : MapRequest
        Request.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    try:
        event = await asyncio.to_thread(
            map_data,
            timetable=ports.timetable,
            effect=effect,
            measures=ports.measures,
        )
    except sqlite3.Error:
        logger.exception('reading map data failed')
        await queue.put(MapDataFailed(request=effect))
        return
    await queue.put(event)


async def render(
    *, ports: Ports, view: StopView | MapView, maps: MapRenderer
) -> None:
    """
    Draw a view off the event loop, show it and report how long it took.

    Parameters
    ----------
    ports : Ports
        Adapters.
    view : StopView | MapView
        View to draw.
    maps : MapRenderer
        Draws Map views.
    """
    if isinstance(view, StopView):
        draw = functools.partial(draw_stop_view, view=view)
    else:
        draw = functools.partial(maps.frame, view=view)
    frame, render_ms = await timed_async(call=asyncio.to_thread(draw))
    ports.display.show(frame=frame)
    logger.info('frame drawn', extra={'render_ms': render_ms})
    ports.observer.frame_drawn(render_ms=render_ms)


def background(
    *,
    ports: Ports,
    effect: LoadDepartures | FetchFeed | MapRequest,
    queue: asyncio.Queue[Event],
) -> tuple[Coroutine[Any, Any, None], Event]:
    """
    Make the work for an effect done in the background.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : LoadDepartures | FetchFeed | MapRequest
        Effect.
    queue : asyncio.Queue[Event]
        Event queue.

    Returns
    -------
    tuple[Coroutine[Any, Any, None], Event]
        The work, and the event that lets the app recover if it crashes.
    """
    match effect:
        case LoadDepartures():
            return (
                load(ports=ports, effect=effect, queue=queue),
                DeparturesFailed(pin=effect.pin, timetable=effect.timetable),
            )
        case FetchFeed(feed=feed):
            failed = FeedFailed(
                feed=feed, at=ports.clock.now(), reason='unexpected error'
            )
            return fetch(ports=ports, effect=effect, queue=queue), failed
    return (
        load_map(ports=ports, effect=effect, queue=queue),
        MapDataFailed(request=effect),
    )


async def perform(
    *,
    ports: Ports,
    effect: Effect,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    maps: MapRenderer,
) -> None:
    """
    Carry out one effect.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : Effect
        Effect.
    queue : asyncio.Queue[Event]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    maps : MapRenderer
        Draws Map views.
    """
    match effect:
        case Render(view=StopView() | MapView() as view):
            await render(ports=ports, view=view, maps=maps)
            return
        case (
            Render()
            | Stop()
            | SaveState()
            | MatchPlaces()
            | LoadStrip()
            | LoadColumn()
            | LoadLine()
            | LoadChooserDepartures()
        ):
            return
    work, failed = background(ports=ports, effect=effect, queue=queue)
    task = asyncio.create_task(work)
    tasks.add(task)
    task.add_done_callback(
        functools.partial(finished, tasks=tasks, queue=queue, failed=failed)
    )


def finished(
    task: asyncio.Task[None],
    *,
    tasks: set[asyncio.Task[None]],
    queue: asyncio.Queue[Event],
    failed: Event,
) -> None:
    """
    Forget a finished task; if it crashed, log it and post its failure.

    Parameters
    ----------
    task : asyncio.Task[None]
        The finished task.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    queue : asyncio.Queue[Event]
        Event queue.
    failed : Event
        Event that lets the app recover from this task crashing.
    """
    tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error('background task crashed', exc_info=error)
        queue.put_nowait(failed)


def ticker_finished(
    task: asyncio.Task[None], *, queue: asyncio.Queue[Event]
) -> None:
    """
    If the ticker crashed, log it and post QuitRequested.

    Parameters
    ----------
    task : asyncio.Task[None]
        The finished ticker task.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error('ticker crashed', exc_info=error)
        queue.put_nowait(QuitRequested())


async def ticker(
    *,
    ports: Ports,
    queue: asyncio.Queue[Event],
    every: float,
    pump: Callable[[], None] | None,
) -> None:
    """
    Post ticks and keep the window responsive.

    Parameters
    ----------
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Called often to process window events.
    """
    elapsed = every
    while True:
        if elapsed >= every:
            await queue.put(Tick(now=ports.clock.now()))
            elapsed = 0.0
        if pump:
            pump()
        await asyncio.sleep(0.03)
        elapsed += 0.03


def starting_state(*, ports: Ports, pin: Pin) -> State:
    """
    Make the state at start-up, with the pin's places from the timetable.

    Parameters
    ----------
    ports : Ports
        Adapters.
    pin : Pin
        Pin to show.

    Returns
    -------
    State
        State.
    """
    timetable = ports.timetable
    destination = pin.destination
    filled = replace(
        pin,
        origin=timetable.stop(stop_id=pin.origin.stop_id),
        destination=(
            None
            if destination is None
            else timetable.stop(stop_id=destination.stop_id)
        ),
    )
    return initial_state(
        saved=seeded(pin=filled), now=ports.clock.now(), stored=True
    )


async def handle(
    *,
    state: State,
    event: Event,
    ports: Ports,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    maps: MapRenderer,
) -> State:
    """
    Advance the state by one event and carry out its effects.

    Parameters
    ----------
    state : State
        State.
    event : Event
        Event.
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    maps : MapRenderer
        Draws Map views.

    Returns
    -------
    State
        New state.
    """
    if isinstance(event, Tick):
        ports.observer.ticked()
    state, effects = step(state, event)
    for effect in effects:
        await perform(
            ports=ports, effect=effect, queue=queue, tasks=tasks, maps=maps
        )
    return state


async def run(
    *,
    ports: Ports,
    pin: Pin,
    tick_every: float = 5.0,
    pump: Callable[[], None] | None = None,
    post_hook: Callable[[Callable[[Event], None]], None] | None = None,
    max_events: int | None = None,
    tiles_variant: TileVariant = TileVariant.GREY,
) -> State:
    """
    Run the app until quit, or until max_events events have been handled.

    Background tasks are cancelled and awaited before this returns.
    Observer errors are logged and never stop the loop.
    Worker threads started by to_thread are not cancelled; asyncio.run
    waits for them when it exits.

    Parameters
    ----------
    ports : Ports
        Adapters.
    pin : Pin
        Pin to show.
    tick_every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Window event pump.
    post_hook : Callable[[Callable[[Event], None]], None] | None
        Receives a function that posts events (used for keyboard knobs).
    max_events : int | None
        Stop after this many events (tests).
    tiles_variant : TileVariant
        Which tile rendering the Map draws.

    Returns
    -------
    State
        Final state.
    """
    ports = replace(
        ports, observer=ObserverGroup(observers=[ports.observer])
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    if post_hook:
        post_hook(queue.put_nowait)
    state = starting_state(ports=ports, pin=pin)
    maps = MapRenderer(tiles=ports.tiles, variant=tiles_variant)
    tasks: set[asyncio.Task[None]] = set()
    clock = asyncio.create_task(
        ticker(ports=ports, queue=queue, every=tick_every, pump=pump)
    )
    clock.add_done_callback(
        functools.partial(ticker_finished, queue=queue)
    )
    handled = 0
    try:
        while state.running and (max_events is None or handled < max_events):
            state = await handle(
                state=state,
                event=await queue.get(),
                ports=ports,
                queue=queue,
                tasks=tasks,
                maps=maps,
            )
            handled += 1
    finally:
        await stop(tasks={clock, *tasks})
    return state


async def stop(*, tasks: set[asyncio.Task[None]]) -> None:
    """
    Cancel background tasks and wait until they have finished.

    Worker threads started by to_thread are not cancelled; asyncio.run
    joins them when it exits.

    Parameters
    ----------
    tasks : set[asyncio.Task[None]]
        Tasks to cancel.
    """
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
