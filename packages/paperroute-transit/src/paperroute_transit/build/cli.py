"""Command line for building the timetable and the map tiles."""

import argparse
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Final

from paperroute_device.logs import configure_logging
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.handoff import Handoff
from paperroute_transit.build.refresh import RefreshBusy, refresh
from paperroute_transit.build.sources import BuildParts
from paperroute_transit.build.swap import guarded, printed
from paperroute_transit.build.timetable import (
    Window,
    build_timetable,
    yesterday,
)
from paperroute_transit.core.city import City
from paperroute_transit.core.tiles import TILE_STYLE, ZOOMS, levels_text

logger = logging.getLogger(__name__)

TILE_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
)
BBOX_SIDES: Final[int] = 4


def add_gtfs(
    *,
    gtfs: argparse.ArgumentParser,
    city: City,
    parts: BuildParts,
) -> None:
    """
    Add the gtfs subcommand's options.

    Parameters
    ----------
    gtfs : argparse.ArgumentParser
        The gtfs subparser.
    city : City
        The city, whose zone sets the default start.
    parts : BuildParts
        The city's sources; each gets a flag naming its zip.
    """
    for spec in parts.sources:
        gtfs.add_argument(
            f'--{spec.name.replace("_", "-")}',
            dest=spec.name,
            type=Path,
        )
    gtfs.add_argument('--out', type=Path, required=True)
    gtfs.add_argument(
        '--start',
        type=date.fromisoformat,
        default=yesterday(zone=city.zone),
    )
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


def add_tiles(*, command: argparse.ArgumentParser, city: City) -> None:
    """
    Add the tiles subcommand's options.

    Parameters
    ----------
    command : argparse.ArgumentParser
        The tiles subparser.
    city : City
        The city, whose box is the default.
    """
    command.add_argument('--osm', type=Path, required=True)
    command.add_argument('--timetable', type=Path, required=True)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--work', type=Path)
    command.add_argument('--bbox', type=parse_bbox, default=city.bbox)
    command.add_argument('--zooms', type=parse_zooms, default=ZOOMS)
    command.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    command.add_argument('--index', default='flex_mem')


def parse_args(
    *,
    city: City,
    parts: BuildParts,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    """
    Read the command line.

    Parameters
    ----------
    city : City
        The city, for the defaults.
    parts : BuildParts
        The city's sources, for the gtfs flags.
    argv : Sequence[str] | None
        Arguments after the program name; the process's when None.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='paperroute-build')
    sub = parser.add_subparsers(dest='command', required=True)
    gtfs = sub.add_parser('gtfs', help='build the timetable database')
    add_gtfs(gtfs=gtfs, city=city, parts=parts)
    add_refresh(
        command=sub.add_parser(
            'refresh',
            help='download changed timetables and rebuild',
        ),
    )
    add_tiles(
        command=sub.add_parser('tiles', help='build the map tiles'),
        city=city,
    )
    sub.add_parser(
        'check-tiles',
        help='check a tiles file fits this release',
    ).add_argument('path', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'gtfs' and not any(
        getattr(args, spec.name) for spec in parts.sources
    ):
        gtfs.error('give at least one source')
    return args


def run_gtfs(*, args: argparse.Namespace, parts: BuildParts) -> None:
    """
    Build the timetable from the given feeds.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed gtfs arguments.
    parts : BuildParts
        The city's reference and its sources, in the order they load.
    """
    configure_logging()
    build_timetable(
        sources=[
            Source(name=spec.name, archive=getattr(args, spec.name))
            for spec in parts.sources
            if getattr(args, spec.name)
        ],
        window=Window(start=args.start, days=args.days), out=args.out,
        city_ref=parts.city_ref,
        handoff=Handoff(
            path=args.out.with_name(f'{args.out.stem}.handoff.json'),
        ),
    )


def run_refresh(
    *,
    args: argparse.Namespace,
    city: City,
    parts: BuildParts,
) -> int:
    """
    Refresh the timetable and print a JSON report.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed refresh arguments.
    city : City
        The city.
    parts : BuildParts
        The city's sources and their downloader.

    Returns
    -------
    int
        Exit status: 0 on success, 1 if busy, failed or every source
        failed.
    """
    configure_logging(error_file=args.error_log)
    try:
        report = guarded(
            step=lambda: refresh(
                data_dir=args.data_dir,
                city=city,
                parts=parts,
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
    return printed(report=report)


def run_tiles(*, args: argparse.Namespace, city: City) -> int:
    """
    Build the map tiles and print a JSON report.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed tiles arguments.
    city : City
        The city, for its projection and area.

    Returns
    -------
    int
        Exit status: 0 on success, 1 if the build failed.
    """
    # Imported here so the refresh never loads osmium or shapely.
    from paperroute_transit.build.tiles import (  # pylint: disable=C0415
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
        projection=city.projection,
        area=city.bbox,
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


class OneLine(logging.Formatter):
    """Format a record as its message and its exception's, on one line."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the record without a traceback.

        Parameters
        ----------
        record : logging.LogRecord
            The record.

        Returns
        -------
        str
            The message, then the exception's message after a colon when
            the record carries one.
        """
        message = record.getMessage()
        if record.exc_info is not None and record.exc_info[1] is not None:
            return f'{message}: {record.exc_info[1]}'
        return message


@contextmanager
def one_line_logs() -> Iterator[None]:
    """
    Print log records to stderr on one line each while the block runs.

    Yields
    ------
    None
        Once, while the handler is attached to the root logger.
    """
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(OneLine())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def run_check_tiles(*, args: argparse.Namespace, city: City) -> int:
    """
    Check a tiles file was built for this release and this city.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed check-tiles arguments.
    city : City
        The city, whose projection origin the tiles must share.

    Returns
    -------
    int
        Exit status: 0 when the zooms and style match and the origin, if
        recorded, is the city's; 1 (after a message) otherwise. A file
        that cannot be read is reported in one line, with no traceback.
    """
    origin = city.projection.origin_text
    with one_line_logs():
        tiles = SqliteTileRepository(path=args.path)
        try:
            fits = tiles.fits(origin=origin)
            zooms, style, built_origin = tiles.built_for()
        finally:
            tiles.close()
    if fits:
        return 0
    print(
        f'tiles: {args.path} has zoom levels {levels_text(zooms=zooms)},'
        f' style {style or "none"} and origin {built_origin or "none"};'
        f' this release needs {levels_text(zooms=ZOOMS)}, {TILE_STYLE}'
        f' and {origin}. The Pi will show "Map not installed" until you'
        ' rebuild the tiles (README, Map tiles).',
        file=sys.stderr,
    )
    return 1


def main(
    *,
    city: City,
    parts: BuildParts,
    argv: Sequence[str] | None = None,
) -> None:
    """
    Run the chosen subcommand.

    Parameters
    ----------
    city : City
        The city to build for.
    parts : BuildParts
        The city's reference, its sources and their downloader.
    argv : Sequence[str] | None
        Arguments after the program name; the process's when None.
    """
    args = parse_args(city=city, parts=parts, argv=argv)
    if args.command == 'check-tiles':
        sys.exit(run_check_tiles(args=args, city=city))
    if args.command == 'refresh':
        sys.exit(run_refresh(args=args, city=city, parts=parts))
    if args.command == 'tiles':
        sys.exit(run_tiles(args=args, city=city))
    run_gtfs(args=args, parts=parts)
