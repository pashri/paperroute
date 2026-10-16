"""Command line: run the device software on the development monitor."""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from paperroute.adapters.clocks import ReplayClock, SystemClock
from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.tfnsw import AiohttpFeedsClient
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.adapters.tk_monitor import TkMonitor
from paperroute.app import Ports, run
from paperroute.core.model import SYDNEY, Pin
from paperroute.logs import configure_logging
from paperroute.ports import FeedsClient


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
    runner.add_argument('--from', dest='origin', required=True)
    runner.add_argument('--to', dest='destination', required=True)
    runner.add_argument(
        '--feeds', default='live', help="'live' or a recordings directory"
    )
    runner.add_argument(
        '--at',
        type=datetime.fromisoformat,
        help='replay from this Sydney time',
    )
    return parser.parse_args()


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
    key_file = os.getenv('PAPERROUTE_API_KEY_FILE')
    if not key_file:
        sys.exit(
            'Set PAPERROUTE_API_KEY_FILE to the file holding the TfNSW API key.'
        )
    key = Path(key_file).expanduser().read_text(encoding='utf-8').strip()
    return AiohttpFeedsClient(api_key=key)


async def show(*, ports: Ports, pin: Pin, monitor: TkMonitor) -> None:
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
    """
    try:
        await run(
            ports=ports,
            pin=pin,
            pump=monitor.pump,
            post_hook=lambda post: monitor.bind(post=post),
        )
    finally:
        await ports.feeds.close()


def main() -> None:
    """Run the Stop view for one pin in a Tkinter window."""
    args = parse_args()
    configure_logging()
    feeds = feeds_client(choice=args.feeds)
    clock = (
        ReplayClock(start=args.at.replace(tzinfo=SYDNEY))
        if args.at
        else SystemClock()
    )
    timetable = SqliteTimetableRepository(path=args.timetable)
    monitor = TkMonitor()
    ports = Ports(
        timetable=timetable, feeds=feeds, display=monitor, clock=clock
    )
    pin = Pin(origin=args.origin, destination=args.destination)
    try:
        asyncio.run(show(ports=ports, pin=pin, monitor=monitor))
    finally:
        monitor.close()
        timetable.close()


if __name__ == '__main__':
    main()
