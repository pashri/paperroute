"""Wire the ports to the reducer through one event queue."""

import asyncio
import functools
import logging
import sqlite3
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.power import NullPower
from paperroute.adapters.rebuild import NullBuilder
from paperroute.adapters.tfnsw import FeedError
from paperroute.adapters.tiles_db import NullTiles
from paperroute.app_device import (
    DeviceEffect,
    DevicePorts,
    Reported,
    device_work,
    key_known,
    report_status,
)
from paperroute.app_effects import (
    StateSaver,
    VersionWatch,
    WorkerEffect,
    filled,
    map_data,
    parse_feed,
    timed_async,
    work_for,
    worked,
)
from paperroute.core.map_view import MapBase, MapView
from paperroute.core.messages import (
    ControlPower,
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FetchFeed,
    LoadDepartures,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    MapRequest,
    NoticeView,
    PowerAction,
    PowerFailed,
    ProbeDevice,
    QuitRequested,
    RebuildTimetable,
    Render,
    SaveState,
    ScreenView,
    Stop,
    Tick,
    TimetableRebuilt,
)
from paperroute.core.model import Pin
from paperroute.core.saved import seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import StripMapView, StripView
from paperroute.core.tiles import TileVariant
from paperroute.measures import StopMeasures
from paperroute.observers import NullObserver, ObserverGroup
from paperroute.ports import (
    Clock,
    Display,
    FeedsClient,
    NetworkRepository,
    Observer,
    PowerControl,
    StateRepository,
    TileRepository,
    TimetableBuilder,
    TimetableRepository,
)
from paperroute.render.chooser import draw_chooser_view
from paperroute.render.map_base import draw_map_base
from paperroute.render.map_view import draw_map_view
from paperroute.render.notice import draw_notice_view
from paperroute.render.stop_view import draw_stop_view
from paperroute.render.strip_view import draw_strip_map_view, draw_strip_view

logger = logging.getLogger(__name__)

TICK_PAUSE: Final[float] = 0.03  # seconds between window pumps

type View = ScreenView
type Frame = NDArray[np.uint8]


@dataclass(frozen=True)
class Ports:
    """The adapters the app runs on."""

    timetable: TimetableRepository
    feeds: FeedsClient
    display: Display
    clock: Clock
    network: NetworkRepository
    states: StateRepository
    observer: Observer = field(default_factory=NullObserver)
    tiles: TileRepository = field(default_factory=NullTiles)
    measures: StopMeasures = field(default_factory=StopMeasures)
    builder: TimetableBuilder = field(default_factory=NullBuilder)
    power: PowerControl = field(default_factory=NullPower)
    device: DevicePorts = field(default_factory=DevicePorts)


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


def drawing(*, view: View, maps: MapRenderer) -> Callable[[], Frame]:
    """
    Pick the drawing for a view.

    Parameters
    ----------
    view : View
        View to draw.
    maps : MapRenderer
        Draws the views with a map.

    Returns
    -------
    Callable[[], Frame]
        Draws the frame when called.
    """
    match view:
        case StopView():
            return functools.partial(draw_stop_view, view=view)
        case MapView():
            return functools.partial(maps.frame, view=view)
        case StripView():
            return functools.partial(draw_strip_view, view=view)
        case StripMapView():
            return functools.partial(maps.strip_map_frame, view=view)
        case NoticeView():
            return functools.partial(draw_notice_view, view=view)
    return functools.partial(draw_chooser_view, view=view)


async def render(*, ports: Ports, view: View, maps: MapRenderer) -> None:
    """
    Draw a view off the event loop, show it and report how long it took.

    Parameters
    ----------
    ports : Ports
        Adapters.
    view : View
        View to draw.
    maps : MapRenderer
        Draws the views with a map.
    """
    draw = drawing(view=view, maps=maps)
    frame, render_ms = await timed_async(call=asyncio.to_thread(draw))
    ports.display.show(frame=frame)
    logger.info('frame drawn', extra={'render_ms': render_ms})
    ports.observer.frame_drawn(render_ms=render_ms)


def background(
    *,
    ports: Ports,
    effect: (
        LoadDepartures
        | FetchFeed
        | MapRequest
        | RebuildTimetable
        | ControlPower
        | DeviceEffect
        | WorkerEffect
    ),
    queue: asyncio.Queue[Event],
) -> tuple[Coroutine[Any, Any, None], Event]:
    """
    Make the work for an effect done in the background.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : LoadDepartures | FetchFeed | MapRequest | RebuildTimetable
        | ControlPower | DeviceEffect | WorkerEffect
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
        case LoadShapes() | LoadPatterns() | LoadModes():
            return (
                load_map(ports=ports, effect=effect, queue=queue),
                MapDataFailed(request=effect),
            )
        case RebuildTimetable():
            failed_rebuild = TimetableRebuilt(ok=False)
            return rebuild(ports=ports, queue=queue), failed_rebuild
        case ControlPower(action=action):
            return (
                power_control(ports=ports, action=action, queue=queue),
                PowerFailed(action=action),
            )
    return other_work(ports=ports, effect=effect, queue=queue)


def other_work(
    *,
    ports: Ports,
    effect: DeviceEffect | WorkerEffect,
    queue: asyncio.Queue[Event],
) -> tuple[Coroutine[Any, Any, None], Event]:
    """
    Make the work for a device or worker-thread effect.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : DeviceEffect | WorkerEffect
        Effect.
    queue : asyncio.Queue[Event]
        Event queue.

    Returns
    -------
    tuple[Coroutine[Any, Any, None], Event]
        The work, and the event that lets the app recover if it crashes.
    """
    if isinstance(effect, ProbeDevice):
        return device_work(ports=ports.device, effect=effect, queue=queue)
    call, fallback = work_for(
        timetable=ports.timetable, network=ports.network, effect=effect
    )
    return worked(call=call, failed=fallback, queue=queue), fallback


async def rebuild(*, ports: Ports, queue: asyncio.Queue[Event]) -> None:
    """
    Rebuild the timetable and post how it went.

    Parameters
    ----------
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    ok = await ports.builder.rebuild()
    await queue.put(TimetableRebuilt(ok=ok))


async def power_control(
    *, ports: Ports, action: PowerAction, queue: asyncio.Queue[Event]
) -> None:
    """
    Shut down or restart, posting PowerFailed when it is refused.

    Parameters
    ----------
    ports : Ports
        Adapters.
    action : PowerAction
        Shut down or restart.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    if not await ports.power.control(action=action):
        await queue.put(PowerFailed(action=action))


async def perform(
    *,
    ports: Ports,
    effect: Effect,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    maps: MapRenderer,
    saver: StateSaver,
) -> None:
    """
    Carry out one effect.

    Drawing and saving finish before this returns, so a quit's save is
    written before the loop ends; other work runs in the background.

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
        Draws the views with a map.
    saver : StateSaver
        Writes the saved state.
    """
    match effect:
        case Render(view=view):
            await render(ports=ports, view=view, maps=maps)
            return
        case SaveState():
            await saver.save(effect=effect)
            return
        case Stop():
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
    Post ticks, note timetable changes and keep the window responsive.

    The timetable version is posted at start and before any tick it
    changed by; a version read error posts nothing.

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
    watch = VersionWatch(timetable=ports.timetable)
    elapsed = every
    while True:
        if elapsed >= every:
            seen = await watch.check()
            if seen is not None:
                await queue.put(seen)
            await queue.put(Tick(now=ports.clock.now()))
            elapsed = 0.0
        if pump:
            pump()
        await asyncio.sleep(TICK_PAUSE)
        elapsed += TICK_PAUSE


def starting_state(*, ports: Ports, seed: Pin | None = None) -> State:
    """
    Make the state at start-up from the saved state, or else the seed.

    Without saved state the seed becomes the one pin, its places filled
    in from the timetable; without either, the defaults show. State not
    from the store is written on the first tick.

    Parameters
    ----------
    ports : Ports
        Adapters.
    seed : Pin | None
        Pin from the command line or environment.

    Returns
    -------
    State
        State.
    """
    loaded = ports.states.load()
    saved = loaded.saved
    if seed is not None and loaded.stored:
        logger.info('seed ignored: the saved state is used')
    elif seed is not None:
        saved = seeded(pin=filled(timetable=ports.timetable, pin=seed))
        logger.info('state seeded from the command line or environment')
    return initial_state(
        saved=saved, now=ports.clock.now(), stored=loaded.stored
    )


@dataclass
class PendingView:
    """
    The last view asked for and not drawn yet.

    Attributes
    ----------
    view : View | None
        The view, or None when the screen is up to date.
    """

    view: View | None = None


@dataclass(frozen=True)
class Outputs:
    """
    What effects draw and write through.

    Attributes
    ----------
    maps : MapRenderer
        Draws the views with a map.
    saver : StateSaver
        Writes the saved state.
    pending : PendingView
        The view waiting to be drawn.
    reported : Reported
        The device status reported last.
    """

    maps: MapRenderer
    saver: StateSaver
    pending: PendingView = field(default_factory=PendingView)
    reported: Reported = field(default_factory=Reported)


async def draw_pending(*, ports: Ports, outputs: Outputs) -> None:
    """
    Draw the waiting view, if there is one.

    Parameters
    ----------
    ports : Ports
        Adapters.
    outputs : Outputs
        What effects draw and write through.
    """
    view = outputs.pending.view
    if view is None:
        return
    outputs.pending.view = None
    await render(ports=ports, view=view, maps=outputs.maps)


async def carry_out(
    *,
    effects: Sequence[Effect],
    ports: Ports,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
) -> None:
    """
    Perform an event's effects in order, keeping its view to draw later.

    A ControlPower draws the waiting view first, so the notice is on
    screen before the device goes down.

    Parameters
    ----------
    effects : Sequence[Effect]
        The effects.
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.
    """
    for effect in effects:
        if isinstance(effect, Render):
            outputs.pending.view = effect.view
            continue
        if isinstance(effect, ControlPower):
            await draw_pending(ports=ports, outputs=outputs)
        await perform(
            ports=ports,
            effect=effect,
            queue=queue,
            tasks=tasks,
            maps=outputs.maps,
            saver=outputs.saver,
        )


async def handle(
    *,
    state: State,
    event: Event,
    ports: Ports,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
) -> State:
    """
    Advance the state by one event and carry out its effects.

    The view is drawn only once no other event is waiting, so a burst
    of knob turns draws one frame of the last state, not one per turn.

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
    outputs : Outputs
        What effects draw and write through.

    Returns
    -------
    State
        New state.
    """
    if isinstance(event, Tick):
        ports.observer.ticked()
    before = state.settings
    state, effects = step(state, event)
    if state.settings != before:
        ports.observer.settings_seen(settings=state.settings)
    report_status(
        observer=ports.observer, state=state, reported=outputs.reported,
    )
    await carry_out(
        effects=effects,
        ports=ports,
        queue=queue,
        tasks=tasks,
        outputs=outputs,
    )
    if queue.empty() or not state.running:
        await draw_pending(ports=ports, outputs=outputs)
    return state


async def run(
    *,
    ports: Ports,
    seed: Pin | None = None,
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
    seed : Pin | None
        Pin to start with when there is no saved state.
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
    state = starting_state(ports=ports, seed=seed)
    ports.observer.settings_seen(settings=state.settings)
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles, variant=tiles_variant),
        saver=StateSaver(states=ports.states),
    )
    report_status(
        observer=ports.observer, state=state, reported=outputs.reported,
    )
    known = key_known(ports=ports.device)
    if known is not None:
        queue.put_nowait(known)
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
                outputs=outputs,
            )
            handled += 1
        await draw_pending(ports=ports, outputs=outputs)
    finally:
        await stop(tasks={clock, *tasks})
        await ports.builder.stop()
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
