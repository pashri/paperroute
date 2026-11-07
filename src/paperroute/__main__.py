"""Command line: run the device software on the development monitor."""

# pylint: disable=too-many-lines

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

from paperroute.adapters.clocks import ReplayClock, SystemClock
from paperroute.adapters.device_probe import SystemProbe
from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.feed_guard import GuardedFeeds
from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.pin_options import SqlitePinOptions
from paperroute.adapters.rebuild import (
    ChildRebuilder,
    NullBuilder,
    refresh_command,
    refresh_env,
)
from paperroute.adapters.state_file import DEFAULT_PATH as STATE_PATH
from paperroute.adapters.state_file import JsonStateRepository
from paperroute.adapters.tfnsw import (
    TFNSW_KEY,
    AiohttpFeedsClient,
    key_path_from_env,
    tfnsw_limiter,
)
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.adapters.trip_planner import trip_planner_for
from paperroute.adapters.web_board import SYDNEY_HINT, SYDNEY_NAV, SydneyPages
from paperroute.adapters.web_display import WebDisplay
from paperroute.app import Ports, run
from paperroute.app_device import DevicePorts
from paperroute.asset_root import use_app_assets
from paperroute.core.feeds import DAILY_LIMIT, feed_label
from paperroute.core.messages import Event, QuitRequested
from paperroute.logs import LastError, configure_logging
from paperroute.observers import Timings
from paperroute.ports import FeedsClient, TileRepository, TimetableBuilder
from paperroute.summary import Summary
from paperroute.web_observer import web_observer
from paperroute_device.adapters.power import NullPower, SystemctlPower
from paperroute_device.adapters.resets import FileResets, NullResets
from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_device.adapters.web_device import DevicePages
from paperroute_device.adapters.wifi import NmcliWifi, NullWifi
from paperroute_device.ports import (
    Clock,
    KeyStore,
    PowerControl,
    Resets,
    WifiControl,
)
from paperroute_hardware.adapters.knob_lines import GpiodKnobLines
from paperroute_hardware.adapters.knob_reader import KnobReader
from paperroute_hardware.adapters.panel_bus import LinuxPanelBus
from paperroute_hardware.adapters.panel_display import Displays
from paperroute_hardware.knobs_open import knobs_for, knobs_state_for
from paperroute_hardware.panel_open import panel_for, panel_times_for
from paperroute_hardware.selftest import selftest_main
from paperroute_transit.adapters.checked_tiles import CheckedTiles, warn_unfit
from paperroute_transit.adapters.tiles_db import (
    NullTiles,
    SqliteTileRepository,
)
from paperroute_transit.core.model import SYDNEY, Pin, StopPlace
from paperroute_transit.core.tiles import TileVariant

if TYPE_CHECKING:
    from paperroute.adapters.tk_monitor import TkMonitor

logger = logging.getLogger(__name__)

STOP_SIGNALS: Final[tuple[signal.Signals, ...]] = (
    signal.SIGTERM,
    signal.SIGINT,
)
DATA_DIR: Final[Path] = Path('/var/lib/paperroute')


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
    parser.add_argument('--panel', action='store_true')
    parser.add_argument('--knobs', action='store_true')
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


def add_state_argument(parser: argparse.ArgumentParser) -> None:
    """
    Add --state, the file the pins and view are kept in.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument(
        '--state',
        type=Path,
        default=STATE_PATH,
        help=f'saved state file (default: {STATE_PATH})',
    )


def add_device_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add the switches for what the device itself may do.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument(
        '--rebuild',
        action='store_true',
        help='rebuild an outdated timetable in the background',
    )
    parser.add_argument(
        '--power',
        action='store_true',
        help='let the Settings page shut down and restart the device',
    )
    parser.add_argument(
        '--wifi',
        action='store_true',
        help='scan, join and share Wi-Fi through NetworkManager',
    )
    parser.add_argument(
        '--version-file',
        type=Path,
        help='file holding the release installed (default: dev)',
    )


def require_both_or_neither(*, args: argparse.Namespace) -> None:
    """
    Exit with a message if only one of the origin and destination is given.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    """
    if bool(args.origin) != bool(args.destination):
        sys.exit('give both --from and --to, or neither')


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
    add_run_arguments(sub.add_parser('run', help='run the device software'))
    sub.add_parser('selftest', help='test the e-paper panel')
    args = parser.parse_args()
    if args.command == 'run':
        require_both_or_neither(args=args)
    return args


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add every argument of the run command.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The run command's parser.
    """
    parser.add_argument('--timetable', type=Path, required=True)
    add_pin_arguments(parser)
    add_display_arguments(parser)
    add_source_arguments(parser)
    add_tiles_arguments(parser)
    add_state_argument(parser)
    add_device_arguments(parser)


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


def builder_for(*, args: argparse.Namespace) -> TimetableBuilder:
    """
    Choose how an outdated timetable is rebuilt.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    TimetableBuilder
        A refresh child with --rebuild; otherwise a builder that never
        rebuilds.
    """
    if not args.rebuild:
        return NullBuilder()
    return ChildRebuilder(
        command=refresh_command(
            timetable=args.timetable, error_log=args.error_log,
        ),
        env=refresh_env(timetable=args.timetable),
    )


def power_for(*, args: argparse.Namespace) -> PowerControl:
    """
    Choose how the device is shut down and restarted.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    PowerControl
        The systemctl control with --power; otherwise one that never acts.
    """
    if args.power:
        return SystemctlPower()
    return NullPower()


def wifi_for(*, args: argparse.Namespace) -> WifiControl:
    """
    Choose how Wi-Fi is controlled.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    WifiControl
        The nmcli control with --wifi; otherwise one that never acts.
    """
    if args.wifi:
        return NmcliWifi()
    return NullWifi()


def resets_for(
    *,
    args: argparse.Namespace,
    key: KeyStore,
    wifi: WifiControl,
    data_dir: Path = DATA_DIR,
) -> Resets:
    """
    Choose how the settings are reset and everything erased.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    key : KeyStore
        Holds the API key.
    wifi : WifiControl
        Forgets the saved Wi-Fi networks.
    data_dir : Path
        The only directory a reset deletes files from.

    Returns
    -------
    Resets
        Resets over the data directory with --power; otherwise
        resets that never act, so a development run keeps its data.
    """
    if not args.power:
        return NullResets()
    return FileResets(
        data_dir=data_dir,
        state=args.state,
        key=key,
        wifi=wifi,
        timetable=args.timetable,
    )


def device_for(
    *,
    args: argparse.Namespace,
    tiles: TileRepository,
    last_error: LastError,
) -> DevicePorts:
    """
    Make the device's own adapters.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    tiles : TileRepository
        The map tiles, whose build time is reported.
    last_error : LastError
        Holds the latest error logged.

    Returns
    -------
    DevicePorts
        Wi-Fi, the system probe, the key store and the resets; replayed
        feeds need no key, so they get Wi-Fi alone, no facts and no
        resets.
    """
    wifi = wifi_for(args=args)
    if args.feeds != 'live':
        return DevicePorts(wifi=wifi)
    key = SecretsFile(path=key_path_from_env())
    probe = SystemProbe(
        wifi=wifi,
        key=key,
        tiles=tiles,
        last_error=last_error,
        data_dir=args.state.parent,
        version_file=args.version_file,
    )
    resets = resets_for(args=args, key=key, wifi=wifi)
    return DevicePorts(wifi=wifi, probe=probe, key=key, resets=resets)


def feeds_client(
    *, choice: str, summary: Summary | None = None,
) -> FeedsClient:
    """
    Make the live client or a replay one.

    The live client reads the key from PAPERROUTE_API_KEY_FILE at each
    fetch, so a changed key is used without a restart. It waits on the
    limiter the Trip Planner shares, and backs off a feed after a 429.

    Parameters
    ----------
    choice : str
        'live' or a directory path.
    summary : Summary | None
        Counts the live client's requests; a new one by default.

    Returns
    -------
    FeedsClient
        Client.
    """
    if choice != 'live':
        return FileFeedsClient(directory=Path(choice))
    return GuardedFeeds(
        inner=AiohttpFeedsClient(
            key=SecretsFile(path=key_path_from_env()).read,
            limiter=tfnsw_limiter(),
        ),
        summary=summary,
    )


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


def seed_for(*, args: argparse.Namespace) -> Pin | None:
    """
    Make the seed pin from --from and --to.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    Pin | None
        Pin between bare places, without names or points; None when
        neither was given.
    """
    if not args.origin or not args.destination:
        return None
    return Pin(
        origin=StopPlace(stop_id=args.origin, name='', point=None),
        destination=StopPlace(stop_id=args.destination, name='', point=None),
    )


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
def network_for(
    *, args: argparse.Namespace
) -> Iterator[SqliteNetworkRepository]:
    """
    Open the chooser and runs reader on the timetable, and close it on exit.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Yields
    ------
    SqliteNetworkRepository
        The open reader.
    """
    network = SqliteNetworkRepository(path=args.timetable)
    try:
        yield network
    finally:
        network.close()


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
        The tiles file, which may be missing. No tiles without --tiles,
        or when the file was built for other zoom levels or another map
        style; the maps then say "Map not installed". A file installed
        while the app runs is checked when it is first seen.
    """
    if args.tiles is None:
        yield NullTiles()
        return
    tiles = SqliteTileRepository(path=args.tiles)
    if args.tiles.exists() and not tiles.fits():
        built = tiles.built_for()
        tiles.close()
        warn_unfit(path=args.tiles, built=built)
        yield NullTiles()
        return
    checked = CheckedTiles(tiles=tiles, path=args.tiles)
    try:
        yield checked
    finally:
        checked.close()


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


def threadsafe(
    *, loop: asyncio.AbstractEventLoop, post: Callable[[Event], None]
) -> Callable[[Event], None]:
    """
    Make a post that other threads can call.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The running loop.
    post : Callable[[Event], None]
        Posts an event; only safe on the loop's thread.

    Returns
    -------
    Callable[[Event], None]
        Hands each event to the loop, which posts it on its own thread.
    """

    def send(event: Event) -> None:
        """
        Hand an event to the loop.

        Parameters
        ----------
        event : Event
            The event.
        """
        loop.call_soon_threadsafe(post, event)

    return send


def bind_web(
    *,
    loop: asyncio.AbstractEventLoop,
    display: WebDisplay,
    post: Callable[[Event], None],
    knobs: KnobReader | None = None,
) -> None:
    """
    Send browser knobs, the physical knobs and stop signals to the app.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        Running loop.
    display : WebDisplay
        Display whose knob events are bound.
    post : Callable[[Event], None]
        Posts an event to the app; only safe on the loop's thread.
    knobs : KnobReader | None
        The physical knobs, started here; their thread posts through
        the loop.
    """
    display.bind(post=post)
    install_stop_signals(loop=loop, post=post)
    if knobs is not None:
        knobs.start(post=threadsafe(loop=loop, post=post))


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


async def serve_web(  # pylint: disable=too-many-arguments
    *,
    ports: Ports,
    display: WebDisplay,
    seed: Pin | None,
    host: str,
    port: int,
    tick_every: float = 5.0,
    max_events: int | None = None,
    tiles_variant: TileVariant = TileVariant.GREY,
    knobs: KnobReader | None = None,
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
    seed : Pin | None
        Pin to start with when there is no saved state.
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
    knobs : KnobReader | None
        The physical knobs; stopped before the loop ends, so their
        thread never posts to a closed loop.
    """
    loop = asyncio.get_running_loop()
    runner = web.AppRunner(display.app(), shutdown_timeout=1.0)
    try:
        await runner.setup()
        await listen(runner=runner, host=host, port=port)
        await run(
            ports=ports,
            seed=seed,
            tick_every=tick_every,
            post_hook=lambda post: bind_web(
                loop=loop, display=display, post=post, knobs=knobs,
            ),
            max_events=max_events,
            tiles_variant=tiles_variant,
        )
    finally:
        if knobs is not None:
            knobs.stop()
        await served(runner=runner, ports=ports, loop=loop)


async def served(
    *,
    runner: web.AppRunner,
    ports: Ports,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Stop serving: drop the stop signals, the web server and the feeds.

    Parameters
    ----------
    runner : web.AppRunner
        The web server.
    ports : Ports
        Adapters; their feeds client is closed.
    loop : asyncio.AbstractEventLoop
        The running loop.
    """
    remove_stop_signals(loop=loop)
    try:
        await runner.cleanup()
    finally:
        await ports.feeds.close()


async def show(
    *,
    ports: Ports,
    seed: Pin | None,
    monitor: 'TkMonitor',
    tiles_variant: TileVariant = TileVariant.GREY,
) -> None:
    """
    Run the app on the monitor, then close the feeds client.

    Parameters
    ----------
    ports : Ports
        Adapters.
    seed : Pin | None
        Pin to start with when there is no saved state.
    monitor : TkMonitor
        Window that pumps events and posts key presses.
    tiles_variant : TileVariant
        Which tile rendering the Map draws.
    """
    try:
        await run(
            ports=ports,
            seed=seed,
            pump=monitor.pump,
            post_hook=lambda post: monitor.bind(post=post),
            tiles_variant=tiles_variant,
        )
    finally:
        await ports.feeds.close()


def tk_main(
    *,
    args: argparse.Namespace,
    feeds: FeedsClient,
    last_error: LastError | None = None,
) -> None:
    """
    Run the device software in a Tkinter window.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    feeds : FeedsClient
        Feeds client.
    last_error : LastError | None
        Holds the latest error logged; a new one by default.
    """
    from paperroute.adapters.tk_monitor import (  # pylint: disable=C0415
        TkMonitor,
    )

    monitor = TkMonitor()
    try:
        with (
            timetable_for(args=args) as timetable,
            network_for(args=args) as network,
            tiles_for(args=args) as tiles,
        ):
            ports = Ports(
                timetable=timetable,
                feeds=feeds,
                display=monitor,
                clock=clock_for(args=args),
                network=network,
                states=JsonStateRepository(path=args.state),
                tiles=tiles,
                builder=builder_for(args=args),
                power=power_for(args=args),
                device=device_for(
                    args=args,
                    tiles=tiles,
                    last_error=last_error or LastError(),
                ),
            )
            asyncio.run(
                show(
                    ports=ports,
                    seed=seed_for(args=args),
                    monitor=monitor,
                    tiles_variant=args.tiles_variant,
                )
            )
    finally:
        monitor.close()


def serve_or_exit(
    *,
    args: argparse.Namespace,
    ports: Ports,
    display: WebDisplay,
    knobs: KnobReader | None = None,
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
    knobs : KnobReader | None
        The physical knobs, if any.
    """
    try:
        asyncio.run(
            serve_web(
                ports=ports,
                display=display,
                seed=seed_for(args=args),
                host=args.host,
                port=args.port,
                tiles_variant=args.tiles_variant,
                knobs=knobs,
            )
        )
    except BindError:
        sys.exit(1)


def device_pages_for(
    *,
    args: argparse.Namespace,
    timings: Timings,
    display: WebDisplay,
    knob_state: Callable[[], bool | None] = lambda: None,
) -> DevicePages | None:
    """
    Build the phone setup pages for a device on live feeds.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    timings : Timings
        Holds the latest device status and feed times.
    display : WebDisplay
        Sends the pages' events on to the app.
    knob_state : Callable[[], bool | None]
        Whether the knobs are answering, or None when not asked for.

    Returns
    -------
    DevicePages | None
        The pages; None for replayed feeds, which have no key file.
    """
    if args.feeds != 'live':
        return None
    return DevicePages(
        status=lambda: timings.device,
        panel=lambda: timings.panel,
        feeds=lambda: timings.last_ok,
        key=SecretsFile(path=key_path_from_env()),
        credential=TFNSW_KEY,
        feed_label=feed_label,
        daily_limit=DAILY_LIMIT,
        post=display.send,
        zone=SYDNEY,
        error_log=args.error_log,
        state_file=args.state,
        knobs=knob_state,
        nav=SYDNEY_NAV,
    )


def sydney_display(
    *, timings: Timings, network: SqliteNetworkRepository, live: bool,
) -> WebDisplay:
    """
    Build the web display with Sydney's nav, hint and board pages.

    Parameters
    ----------
    timings : Timings
        Feeds the status page and the pins.
    network : SqliteNetworkRepository
        Where the pin options look places up.
    live : bool
        Whether the feeds are live, which the trip planner needs.

    Returns
    -------
    WebDisplay
        The display, with no device pages yet.
    """
    return WebDisplay(
        status=timings.snapshot,
        nav=SYDNEY_NAV,
        hint=SYDNEY_HINT,
        board=SydneyPages(
            pins=lambda: timings.pins,
            options=SqlitePinOptions(network=network),
            planner=trip_planner_for(live=live),
        ),
    )


def web_main(
    *,
    args: argparse.Namespace,
    feeds: FeedsClient,
    last_error: LastError | None = None,
    summary: Summary | None = None,
) -> None:
    """
    Serve the device software to browsers.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    feeds : FeedsClient
        Feeds client.
    last_error : LastError | None
        Holds the latest error logged; a new one by default.
    summary : Summary | None
        Counts frames and the feeds' requests; a new one by default.
    """
    timings = Timings()
    observer = web_observer(timings=timings, summary=summary or Summary())
    panel = panel_for(
        enabled=args.panel,
        opener=LinuxPanelBus.open,
        report=lambda refresh: observer.panel_refreshed(refresh=refresh),
    )
    shown = [panel] if panel else []
    timings.panel = panel_times_for(wanted=args.panel, panel=panel)
    knobs = knobs_for(
        enabled=args.knobs,
        opener=GpiodKnobLines.open,
        swapped=timings.knobs_swapped,
    )
    with (
        timetable_for(args=args) as timetable,
        network_for(args=args) as network,
        tiles_for(args=args) as tiles,
    ):
        display = sydney_display(
            timings=timings, network=network, live=args.feeds == 'live',
        )
        display.device = device_pages_for(
            args=args,
            timings=timings,
            display=display,
            knob_state=lambda: knobs_state_for(
                wanted=args.knobs, knobs=knobs,
            ),
        )
        ports = Ports(
            timetable=timetable,
            feeds=feeds,
            display=Displays(displays=[display, *shown]),
            clock=clock_for(args=args),
            network=network,
            states=JsonStateRepository(path=args.state),
            observer=observer,
            tiles=tiles,
            builder=builder_for(args=args),
            power=power_for(args=args),
            device=device_for(
                args=args,
                tiles=tiles,
                last_error=last_error or LastError(),
            ),
        )
        try:
            serve_or_exit(
                args=args, ports=ports, display=display, knobs=knobs,
            )
        finally:
            if panel is not None:
                panel.stop()
            if knobs is not None:
                knobs.stop()


def main() -> None:  # pylint: disable=too-many-statements
    """Run the device software in a window or in browsers."""
    use_app_assets()
    args = parse_args()
    if args.command == 'selftest':
        sys.exit(selftest_main())
    last_error = LastError()
    configure_logging(error_file=args.error_log, last_error=last_error)
    summary = Summary()
    try:
        feeds = feeds_client(choice=args.feeds, summary=summary)
        if args.display == 'web':
            web_main(
                args=args, feeds=feeds, last_error=last_error, summary=summary,
            )
        else:
            for flag in ('panel', 'knobs'):
                if getattr(args, flag):
                    logger.warning(
                        '--%s only works with --display web; ignoring it',
                        flag,
                    )
            tk_main(args=args, feeds=feeds, last_error=last_error)
    except Exception:
        logger.exception('paperroute crashed')
        raise


if __name__ == '__main__':
    main()
