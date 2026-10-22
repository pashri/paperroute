"""Turn effects into work done in the background."""

import asyncio
import functools
import logging
import sqlite3
from collections.abc import Coroutine
from typing import Any

from paperroute.adapters.tfnsw import FeedError
from paperroute.app_device import DeviceEffect, device_work
from paperroute.app_effects import (
    StateSaver,
    WorkerEffect,
    map_data,
    parse_feed,
    timed_async,
    work_for,
    worked,
)
from paperroute.app_ports import Ports
from paperroute.app_render import MapRenderer, render
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
    PowerAction,
    PowerFailed,
    ProbeDevice,
    RebuildTimetable,
    Render,
    ResetDevice,
    SaveState,
    Stop,
    TimetableRebuilt,
)

logger = logging.getLogger(__name__)


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
    if isinstance(effect, ProbeDevice | ResetDevice):
        return device_work(
            ports=ports.device,
            effect=effect,
            queue=queue,
            builder=ports.builder,
        )
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
