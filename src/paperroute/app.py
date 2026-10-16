"""Wire the ports to the reducer through one event queue."""

import asyncio
import functools
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from paperroute.adapters.gtfs_rt import parse_trip_updates
from paperroute.adapters.tfnsw import FeedError
from paperroute.core.model import Pin
from paperroute.core.state import (
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    LoadDepartures,
    Render,
    State,
    Tick,
    initial_state,
    step,
)
from paperroute.ports import Clock, Display, FeedsClient, TimetableRepository
from paperroute.render.stop_view import draw_stop_view

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ports:
    """The adapters the app runs on."""

    timetable: TimetableRepository
    feeds: FeedsClient
    display: Display
    clock: Clock


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
        await queue.put(DeparturesFailed())
        return
    await queue.put(
        DeparturesLoaded(departures=tuple(found), until=effect.end)
    )


async def fetch(
    *, ports: Ports, effect: FetchFeed, queue: asyncio.Queue[Event]
) -> None:
    """
    Fetch and parse a feed, posting success or failure.

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
        data = await ports.feeds.fetch(feed=effect.feed)
        snapshot = await asyncio.to_thread(
            parse_trip_updates, data=data, fetched_at=ports.clock.now()
        )
    except FeedError as error:
        logger.warning('feed failed: %s', error)
        failed = FeedFailed(
            feed=effect.feed, at=ports.clock.now(), reason=str(error)
        )
        await queue.put(failed)
        return
    await queue.put(FeedFetched(feed=effect.feed, snapshot=snapshot))


async def perform(
    *,
    ports: Ports,
    effect: Effect,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
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
    """
    match effect:
        case LoadDepartures():
            work = load(ports=ports, effect=effect, queue=queue)
            failed: Event = DeparturesFailed()
        case FetchFeed(feed=feed):
            work = fetch(ports=ports, effect=effect, queue=queue)
            failed = FeedFailed(
                feed=feed, at=ports.clock.now(), reason='unexpected error'
            )
        case Render(view=view):
            frame = await asyncio.to_thread(draw_stop_view, view=view)
            ports.display.show(frame=frame)
            return
        case _:
            return
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


async def run(
    *,
    ports: Ports,
    pin: Pin,
    tick_every: float = 5.0,
    pump: Callable[[], None] | None = None,
    post_hook: Callable[[Callable[[Event], None]], None] | None = None,
    max_events: int | None = None,
) -> State:
    """
    Run the app until quit, or until max_events events have been handled.

    Background tasks are cancelled and awaited before this returns.
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

    Returns
    -------
    State
        Final state.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue()
    if post_hook:
        post_hook(queue.put_nowait)
    state = initial_state(
        pin=pin,
        now=ports.clock.now(),
        origin=ports.timetable.stop_name(stop_id=pin.origin),
        destination=ports.timetable.stop_name(stop_id=pin.destination),
    )
    tasks: set[asyncio.Task[None]] = set()
    clock = asyncio.create_task(
        ticker(ports=ports, queue=queue, every=tick_every, pump=pump)
    )
    handled = 0
    try:
        while state.running and (max_events is None or handled < max_events):
            state, effects = step(state, await queue.get())
            handled += 1
            for effect in effects:
                await perform(
                    ports=ports, effect=effect, queue=queue, tasks=tasks
                )
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
