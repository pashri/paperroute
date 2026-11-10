"""Turn effects into work done in the background."""

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Coroutine
from typing import Any, cast

from paperroute_transit.adapters.feed_errors import FeedError
from paperroute_transit.adapters.feed_guard import FeedBackingOff, JoinedBody
from paperroute_transit.app_connections import departures_read
from paperroute_transit.app_effects import (
    WorkerEffect,
    map_data,
    parse_feed,
    timed_async,
    work_for,
    worked,
)
from paperroute_transit.app_ports import Ports
from paperroute_transit.core.messages import (
    DeparturesFailed,
    Effect,
    Event,
    FeedFailed,
    FeedJoined,
    FetchFeed,
    LoadDepartures,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapDataFailed,
    MapRequest,
    RebuildTimetable,
    TimetableRebuilt,
)

logger = logging.getLogger(__name__)


async def load(
    *, ports: Ports, effect: LoadDepartures, post: Callable[[Event], None],
) -> None:
    """
    Load departures off the event loop and post the result or the failure.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : LoadDepartures
        Request.
    post : Callable[[Event], None]
        Posts an event to the kit's queue.
    """
    try:
        event = await asyncio.to_thread(
            departures_read,
            timetable=ports.timetable,
            effect=effect,
            cache=ports.legs,
        )
    except sqlite3.Error:
        logger.exception('reading departures failed')
        post(DeparturesFailed(pin=effect.pin, timetable=effect.timetable))
        return
    post(event)


def refusal_extra(*, error: Exception) -> dict[str, str]:
    """
    Give the log fields that say why the server refused a feed.

    Parameters
    ----------
    error : Exception
        The failure.

    Returns
    -------
    dict[str, str]
        ``refusal`` when the server gave a reason; empty otherwise.
    """
    detail = error.detail if isinstance(error, FeedError) else ''
    return {'refusal': detail} if detail else {}


async def fetch(
    *, ports: Ports, effect: FetchFeed, post: Callable[[Event], None],
) -> None:
    """
    Fetch and parse a feed, posting success or failure.

    A failed download, a bad body or a failed route labels read all post
    FeedFailed for the feed; one turned away by its backoff is marked as
    not sent. A feed that shared another fetch's request also posts
    FeedJoined after its result.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : FetchFeed
        Request.
    post : Callable[[Event], None]
        Posts an event to the kit's queue.
    """
    try:
        data, fetch_ms = await timed_async(
            call=ports.feeds.fetch(feed=effect.feed)
        )
        (event, count), parse_ms = await timed_async(
            call=asyncio.to_thread(
                parse_feed,
                timetable=ports.timetable,
                plan=ports.timetable.city.feeds,
                effect=effect,
                data=data,
                fetched_at=ports.clock.now(),
            )
        )
    except (FeedError, sqlite3.Error) as error:
        logger.warning(
            'feed failed: %s', error, extra=refusal_extra(error=error),
        )
        failed = FeedFailed(
            feed=effect.feed,
            at=ports.clock.now(),
            reason=str(error),
            sent=not isinstance(error, FeedBackingOff),
        )
        post(failed)
        return
    logger.debug(
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
    ports.observer.work_done(name=effect.feed, ms=parse_ms)
    post(event)
    if isinstance(data, JoinedBody):
        post(FeedJoined(feed=effect.feed))


async def load_map(
    *, ports: Ports, effect: MapRequest, post: Callable[[Event], None],
) -> None:
    """
    Load map data off the event loop and post the result or the failure.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : MapRequest
        Request.
    post : Callable[[Event], None]
        Posts an event to the kit's queue.
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
        post(MapDataFailed(request=effect))
        return
    post(event)


def background(
    *,
    ports: Ports,
    effect: Effect,
    post: Callable[[Event], None],
) -> tuple[Coroutine[Any, Any, None], Event]:
    """
    Make the work for one of transit's effects.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : Effect
        One of transit's own effects.
    post : Callable[[Event], None]
        Posts an event to the kit's queue.

    Returns
    -------
    tuple[Coroutine[Any, Any, None], Event]
        The work, and the event that lets the board recover if it
        crashes.
    """
    match effect:
        case LoadDepartures():
            return (
                load(ports=ports, effect=effect, post=post),
                DeparturesFailed(pin=effect.pin, timetable=effect.timetable),
            )
        case FetchFeed(feed=feed):
            failed = FeedFailed(
                feed=feed, at=ports.clock.now(), reason='unexpected error'
            )
            return fetch(ports=ports, effect=effect, post=post), failed
        case LoadShapes() | LoadPatterns() | LoadModes():
            return (
                load_map(ports=ports, effect=effect, post=post),
                MapDataFailed(request=effect),
            )
        case RebuildTimetable(force=force):
            work = rebuild(ports=ports, post=post, force=force)
            return work, TimetableRebuilt(ok=False)
    call, fallback = work_for(
        timetable=ports.timetable,
        network=ports.network,
        effect=cast(WorkerEffect, effect),
    )
    return worked(call=call, failed=fallback, post=post), fallback


async def rebuild(
    *, ports: Ports, post: Callable[[Event], None], force: bool = False
) -> None:
    """
    Rebuild the timetable and post how it went.

    Parameters
    ----------
    ports : Ports
        Adapters.
    post : Callable[[Event], None]
        Posts an event to the kit's queue.
    force : bool
        Build even when the timetable is up to date.
    """
    ok = await ports.builder.rebuild(force=force)
    post(TimetableRebuilt(ok=ok))


async def perform(
    *, ports: Ports, effect: object, post: Callable[[object], None],
) -> None:
    """
    Carry out one of transit's effects, posting its failure if it crashes.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effect : object
        One of transit's own effects.
    post : Callable[[object], None]
        Posts an event to the kit's queue.
    """
    work, failed = background(
        ports=ports, effect=cast(Effect, effect), post=post,
    )
    try:
        await work
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception('background task crashed')
        post(failed)
