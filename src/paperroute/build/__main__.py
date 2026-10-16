"""Command line for building and refreshing the timetable."""

import argparse
import json
import logging
import sqlite3
import sys
import zipfile
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Final

from paperroute.adapters.tfnsw import api_key_from_env
from paperroute.build.refresh import RefreshBusy, TimetableCheckError, refresh
from paperroute.build.sources import SourceError
from paperroute.build.timetable import (
    Source,
    Window,
    build_timetable,
    yesterday,
)
from paperroute.logs import configure_logging

logger = logging.getLogger(__name__)

REFRESH_ERRORS: Final[tuple[type[Exception], ...]] = (
    SourceError,
    TimetableCheckError,
    sqlite3.Error,
    zipfile.BadZipFile,
    OSError,
    KeyError,
    ValueError,
)


def add_gtfs(*, gtfs: argparse.ArgumentParser) -> None:
    """
    Add the gtfs subcommand's options.

    Parameters
    ----------
    gtfs : argparse.ArgumentParser
        The gtfs subparser.
    """
    gtfs.add_argument('--bundle', type=Path, required=True)
    gtfs.add_argument('--sydneytrains', type=Path)
    gtfs.add_argument('--metro', type=Path)
    gtfs.add_argument('--out', type=Path, required=True)
    gtfs.add_argument('--start', type=date.fromisoformat,
                      default=yesterday())
    gtfs.add_argument('--days', type=int, default=14)


def add_refresh(*, command: argparse.ArgumentParser) -> None:
    """
    Add the refresh subcommand's options.

    Parameters
    ----------
    command : argparse.ArgumentParser
        The refresh subparser.
    """
    command.add_argument('--data-dir', type=Path, required=True)
    command.add_argument('--days', type=int, default=14)
    command.add_argument('--force', action='store_true')
    command.add_argument('--error-log', type=Path)


def parse_args() -> argparse.Namespace:
    """
    Read the command line.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m paperroute.build')
    sub = parser.add_subparsers(dest='command', required=True)
    add_gtfs(
        gtfs=sub.add_parser('gtfs', help='build the timetable database'),
    )
    add_refresh(
        command=sub.add_parser(
            'refresh',
            help='download changed TfNSW timetables and rebuild',
        ),
    )
    return parser.parse_args()


def run_gtfs(*, args: argparse.Namespace) -> None:
    """
    Build the timetable from the given feeds.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed gtfs arguments.
    """
    preferred = [Source(name=name, archive=path) for name, path in
                 (('sydneytrains', args.sydneytrains),
                  ('metro', args.metro)) if path]
    report = build_timetable(
        sources=[*preferred, Source(name='bundle', archive=args.bundle)],
        window=Window(start=args.start, days=args.days), out=args.out)
    print(f'progress done dropped={report.dropped} '
          f'partial={report.partial}')


def run_refresh(*, args: argparse.Namespace) -> int:
    """
    Refresh the timetable and print a JSON report.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed refresh arguments.

    Returns
    -------
    int
        Exit status: 0 on success, 1 if busy or failed.
    """
    configure_logging(error_file=args.error_log)
    api_key = api_key_from_env()
    try:
        report = refresh(
            data_dir=args.data_dir,
            api_key=api_key,
            days=args.days,
            force=args.force,
        )
    except RefreshBusy:
        print('refresh already running')
        return 1
    except REFRESH_ERRORS:
        logger.exception('refresh failed')
        return 1
    print(json.dumps(asdict(report)))
    return 0


def main() -> None:
    """Run the chosen subcommand."""
    args = parse_args()
    if args.command == 'refresh':
        sys.exit(run_refresh(args=args))
    run_gtfs(args=args)


if __name__ == '__main__':
    main()
