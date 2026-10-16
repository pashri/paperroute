"""Command line for building the timetable."""

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from paperroute.build.timetable import Source, Window, build_timetable
from paperroute.core.model import SYDNEY


def yesterday() -> date:
    """
    The Sydney date before today.

    Starting a day early keeps the previous service day's 24:00-28:59
    trips when the build runs in the small hours.

    Returns
    -------
    date
        Yesterday in Sydney.
    """
    return datetime.now(SYDNEY).date() - timedelta(days=1)


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
    gtfs = sub.add_parser('gtfs', help='build the timetable database')
    gtfs.add_argument('--bundle', type=Path, required=True)
    gtfs.add_argument('--sydneytrains', type=Path)
    gtfs.add_argument('--metro', type=Path)
    gtfs.add_argument('--out', type=Path, required=True)
    gtfs.add_argument('--start', type=date.fromisoformat,
                      default=yesterday())
    gtfs.add_argument('--days', type=int, default=14)
    return parser.parse_args()


def main() -> None:
    """Build the timetable from the given feeds."""
    args = parse_args()
    preferred = [Source(name=name, archive=path) for name, path in
                 (('sydneytrains', args.sydneytrains),
                  ('metro', args.metro)) if path]
    report = build_timetable(
        sources=[*preferred, Source(name='bundle', archive=args.bundle)],
        window=Window(start=args.start, days=args.days), out=args.out)
    print(f'progress done dropped={report.dropped} '
          f'partial={report.partial}')


if __name__ == '__main__':
    main()
