"""Command line: run the device software on the development monitor."""

import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from aiohttp import web

from paperroute.adapters.act_led import ActLed
from paperroute.adapters.clocks import ReplayClock, SystemClock
from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.systemd import SystemdNotifier
from paperroute.adapters.tfnsw import AiohttpFeedsClient, api_key_from_env
from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.adapters.web_display import WebDisplay
from paperroute.app import Ports, run
from paperroute.core.messages import Event, QuitRequested
from paperroute.core.model import SYDNEY, Pin
from paperroute.core.tiles import TileVariant
from paperroute.logs import configure_logging
from paperroute.observers import ObserverGroup, Timings
from paperroute.ports import Clock, FeedsClient, TileRepository

if TYPE_CHECKING:
    from paperroute.adapters.tk_monitor import TkMonitor

logger = logging.getLogger(__name__)

STOP_SIGNALS: Final[tuple[signal.Signals, ...]] = (
    signal.SIGTERM,
    signal.SIGINT,
)


def add_pin_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add --from and --to, defaulting to the environment.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument(
        '--from',
        dest='origin',
        default=os.getenv('PAPERROUTE_FROM'),
        help='origin stop id (default: $PAPERROUTE_FROM)',
    )
    parser.add_argument(
        '--to',
        dest='destination',
        default=os.getenv('PAPERROUTE_TO'),
        help='destination stop id (default: $PAPERROUTE_TO)',
    )


def add_display_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add the display choice and the web server options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument('--display', choices=['tk', 'web'], default='tk')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--error-log', type=Path)


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add --feeds and --at.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument(
        '--feeds', default='live', help="'live' or a recordings directory"
    )
    parser.add_argument(
        '--at',
        type=datetime.fromisoformat,
        help='replay from this Sydney time',
    )


def add_tiles_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add --tiles and --tiles-variant.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument(
        '--tiles', type=Path, help='map tiles file; it may not exist yet'
    )
    parser.add_argument(
        '--tiles-variant',
        type=TileVariant,
        choices=list(TileVariant),
        default=TileVariant.GREY,
    )


def require_pin(*, args: argparse.Namespace) -> None:
    """
    Exit with a message if the origin or destination is missing.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    """
    if not args.origin or not args.destination:
        sys.exit(
            'give --from and --to, or set PAPERROUTE_FROM and PAPERROUTE_TO'
        )


class BindError(Exception):
    """The web server could not listen on its address."""


def parse_args() -> argparse.Namespace:
    """
    Read the command line.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m paperroute')
    sub = parser.add_subparsers(dest='command', required=True)
    runner = sub.add_parser('run', help='show a pin on the monitor')
    runner.add_argument('--timetable', type=Path, required=True)
    add_pin_arguments(runner)
    add_display_arguments(runner)
    add_source_arguments(runner)
    add_tiles_arguments(runner)
    args = parser.parse_args()
    require_pin(args=args)
    return args


def sydney(*, moment: datetime) -> datetime:
    """
    Give a datetime Sydney time: naive ones are taken to be Sydney already.

    Parameters
    ----------
    moment : datetime
        Naive or aware time.

    Returns
    -------
    datetime
        Aware Sydney datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=SYDNEY)
    return moment.astimezone(SYDNEY)


def feeds_client(*, choice: str) -> FeedsClient:
    """
    Make the live client (key from PAPERROUTE_API_KEY_FILE) or a replay one.

    Parameters
    ----------
    choice : str
        'live' or a directory path.

    Returns
    -------
    FeedsClient
        Client.
    """
    if choice != 'live':
        return FileFeedsClient(directory=Path(choice))
    return AiohttpFeedsClient(api_key=api_key_from_env())


def clock_for(*, args: argparse.Namespace) -> Clock:
    """
    Make the replay clock if --at was given, else the system clock.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    Clock
        Clock.
    """
    if args.at:
        return ReplayClock(start=sydney(moment=args.at))
    return SystemClock()


def pin_for(*, args: argparse.Namespace) -> Pin:
    """
    Make the pin from --from and --to.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    Pin
        Pin.
    """
    return Pin(origin=args.origin, destination=args.destination)


@contextmanager
def timetable_for(
    *, args: argparse.Namespace
) -> Iterator[SqliteTimetableRepository]:
    """
    Open the timetable and close it on exit.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Yields
    ------
    SqliteTimetableRepository
        The open timetable.
    """
    timetable = SqliteTimetableRepository(path=args.timetable)
    try:
        yield timetable
    finally:
        timetable.close()


@contextmanager
def tiles_for(*, args: argparse.Namespace) -> Iterator[TileRepository]:
    """
    Open the tiles file given, if any, and close it on exit.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Yields
    ------
    TileRepository
        The tiles file, which may be missing, or no tiles without --tiles.
    """
    if args.tiles is None:
        yield NullTiles()
        return
    tiles = SqliteTileRepository(path=args.tiles)
    try:
        yield tiles
    finally:
        tiles.close()


def install_stop_signals(
    *, loop: asyncio.AbstractEventLoop, post: Callable[[Event], None]
) -> None:
    """
    Post QuitRequested on SIGTERM and SIGINT.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        Running loop.
    post : Callable[[Event], None]
        Posts an event to the app.
    """
    for number in STOP_SIGNALS:
        loop.add_signal_handler(number, post, QuitRequested())


def remove_stop_signals(*, loop: asyncio.AbstractEventLoop) -> None:
    """
    Remove the SIGTERM and SIGINT handlers.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        Running loop.
    """
    for number in STOP_SIGNALS:
        loop.remove_signal_handler(number)


def bind_web(
    *,
    loop: asyncio.AbstractEventLoop,
    display: WebDisplay,
    post: Callable[[Event], None],
) -> None:
    """
    Send browser knobs and stop signals to the app.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        Running loop.
    display : WebDisplay
        Display whose knob events are bound.
    post : Callable[[Event], None]
        Posts an event to the app.
    """
    display.bind(post=post)
    install_stop_signals(loop=loop, post=post)


async def listen(*, runner: web.AppRunner, host: str, port: int) -> None:
    """
    Start listening, logging the address if it can't be used.

    Parameters
    ----------
    runner : web.AppRunner
        Set-up runner.
    host : str
        Address to listen on.
    port : int
        Port to listen on.

    Raises
    ------
    BindError
        If the address can't be bound.
    """
    try:
        await web.TCPSite(runner, host, port).start()
    except OSError as error:
        logger.exception('cannot serve on %s:%s', host, port)
        raise BindError(f'cannot serve on {host}:{port}') from error


async def serve_web(
    *,
    ports: Ports,
    display: WebDisplay,
    pin: Pin,
    host: str,
    port: int,
    tick_every: float = 5.0,
    max_events: int | None = None,
    tiles_variant: TileVariant = TileVariant.GREY,
) -> None:
    """
    Run the app and serve its display until it quits.

    Worker threads are joined by asyncio.run when it exits, so the
    caller closes the timetable after that.

    Parameters
    ----------
    ports : Ports
        Adapters; the display must be the given web display.
    display : WebDisplay
        Web display.
    pin : Pin
        Pin to show.
    host : str
        Address to listen on.
    port : int
        Port to listen on.
    tick_every : float
        Seconds between ticks.
    max_events : int | None
        Stop after this many events (tests).
    tiles_variant : TileVariant
        Which tile rendering the Map draws.
    """
    loop = asyncio.get_running_loop()
    runner = web.AppRunner(display.app(), shutdown_timeout=1.0)
    try:
        await runner.setup()
        await listen(runner=runner, host=host, port=port)
        await run(
            ports=ports,
            pin=pin,
            tick_every=tick_every,
            post_hook=lambda post: bind_web(
                loop=loop, display=display, post=post
            ),
            max_events=max_events,
            tiles_variant=tiles_variant,
        )
    finally:
        remove_stop_signals(loop=loop)
        try:
            await runner.cleanup()
        finally:
            await ports.feeds.close()


async def show(
    *,
    ports: Ports,
    pin: Pin,
    monitor: 'TkMonitor',
    tiles_variant: TileVariant = TileVariant.GREY,
) -> None:
    """
    Run the app on the monitor, then close the feeds client.

    Parameters
    ----------
    ports : Ports
        Adapters.
    pin : Pin
        Pin to show.
    monitor : TkMonitor
        Window that pumps events and posts key presses.
    tiles_variant : TileVariant
        Which tile rendering the Map draws.
    """
    try:
        await run(
            ports=ports,
            pin=pin,
            pump=monitor.pump,
            post_hook=lambda post: monitor.bind(post=post),
            tiles_variant=tiles_variant,
        )
    finally:
        await ports.feeds.close()


def tk_main(*, args: argparse.Namespace, feeds: FeedsClient) -> None:
    """
    Run the Stop view for one pin in a Tkinter window.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    feeds : FeedsClient
        Feeds client.
    """
    from paperroute.adapters.tk_monitor import (  # pylint: disable=C0415
        TkMonitor,
    )

    monitor = TkMonitor()
    try:
        with (
            timetable_for(args=args) as timetable,
            tiles_for(args=args) as tiles,
        ):
            ports = Ports(
                timetable=timetable,
                feeds=feeds,
                display=monitor,
                clock=clock_for(args=args),
                tiles=tiles,
            )
            asyncio.run(
                show(
                    ports=ports,
                    pin=pin_for(args=args),
                    monitor=monitor,
                    tiles_variant=args.tiles_variant,
                )
            )
    finally:
        monitor.close()


def serve_or_exit(
    *, args: argparse.Namespace, ports: Ports, display: WebDisplay
) -> None:
    """
    Serve the display; exit 1 if the port can't be used.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    ports : Ports
        Adapters.
    display : WebDisplay
        Web display.
    """
    try:
        asyncio.run(
            serve_web(
                ports=ports,
                display=display,
                pin=pin_for(args=args),
                host=args.host,
                port=args.port,
                tiles_variant=args.tiles_variant,
            )
        )
    except BindError:
        sys.exit(1)


def web_main(*, args: argparse.Namespace, feeds: FeedsClient) -> None:
    """
    Serve the Stop view for one pin to browsers.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    feeds : FeedsClient
        Feeds client.
    """
    timings = Timings()
    display = WebDisplay(status=timings.snapshot)
    observer = ObserverGroup(
        observers=[
            timings,
            SystemdNotifier.from_environment(),
            ActLed(),
        ]
    )
    with (
        timetable_for(args=args) as timetable,
        tiles_for(args=args) as tiles,
    ):
        ports = Ports(
            timetable=timetable,
            feeds=feeds,
            display=display,
            clock=clock_for(args=args),
            observer=observer,
            tiles=tiles,
        )
        serve_or_exit(args=args, ports=ports, display=display)


def main() -> None:
    """Run the Stop view for one pin in a window or in browsers."""
    args = parse_args()
    configure_logging(error_file=args.error_log)
    try:
        feeds = feeds_client(choice=args.feeds)
        if args.display == 'web':
            web_main(args=args, feeds=feeds)
        else:
            tk_main(args=args, feeds=feeds)
    except Exception:
        logger.exception('paperroute crashed')
        raise


if __name__ == '__main__':
    main()
