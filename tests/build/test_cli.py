"""Tests for the build command line."""

import argparse
import fcntl
import json
import logging
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute.build.__main__ import SYDNEY_BUILD
from paperroute.build.tfnsw_sources import TfnswDownloader
from paperroute.city import SYDNEY, SYDNEY_REF, SYDNEY_ZONE
from paperroute_testkit.build.osm_factory import TINY_BBOX, write_tiny_osm
from paperroute_transit.build.cli import main, parse_args, run_check_tiles
from paperroute_transit.build.sources import BuildParts, SourceSpec
from paperroute_transit.build.tile_store import TileWriter
from paperroute_transit.core.tiles import TILE_STYLE
from tests.build.tile_inputs import write_timetable

ZOOMS_TEXT: Final[str] = '3,5,8,14,25,45'
TWO_SOURCES: Final[BuildParts] = BuildParts(
    city_ref=SYDNEY_REF,
    sources=(
        SourceSpec(name='a', path='x'),
        SourceSpec(name='b_c', path='y'),
    ),
    downloader=TfnswDownloader(key=lambda: 'fake-key-0123456789'),
)


def parsed(*, argv: list[str]) -> argparse.Namespace:
    """
    Parse a command line with Sydney's city and build parts.

    Parameters
    ----------
    argv : list[str]
        Arguments after the program name.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    return parse_args(city=SYDNEY, parts=SYDNEY_BUILD, argv=argv)


def test_start_defaults_to_yesterday() -> None:
    """The window starts a day early to keep NightRide trips."""
    today = datetime.now(SYDNEY_ZONE).date()
    start = parsed(argv=['gtfs', '--bundle', 'b.zip', '--out', 'o.db']).start
    assert start in (
        today - timedelta(days=1),
        today - timedelta(days=2),
    )


def test_gtfs_flags_follow_the_sources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each source gets a flag, and at least one must be given."""
    args = parse_args(
        city=SYDNEY,
        parts=TWO_SOURCES,
        argv=['gtfs', '--b-c', 'f.zip', '--out', 'o.db'],
    )
    assert args.a is None
    assert args.b_c == Path('f.zip')
    with pytest.raises(SystemExit) as info:
        parse_args(
            city=SYDNEY,
            parts=TWO_SOURCES,
            argv=['gtfs', '--out', 'o.db'],
        )
    assert info.value.code == 2
    assert 'give at least one source' in capsys.readouterr().err


def test_tiles_cli_parses_bbox_and_zooms() -> None:
    """The tiles subcommand reads its box, zooms and defaults."""
    args = parsed(
        argv=[
            'tiles', '--osm', 'nsw.osm.pbf',
            '--timetable', 'timetable.db', '--out', 'data/tiles.db',
            '--bbox', '150.95,-33.84,151.03,-33.76', '--zooms', '8,45',
        ],
    )
    assert args.bbox == (150.95, -33.84, 151.03, -33.76)
    assert args.zooms == (8, 45)
    assert args.work is None
    assert args.jobs == (os.cpu_count() or 1)
    assert args.index == 'flex_mem'


def test_tiles_bbox_defaults_to_the_city() -> None:
    """With no box given, the tiles cover the city's box."""
    args = parsed(
        argv=['tiles', '--osm', 'a', '--timetable', 'b', '--out', 'c'],
    )
    assert args.bbox == SYDNEY.bbox


def test_tiles_cli_refuses_a_short_bbox() -> None:
    """A box needs four numbers."""
    with pytest.raises(SystemExit):
        parsed(
            argv=[
                'tiles', '--osm', 'a', '--timetable', 'b',
                '--out', 'c', '--bbox', '150.95,-33.84,151.03',
            ],
        )


def test_refresh_takes_data_dir_and_error_log() -> None:
    """The refresh subcommand reads its paths, days and force flag."""
    args = parsed(
        argv=[
            'refresh',
            '--data-dir',
            '/var/lib/paperroute',
            '--error-log',
            '/var/lib/paperroute/log/errors.jsonl',
        ],
    )
    assert args.data_dir == Path('/var/lib/paperroute')
    assert args.error_log == Path('/var/lib/paperroute/log/errors.jsonl')
    assert args.days == 14
    assert not args.force


@pytest.mark.usefixtures('restored_logging')
def test_refresh_reports_a_held_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A second refresh says it is busy and exits 1 before any request."""
    with (tmp_path / 'refresh.lock').open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit) as info:
            main(
                city=SYDNEY,
                parts=TWO_SOURCES,
                argv=['refresh', '--data-dir', str(tmp_path)],
            )
    assert info.value.code == 1
    assert capsys.readouterr().out == 'refresh already running\n'


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """Put the root logger back after a test that configures it."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def tiles_argv(*, tmp_path: Path, osm: Path) -> list[str]:
    """
    Build a tiles command line over the tiny inputs.

    Parameters
    ----------
    tmp_path : Path
        Directory holding the timetable and the output.
    osm : Path
        The OSM file to read.

    Returns
    -------
    list[str]
        The arguments, with one job and the default work directory.
    """
    write_timetable(path=tmp_path / 'timetable.db')
    return [
        'tiles', '--osm', str(osm),
        '--timetable', str(tmp_path / 'timetable.db'),
        '--out', str(tmp_path / 'tiles.db'),
        '--bbox', ','.join(str(value) for value in TINY_BBOX),
        '--zooms', '8', '--jobs', '1',
    ]


@pytest.mark.usefixtures('restored_logging')
def test_tiles_prints_a_json_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A finished build prints its report as the last line and exits 0."""
    write_tiny_osm(path=tmp_path / 'tiny.osm.pbf')
    with pytest.raises(SystemExit) as info:
        main(
            city=SYDNEY,
            parts=SYDNEY_BUILD,
            argv=tiles_argv(tmp_path=tmp_path, osm=tmp_path / 'tiny.osm.pbf'),
        )
    assert info.value.code == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report['tiles'] > 0
    assert (tmp_path / 'tiles-work').is_dir()


@pytest.mark.usefixtures('restored_logging')
def test_tiles_logs_a_failed_build(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing OSM file is logged and exits 1."""
    with pytest.raises(SystemExit) as info:
        main(
            city=SYDNEY,
            parts=SYDNEY_BUILD,
            argv=tiles_argv(
                tmp_path=tmp_path,
                osm=tmp_path / 'missing.osm.pbf',
            ),
        )
    assert info.value.code == 1
    lines = capsys.readouterr().err.splitlines()
    assert json.loads(lines[-1])['msg'] == 'tile build failed'
    assert not (tmp_path / 'tiles.db').exists()


def tiles_with(*, out: Path, meta: dict[str, str]) -> Path:
    """
    Write a tiles file with no tiles and the given meta.

    Parameters
    ----------
    out : Path
        Destination file.
    meta : dict[str, str]
        Meta rows.

    Returns
    -------
    Path
        The file.
    """
    with TileWriter(out=out) as writer:
        writer.finish(meta=meta)
    return out


def checked(*, path: Path) -> int:
    """
    Check a tiles file against Sydney.

    Parameters
    ----------
    path : Path
        The tiles file.

    Returns
    -------
    int
        The check's exit status.
    """
    return run_check_tiles(args=argparse.Namespace(path=path), city=SYDNEY)


def test_check_tiles_accepts_this_release(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tiles for this release's zooms, style and origin pass quietly."""
    out = tiles_with(
        out=tmp_path / 'tiles.db',
        meta={
            'zooms': ZOOMS_TEXT,
            'style': TILE_STYLE,
            'origin': SYDNEY.projection.origin_text,
        },
    )
    assert checked(path=out) == 0
    assert capsys.readouterr().err == ''


def test_check_tiles_warns_on_other_zooms(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Other zooms fail with both lists and what the Pi will show."""
    out = tiles_with(
        out=tmp_path / 'tiles.db',
        meta={'zooms': '3,5,8,14,45', 'style': TILE_STYLE},
    )
    assert checked(path=out) == 1
    origin = SYDNEY.projection.origin_text
    assert capsys.readouterr().err == (
        f'tiles: {out} has zoom levels 3,5,8,14,45, style {TILE_STYLE} and '
        f'origin none; this release needs {ZOOMS_TEXT}, {TILE_STYLE} and '
        f'{origin}. The Pi will show "Map not installed" until you rebuild '
        'the tiles (README, Map tiles).\n'
    )


def test_check_tiles_refuses_other_origin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tiles drawn around another origin fail; no origin row passes."""
    meta = {'zooms': ZOOMS_TEXT, 'style': TILE_STYLE}
    out = tiles_with(
        out=tmp_path / 'tiles.db',
        meta={**meta, 'origin': '0.0,0.0'},
    )
    assert checked(path=out) == 1
    err = capsys.readouterr().err
    assert 'origin 0.0,0.0;' in err
    assert f'and {SYDNEY.projection.origin_text}.' in err
    plain = tiles_with(out=tmp_path / 'plain.db', meta=meta)
    assert checked(path=plain) == 0
    assert capsys.readouterr().err == ''


def test_check_tiles_unreadable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file that isn't a tiles database fails with none for each."""
    out = tmp_path / 'tiles.db'
    out.write_bytes(b'not a database ' * 100)
    assert checked(path=out) == 1
    err = capsys.readouterr().err
    assert 'has zoom levels none, style none and origin none;' in err


def test_check_tiles_from_the_command_line(tmp_path: Path) -> None:
    """check-tiles takes the path and exits with the check's status."""
    out = tiles_with(
        out=tmp_path / 'tiles.db',
        meta={'zooms': ZOOMS_TEXT, 'style': TILE_STYLE},
    )
    with pytest.raises(SystemExit) as info:
        main(city=SYDNEY, parts=SYDNEY_BUILD, argv=['check-tiles', str(out)])
    assert info.value.code == 0
