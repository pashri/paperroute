"""Command line: run an app on the panel, in browsers or a window."""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from paperroute_device.adapters.clocks import SystemClock
from paperroute_device.adapters.device_probe import SystemProbe
from paperroute_device.adapters.power import NullPower, SystemctlPower
from paperroute_device.adapters.resets import FileResets, NullResets
from paperroute_device.adapters.settings_page import SettingsPage
from paperroute_device.adapters.state_file import DEFAULT_PATH, StateFile
from paperroute_device.adapters.web_device import DevicePages
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.adapters.web_server import BindError, serving
from paperroute_device.adapters.wifi import NmcliWifi, NullWifi
from paperroute_device.core.app import App
from paperroute_device.core.kit_events import QuitRequested
from paperroute_device.logs import LastError, configure_logging
from paperroute_device.ports import (
    Clock,
    KeyStore,
    PowerControl,
    Resets,
    WifiControl,
)
from paperroute_device.runtime.app_web import AppWeb
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.loop import run
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.timings import KitTimings
from paperroute_device.runtime.web_observer import web_observer
from paperroute_hardware.adapters.knob_lines import GpiodKnobLines
from paperroute_hardware.adapters.knob_reader import KnobReader
from paperroute_hardware.adapters.panel_bus import LinuxPanelBus
from paperroute_hardware.adapters.panel_display import Displays
from paperroute_hardware.knobs_open import knobs_for, knobs_state_for
from paperroute_hardware.panel_open import panel_for, panel_times_for
from paperroute_hardware.selftest import selftest_main

if TYPE_CHECKING:
    from paperroute_device.adapters.tk_monitor import TkMonitor

logger = logging.getLogger(__name__)

STOP_SIGNALS: Final[tuple[signal.Signals, ...]] = (
    signal.SIGTERM,
    signal.SIGINT,
)
DATA_DIR: Final[Path] = Path('/var/lib/paperroute')
NO_ZONE: Final[str] = 'paperroute-board: this app has no time zone'


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


def add_device_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add the state file and the switches for what the device may do.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser to add to.
    """
    parser.add_argument(
        '--state',
        type=Path,
        default=DEFAULT_PATH,
        help=f'saved state file (default: {DEFAULT_PATH})',
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


def parse_args(
    *, app: App[Any], argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    """
    Read the command line.

    Parameters
    ----------
    app : App[Any]
        The app, which adds its own flags to the run command.
    argv : Sequence[str] | None
        The arguments; None reads ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='paperroute-board')
    sub = parser.add_subparsers(dest='command', required=True)
    runner = sub.add_parser('run', help='run the device software')
    add_display_arguments(runner)
    add_device_arguments(runner)
    app.add_arguments(parser=runner)
    sub.add_parser('selftest', help='test the e-paper panel')
    sub.add_parser('zone', help="print the app's IANA time zone")
    return parser.parse_args(argv)


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
    keys: Sequence[KeyStore],
    wifi: WifiControl,
    erased: Sequence[Path],
    data_dir: Path = DATA_DIR,
) -> Resets:
    """
    Choose how the settings are reset and everything erased.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    keys : Sequence[KeyStore]
        Hold the API keys.
    wifi : WifiControl
        Forgets the saved Wi-Fi networks.
    erased : Sequence[Path]
        The app's files that Erase everything deletes.
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
        keys=keys,
        wifi=wifi,
        erased=erased,
    )


def device_for(
    *, args: argparse.Namespace, app: App[Any], last_error: LastError,
) -> DevicePorts:
    """
    Make the device's own adapters.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    app : App[Any]
        The app, whose keys are all probed and reset.
    last_error : LastError
        Holds the latest error logged.

    Returns
    -------
    DevicePorts
        Wi-Fi, the system probe, the keys and the resets; a replaying
        app gets Wi-Fi alone, no facts and no resets.
    """
    wifi = wifi_for(args=args)
    if app.replaying():
        return DevicePorts(wifi=wifi)
    keys = app.keys()
    probe = SystemProbe(
        wifi=wifi,
        keys=keys,
        last_error=last_error,
        data_dir=args.state.parent,
        version_file=args.version_file,
    )
    resets = resets_for(
        args=args,
        keys=[key.store for key in keys],
        wifi=wifi,
        erased=app.erased(args=args),
    )
    return DevicePorts(wifi=wifi, probe=probe, keys=keys, resets=resets)


def clock_for(*, app: App[Any], zone: tzinfo) -> Clock:
    """
    Choose the clock the run keeps.

    Parameters
    ----------
    app : App[Any]
        The app, its adapters open; it may keep a clock of its own.
    zone : tzinfo
        The app's zone, or UTC.

    Returns
    -------
    Clock
        The app's clock, or else the system clock in the zone.
    """
    return app.clock() or SystemClock(zone=zone)


def install_stop_signals(
    *, loop: asyncio.AbstractEventLoop, post: Callable[[object], None],
) -> None:
    """
    Post QuitRequested on SIGTERM and SIGINT.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        Running loop.
    post : Callable[[object], None]
        Posts an event to the kit.
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
    *, loop: asyncio.AbstractEventLoop, post: Callable[[object], None],
) -> Callable[[object], None]:
    """
    Make a post that other threads can call.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The running loop.
    post : Callable[[object], None]
        Posts an event; only safe on the loop's thread.

    Returns
    -------
    Callable[[object], None]
        Hands each event to the loop, which posts it on its own thread.
    """

    def send(event: object) -> None:
        """
        Hand an event to the loop.

        Parameters
        ----------
        event : object
            The event.
        """
        loop.call_soon_threadsafe(post, event)

    return send


def bind_web(
    *,
    loop: asyncio.AbstractEventLoop,
    display: WebDisplay,
    post: Callable[[object], None],
    knobs: KnobReader | None = None,
) -> None:
    """
    Send browser knobs, the physical knobs and stop signals to the kit.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        Running loop.
    display : WebDisplay
        Display whose events are bound.
    post : Callable[[object], None]
        Posts an event to the kit; only safe on the loop's thread.
    knobs : KnobReader | None
        The physical knobs, started here; their thread posts through
        the loop.
    """
    display.bind(post=post)
    install_stop_signals(loop=loop, post=post)
    if knobs is not None:
        knobs.start(post=threadsafe(loop=loop, post=post))


async def serve_web(  # pylint: disable=too-many-arguments
    *,
    app: App[Any],
    ports: KitPorts,
    display: WebDisplay,
    host: str,
    port: int,
    zone: tzinfo,
    tick_every: float = 5.0,
    max_events: int | None = None,
    knobs: KnobReader | None = None,
) -> None:
    """
    Run the app and serve its display until it quits.

    Parameters
    ----------
    app : App[Any]
        The app.
    ports : KitPorts
        Adapters; the display must include the given web display.
    display : WebDisplay
        Web display.
    host : str
        Address to listen on.
    port : int
        Port to listen on.
    zone : tzinfo
        Local time zone.
    tick_every : float
        Seconds between ticks.
    max_events : int | None
        Stop after this many events (tests).
    knobs : KnobReader | None
        The physical knobs; stopped before the loop ends, so their
        thread never posts to a closed loop.
    """
    loop = asyncio.get_running_loop()
    async with serving(app=display.app(), host=host, port=port):
        try:
            await run(
                app=app,
                ports=ports,
                zone=zone,
                tick_every=tick_every,
                post_hook=lambda post: bind_web(
                    loop=loop, display=display, post=post, knobs=knobs,
                ),
                max_events=max_events,
            )
        finally:
            if knobs is not None:
                knobs.stop()
            remove_stop_signals(loop=loop)


async def show(
    *, app: App[Any], ports: KitPorts, monitor: 'TkMonitor', zone: tzinfo,
) -> None:
    """
    Run the app on the monitor.

    Parameters
    ----------
    app : App[Any]
        The app.
    ports : KitPorts
        Adapters.
    monitor : TkMonitor
        Window that pumps events and posts key presses.
    zone : tzinfo
        Local time zone.
    """
    await run(
        app=app,
        ports=ports,
        zone=zone,
        pump=monitor.pump,
        post_hook=lambda post: monitor.bind(post=post),
    )


def tk_main(
    *, args: argparse.Namespace, app: App[Any], last_error: LastError,
) -> None:
    """
    Run the device software in a Tkinter window.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    app : App[Any]
        The app.
    last_error : LastError
        Holds the latest error logged.
    """
    from paperroute_device.adapters.tk_monitor import (  # pylint: disable=C0415
        TkMonitor,
    )

    zone = app.zone() or UTC
    monitor = TkMonitor()
    try:
        with app.opened(args=args, last_error=last_error):
            ports = KitPorts(
                display=monitor,
                clock=clock_for(app=app, zone=zone),
                states=StateFile(path=args.state, app=app),
                power=power_for(args=args),
                device=device_for(
                    args=args, app=app, last_error=last_error,
                ),
            )
            asyncio.run(
                show(app=app, ports=ports, monitor=monitor, zone=zone),
            )
    finally:
        monitor.close()


def serve_or_exit(  # pylint: disable=too-many-arguments
    *,
    args: argparse.Namespace,
    app: App[Any],
    ports: KitPorts,
    display: WebDisplay,
    zone: tzinfo,
    knobs: KnobReader | None = None,
) -> None:
    """
    Serve the display; exit 1 if the port can't be used.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    app : App[Any]
        The app.
    ports : KitPorts
        Adapters.
    display : WebDisplay
        Web display.
    zone : tzinfo
        Local time zone.
    knobs : KnobReader | None
        The physical knobs, if any.
    """
    try:
        asyncio.run(
            serve_web(
                app=app,
                ports=ports,
                display=display,
                host=args.host,
                port=args.port,
                zone=zone,
                knobs=knobs,
            )
        )
    except BindError:
        sys.exit(1)


def board_display(
    *, web: AppWeb, app: App[Any], timings: KitTimings,
) -> WebDisplay:
    """
    Build the web display with the app's pages and the Settings page.

    Parameters
    ----------
    web : AppWeb
        The app's part of the web pages.
    app : App[Any]
        The app, whose quiet label the Settings page offers.
    timings : KitTimings
        What the status page and the Settings page read.

    Returns
    -------
    WebDisplay
        The display, with no device pages yet.
    """
    display = WebDisplay(
        status=timings.snapshot,
        nav=web.nav,
        hint=web.hint,
        status_line=web.status_line,
        sheets=web.sheets,
        routes=web.routes,
    )
    display.settings = SettingsPage(
        status=timings.snapshot,
        post=display.send,
        plug=web.settings,
        quiet_label=app.quiet_label,
        nav=web.nav,
        sheets=web.sheets,
    )
    return display


# pylint: disable-next=too-many-arguments
def device_pages_for(
    *,
    args: argparse.Namespace,
    app: App[Any],
    web: AppWeb,
    timings: KitTimings,
    display: WebDisplay,
    device: DevicePorts,
    knob_state: Callable[[], bool | None] = lambda: None,
) -> DevicePages:
    """
    Build the phone setup pages.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    app : App[Any]
        The app, whose reset hint the pages show.
    web : AppWeb
        The app's part of the web pages: the nav, sheets and Data
        section.
    timings : KitTimings
        Holds the latest device status.
    display : WebDisplay
        Sends the pages' events on to the kit.
    device : DevicePorts
        The device's adapters, whose keys the pages show and store.
    knob_state : Callable[[], bool | None]
        Whether the knobs are answering, or None when not asked for.

    Returns
    -------
    DevicePages
        The pages.
    """
    return DevicePages(
        status=lambda: timings.device,
        panel=lambda: timings.panel,
        keys=device.keys,
        post=display.send,
        error_log=args.error_log,
        state_file=args.state,
        knobs=knob_state,
        nav=web.nav,
        reset_hint=app.reset_hint,
        sheets=web.sheets,
        data=web.data,
    )


def web_main(
    *, args: argparse.Namespace, app: App[Any], last_error: LastError,
) -> None:
    """
    Serve the device software to browsers.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    app : App[Any]
        The app.
    last_error : LastError
        Holds the latest error logged.
    """
    zone = app.zone() or UTC
    timings = app.timings()
    observer = web_observer(timings=timings, extra=app.observers())
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
    try:
        with app.opened(args=args, last_error=last_error):
            web = app.web(args=args, timings=timings)
            display = board_display(web=web, app=app, timings=timings)
            device = device_for(args=args, app=app, last_error=last_error)
            if not app.replaying():
                display.device = device_pages_for(
                    args=args,
                    app=app,
                    web=web,
                    timings=timings,
                    display=display,
                    device=device,
                    knob_state=lambda: knobs_state_for(
                        wanted=args.knobs, knobs=knobs,
                    ),
                )
            ports = KitPorts(
                display=Displays(displays=[display, *shown]),
                clock=clock_for(app=app, zone=zone),
                states=StateFile(path=args.state, app=app),
                power=power_for(args=args),
                device=device,
                observer=observer,
            )
            serve_or_exit(
                args=args,
                app=app,
                ports=ports,
                display=display,
                zone=zone,
                knobs=knobs,
            )
    finally:
        if panel is not None:
            panel.stop()
        if knobs is not None:
            knobs.stop()


def zone_main(*, app: App[Any]) -> None:
    """
    Print the app's IANA time zone, or exit 1 when it has none.

    Parameters
    ----------
    app : App[Any]
        The app.
    """
    zone = app.zone()
    if zone is None:
        sys.exit(NO_ZONE)
    print(str(zone))


def main(*, app: App[Any], argv: Sequence[str] | None = None) -> None:
    """
    Run an app on the device: in a window, in browsers or on the panel.

    Parameters
    ----------
    app : App[Any]
        The app.
    argv : Sequence[str] | None
        The arguments; None reads ``sys.argv[1:]``.
    """
    args = parse_args(app=app, argv=argv)
    if args.command == 'selftest':
        sys.exit(selftest_main())
    if args.command == 'zone':
        zone_main(app=app)
        return
    last_error = LastError(zone=app.zone() or UTC)
    configure_logging(error_file=args.error_log, last_error=last_error)
    try:
        if args.display == 'web':
            web_main(args=args, app=app, last_error=last_error)
            return
        for flag in ('panel', 'knobs'):
            if getattr(args, flag):
                logger.warning(
                    '--%s only works with --display web; ignoring it', flag,
                )
        tk_main(args=args, app=app, last_error=last_error)
    except Exception:
        logger.exception('paperroute crashed')
        raise
