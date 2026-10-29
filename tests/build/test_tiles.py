"""Tests for the tile build command."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.tiles_db import SqliteTileRepository
from paperroute.build.features import to_placed
from paperroute.build.labels import label_box
from paperroute.build.tile_store import TILE_FORMAT
from paperroute.build.tiles import TileBuildConfig, TileReport, build_tiles
from paperroute.core.geo import to_metres
from paperroute.core.tiles import (
    TILE_STYLE,
    ZOOMS,
    TileVariant,
    tile_of,
    to_pixels,
)
from tests.build.osm_factory import TIMESTAMP, TINY_BBOX, write_tiny_osm
from tests.build.tile_inputs import STOPS, write_timetable

OLD_TILES: Final[bytes] = b'old tiles file'


def config_for(*, tmp_path: Path, jobs: int) -> TileBuildConfig:
    """
    Build a configuration over the tiny OSM file and timetable.

    Parameters
    ----------
    tmp_path : Path
        Directory holding the inputs.
    jobs : int
        Worker processes.

    Returns
    -------
    TileBuildConfig
        The configuration, writing ``tiles-<jobs>.db``.
    """
    return TileBuildConfig(
        osm=tmp_path / 'tiny.osm.pbf',
        timetable=tmp_path / 'timetable.db',
        out=tmp_path / f'tiles-{jobs}.db',
        work=tmp_path / f'work-{jobs}',
        bbox=TINY_BBOX,
        jobs=jobs,
    )


@pytest.fixture(name='inputs', scope='module')
def _inputs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the tiny OSM file and timetable once."""
    path = tmp_path_factory.mktemp('tiles')
    write_tiny_osm(path=path / 'tiny.osm.pbf')
    write_timetable(path=path / 'timetable.db')
    return path


@pytest.fixture(name='serial', scope='module')
def _serial(inputs: Path) -> TileReport:
    """Build the tiles once with no worker pool."""
    return build_tiles(config=config_for(tmp_path=inputs, jobs=1))


def read_rows(*, path: Path, sql: str) -> list[tuple[object, ...]]:
    """
    Run a query on a database file.

    Parameters
    ----------
    path : Path
        The database file.
    sql : str
        The query.

    Returns
    -------
    list[tuple[object, ...]]
        Every row.
    """
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def test_build_tiles_end_to_end(inputs: Path, serial: TileReport) -> None:
    """Every zoom has tiles, labels and meta, readable on the device."""
    out = inputs / 'tiles-1.db'
    zooms = read_rows(path=out, sql='SELECT DISTINCT zoom FROM tiles')
    assert sorted(int(str(zoom)) for zoom, in zooms) == sorted(ZOOMS)
    meta = {
        str(key): str(value)
        for key, value in read_rows(
            path=out,
            sql='SELECT key, value FROM meta',
        )
    }
    assert meta['format'] == TILE_FORMAT
    assert meta['osm_timestamp'] == TIMESTAMP
    assert meta['tiles'] == str(serial.tiles)
    assert serial.labels > 0
    labels = read_rows(path=out, sql='SELECT count(*) FROM labels')
    assert labels == [(serial.labels,)]
    _, lon, lat = STOPS[0]
    pixel = to_pixels(point=to_metres(lon=lon, lat=lat), mpp=8)
    col, row = tile_of(pixel=pixel)
    repo = SqliteTileRepository(path=out)
    try:
        tile = repo.tile(zoom=8, col=col, row=row, variant=TileVariant.GREY)
    finally:
        repo.close()
    assert tile is not None


def test_build_writes_the_style(inputs: Path, serial: TileReport) -> None:
    """The meta names the map style, so the device can check it fits."""
    assert serial.tiles > 0
    rows = read_rows(
        path=inputs / 'tiles-1.db',
        sql="SELECT value FROM meta WHERE key = 'style'",
    )
    assert rows == [(TILE_STYLE,)]


@pytest.mark.parametrize('mpp', [3, 5, 8])
def test_street_is_named_through_a_stop(
    inputs: Path,
    serial: TileReport,
    mpp: int,
) -> None:
    """No space is kept clear around a stop: a street name runs under it."""
    assert serial.labels
    rows = read_rows(
        path=inputs / 'tiles-1.db',
        sql='SELECT zoom, kind, name, rank, x, y, angle, width, ax, ay,'
        " bx, by FROM labels WHERE name = 'Hollis Street'",
    )
    _, lon, lat = STOPS[0]
    x, y = to_pixels(point=to_metres(lon=lon, lat=lat), mpp=mpp)
    boxes = [
        label_box(label=to_placed(row=row))
        for row in rows
        if row[0] == mpp
    ]
    assert any(
        left <= x <= right and top <= y <= bottom
        for left, top, right, bottom in boxes
    )


def test_build_tiles_cleans_its_work_files(inputs: Path) -> None:
    """The scratch store is gone after a build."""
    assert not (inputs / 'work-1' / 'features.db').exists()


@pytest.mark.usefixtures('serial')
def test_parallel_build_matches_serial(inputs: Path) -> None:
    """Two workers write the same tiles as one."""
    build_tiles(config=config_for(tmp_path=inputs, jobs=2))
    sql = 'SELECT zoom, col, row, grey, bits FROM tiles ORDER BY 1, 2, 3'
    parallel = read_rows(path=inputs / 'tiles-2.db', sql=sql)
    assert parallel == read_rows(path=inputs / 'tiles-1.db', sql=sql)


def test_failed_build_keeps_old_tiles(inputs: Path, tmp_path: Path) -> None:
    """A missing OSM file leaves the old tiles and other files alone."""
    out = tmp_path / 'tiles.db'
    out.write_bytes(OLD_TILES)
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'features.db').write_bytes(b'stale scratch')
    planted = work / 'keep.txt'
    planted.write_text('mine', encoding='utf-8')
    config = TileBuildConfig(
        osm=tmp_path / 'missing.osm.pbf',
        timetable=inputs / 'timetable.db',
        out=out,
        work=work,
        bbox=TINY_BBOX,
    )
    with pytest.raises(FileNotFoundError):
        build_tiles(config=config)
    assert out.read_bytes() == OLD_TILES
    assert not (work / 'features.db').exists()
    assert planted.read_text(encoding='utf-8') == 'mine'


def test_build_tiles_refuses_a_bbox_without_stops(
    inputs: Path,
    tmp_path: Path,
) -> None:
    """A box holding no stops is an error, not an empty file."""
    config = TileBuildConfig(
        osm=inputs / 'tiny.osm.pbf',
        timetable=inputs / 'timetable.db',
        out=tmp_path / 'tiles.db',
        work=tmp_path / 'work',
        bbox=(150.0, -34.0, 150.01, -33.99),
    )
    with pytest.raises(ValueError, match='no stops'):
        build_tiles(config=config)
    assert not (tmp_path / 'tiles.db').exists()


def test_build_writes_the_25_level(inputs: Path, tmp_path: Path) -> None:
    """A build of the 25 m/px level writes its tiles, places and meta."""
    out = tmp_path / 'tiles.db'
    config = TileBuildConfig(
        osm=inputs / 'tiny.osm.pbf',
        timetable=inputs / 'timetable.db',
        out=out,
        work=tmp_path / 'work',
        bbox=TINY_BBOX,
        zooms=(25,),
    )
    build_tiles(config=config)
    zooms = read_rows(path=out, sql='SELECT DISTINCT zoom FROM tiles')
    assert zooms == [(25,)]
    places = read_rows(
        path=out,
        sql='SELECT count(*) FROM labels WHERE zoom = 25'
        " AND kind IN ('suburb', 'station')",
    )
    assert int(str(places[0][0])) > 0
    meta = read_rows(
        path=out,
        sql="SELECT value FROM meta WHERE key = 'zooms'",
    )
    assert meta == [('25',)]
