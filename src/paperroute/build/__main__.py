"""Command line for building the timetable and the map tiles."""

import argparse
import json
import logging
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Final

from paperroute.adapters.tfnsw import api_key_from_env
from paperroute.asset_root import use_app_assets
from paperroute.build.gtfs_files import Source
from paperroute.build.handoff import Handoff
from paperroute.build.refresh import RefreshBusy, refresh
from paperroute.build.swap import guarded
from paperroute.build.tile_store import tile_meta
from paperroute.build.timetable import Window, build_timetable, yesterday
from paperroute.core.tiles import NSW_BBOX, TILE_STYLE, ZOOMS, levels_text
from paperroute.logs import configure_logging

logger = logging.getLogger(__name__)

TILE_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
)
BBOX_SIDES: Final[int] = 4


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


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """
    Read a box written as ``W,S,E,N``.

    Parameters
    ----------
    text : str
        Four comma-separated numbers in degrees.

    Returns
    -------
    tuple[float, float, float, float]
        West, south, east and north.

    Raises
    ------
    ValueError
        When the text is not four numbers.
    """
    values = tuple(float(part) for part in text.split(','))
    if len(values) != BBOX_SIDES:
        raise ValueError(f'a bbox needs {BBOX_SIDES} numbers: {text}')
    west, south, east, north = values
    return (west, south, east, north)


def parse_zooms(text: str) -> tuple[int, ...]:
    """
    Read comma-separated metres per pixel.

    Parameters
    ----------
    text : str
        Whole numbers, such as ``3,5,8``.

    Returns
    -------
    tuple[int, ...]
        The zooms, in the given order.
    """
    return tuple(int(part) for part in text.split(','))


def add_tiles(*, command: argparse.ArgumentParser) -> None:
    """
    Add the tiles subcommand's options.

    Parameters
    ----------
    command : argparse.ArgumentParser
        The tiles subparser.
    """
    command.add_argument('--osm', type=Path, required=True)
    command.add_argument('--timetable', type=Path, required=True)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--work', type=Path)
    command.add_argument('--bbox', type=parse_bbox, default=NSW_BBOX)
    command.add_argument('--zooms', type=parse_zooms, default=ZOOMS)
    command.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    command.add_argument('--index', default='flex_mem')


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
    add_tiles(
        command=sub.add_parser('tiles', help='build the map tiles'),
    )
    sub.add_parser(
        'check-tiles',
        help='check a tiles file fits this release',
    ).add_argument('path', type=Path)
    return parser.parse_args()


def run_gtfs(*, args: argparse.Namespace) -> None:
    """
    Build the timetable from the given feeds.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed gtfs arguments.
    """
    configure_logging()
    preferred = [Source(name=name, archive=path) for name, path in
                 (('sydneytrains', args.sydneytrains),
                  ('metro', args.metro)) if path]
    build_timetable(
        sources=[*preferred, Source(name='bundle', archive=args.bundle)],
        window=Window(start=args.start, days=args.days), out=args.out,
        handoff=Handoff(
            path=args.out.with_name(f'{args.out.stem}.handoff.json'),
        ),
    )


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
    try:
        report = guarded(
            step=lambda: refresh(
                data_dir=args.data_dir,
                api_key=api_key_from_env(),
                days=args.days,
                force=args.force,
                error_log=args.error_log,
                hand_over=True,
            ),
            passthrough=(RefreshBusy,),
        )
    except RefreshBusy:
        print('refresh already running')
        return 1
    if report is None:
        return 1
    print(json.dumps(asdict(report)))
    return 0


def run_tiles(*, args: argparse.Namespace) -> int:
    """
    Build the map tiles and print a JSON report.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed tiles arguments.

    Returns
    -------
    int
        Exit status: 0 on success, 1 if the build failed.
    """
    # Imported here so the refresh never loads osmium or shapely.
    from paperroute.build.tiles import (  # pylint: disable=C0415
        TileBuildConfig,
        build_tiles,
    )

    configure_logging()
    config = TileBuildConfig(
        osm=args.osm,
        timetable=args.timetable,
        out=args.out,
        work=args.work or args.out.parent / 'tiles-work',
        bbox=args.bbox,
        zooms=args.zooms,
        jobs=args.jobs,
        index=args.index,
    )
    try:
        report = build_tiles(config=config)
    except TILE_ERRORS:
        logger.exception('tile build failed')
        return 1
    print(json.dumps(asdict(report)))
    return 0


def run_check_tiles(*, args: argparse.Namespace) -> int:
    """
    Check a tiles file was built for this release's zooms and style.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed check-tiles arguments.

    Returns
    -------
    int
        Exit status: 0 when it fits, 1 (after a message) when it doesn't.
    """
    zooms, style = tile_meta(path=args.path)
    if zooms == ZOOMS and style == TILE_STYLE:
        return 0
    print(
        f'tiles: {args.path} has zoom levels {levels_text(zooms=zooms)}'
        f' and style {style or "none"}; this release needs'
        f' {levels_text(zooms=ZOOMS)} and {TILE_STYLE}. The Pi will show'
        ' "Map not installed" until you rebuild the tiles (README, Map'
        ' tiles).',
        file=sys.stderr,
    )
    return 1


def main() -> None:
    """Run the chosen subcommand."""
    use_app_assets()
    args = parse_args()
    if args.command == 'check-tiles':
        sys.exit(run_check_tiles(args=args))
    if args.command == 'refresh':
        sys.exit(run_refresh(args=args))
    if args.command == 'tiles':
        sys.exit(run_tiles(args=args))
    run_gtfs(args=args)


if __name__ == '__main__':
    main()
