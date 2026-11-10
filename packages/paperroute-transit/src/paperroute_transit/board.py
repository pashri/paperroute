"""Run a city's board on the device kit, and open what it reads."""

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, tzinfo
from pathlib import Path

from paperroute_device import cli
from paperroute_device.adapters.clocks import ReplayClock, SystemClock
from paperroute_device.ports import Clock
from paperroute_transit.adapters.checked_tiles import CheckedTiles, warn_unfit
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.feed_guard import GuardedFeeds
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.rebuild import (
    ChildRebuilder,
    NullBuilder,
    refresh_command,
    refresh_env,
)
from paperroute_transit.adapters.tiles_db import (
    NullTiles,
    SqliteTileRepository,
)
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.city_parts import CityParts, LiveFeeds
from paperroute_transit.core.city import City
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.tiles import TileVariant
from paperroute_transit.ports import (
    FeedsClient,
    TileRepository,
    TimetableBuilder,
)
from paperroute_transit.summary import Summary


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
        help='replay from this local time',
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


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add the board's arguments to the run command.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The run command's parser, holding the kit's arguments already.
    """
    parser.add_argument('--timetable', type=Path, required=True)
    add_pin_arguments(parser)
    add_source_arguments(parser)
    add_tiles_arguments(parser)
    add_rebuild_argument(parser)


def local_time(*, moment: datetime, zone: tzinfo) -> datetime:
    """
    Give a datetime in a zone: naive ones are taken to be in it already.

    Parameters
    ----------
    moment : datetime
        Naive or aware time.
    zone : tzinfo
        The city's time zone.

    Returns
    -------
    datetime
        Aware datetime in the zone.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=zone)
    return moment.astimezone(zone)


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


def feeds_client(
    *, choice: str, live: LiveFeeds, summary: Summary | None = None,
) -> FeedsClient:
    """
    Make the live client or a replay one.

    The live client backs off a feed after a 429.

    Parameters
    ----------
    choice : str
        'live' or a directory path.
    live : LiveFeeds
        Makes the city's live client; only called for 'live'.
    summary : Summary | None
        Counts the live client's requests; a new one by default.

    Returns
    -------
    FeedsClient
        Client.
    """
    if choice != 'live':
        return FileFeedsClient(directory=Path(choice))
    return GuardedFeeds(inner=live(), summary=summary)


def clock_for(*, args: argparse.Namespace, city: City) -> Clock:
    """
    Make the replay clock if --at was given, else the system clock.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    city : City
        The city, whose zone the clock keeps.

    Returns
    -------
    Clock
        Clock.
    """
    if args.at:
        return ReplayClock(
            start=local_time(moment=args.at, zone=city.zone), zone=city.zone,
        )
    return SystemClock(zone=city.zone)


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
    *, args: argparse.Namespace, city: City,
) -> Iterator[SqliteTimetableRepository]:
    """
    Open the timetable and close it on exit.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    city : City
        The city the timetable is for.

    Yields
    ------
    SqliteTimetableRepository
        The open timetable.
    """
    timetable = SqliteTimetableRepository(path=args.timetable, city=city)
    try:
        yield timetable
    finally:
        timetable.close()


@contextmanager
def network_for(
    *, args: argparse.Namespace, city: City,
) -> Iterator[SqliteNetworkRepository]:
    """
    Open the chooser and runs reader on the timetable, and close it on exit.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    city : City
        The city the timetable is for.

    Yields
    ------
    SqliteNetworkRepository
        The open reader.
    """
    network = SqliteNetworkRepository(path=args.timetable, city=city)
    try:
        yield network
    finally:
        network.close()


@contextmanager
def tiles_for(
    *, args: argparse.Namespace, city: City,
) -> Iterator[TileRepository]:
    """
    Open the tiles file given, if any, and close it on exit.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    city : City
        The city, whose projection origin the tiles must be built for.

    Yields
    ------
    TileRepository
        The tiles file, which may be missing. No tiles without --tiles,
        or when the file was built for other zoom levels, another map
        style or another projection origin; the maps then say "Map not
        installed". A file installed while the app runs is checked when
        it is first seen.
    """
    if args.tiles is None:
        yield NullTiles()
        return
    tiles = SqliteTileRepository(path=args.tiles)
    origin = city.projection.origin_text
    if args.tiles.exists() and not tiles.fits(origin=origin):
        built = tiles.built_for()
        tiles.close()
        warn_unfit(path=args.tiles, built=built, origin=origin)
        yield NullTiles()
        return
    checked = CheckedTiles(tiles=tiles, path=args.tiles, origin=origin)
    try:
        yield checked
    finally:
        checked.close()


def add_rebuild_argument(parser: argparse.ArgumentParser) -> None:
    """
    Add --rebuild.

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


def main(
    *, city: City, parts: CityParts, argv: Sequence[str] | None = None,
) -> None:
    """
    Run a city's board on the device kit.

    Parameters
    ----------
    city : City
        The city.
    parts : CityParts
        The city's runtime wiring.
    argv : Sequence[str] | None
        The arguments; None reads ``sys.argv[1:]``.
    """
    # pylint: disable-next=import-outside-toplevel,cyclic-import
    from paperroute_transit.transit_app import TransitApp

    cli.main(app=TransitApp(city=city, parts=parts), argv=argv)
