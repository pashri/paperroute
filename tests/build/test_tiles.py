"""Tests for the tile build command."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Final

import osmium
import pytest
from osmium.osm.mutable import Node

from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_testkit.build.osm_factory import (
    TIMESTAMP,
    TINY_BBOX,
    write_rail_osm,
    write_tiny_osm,
)
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.build.features import to_placed
from paperroute_transit.build.labels import label_box
from paperroute_transit.build.tile_store import TILE_FORMAT
from paperroute_transit.build.tiles import (
    TileBuildConfig,
    TileReport,
    build_tiles,
)
from paperroute_transit.build.track_db import read_track_db
from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.map_places import (
    STATION_SOURCES,
    MapPlaces,
    StationSource,
)
from paperroute_transit.core.tiles import (
    TILE_STYLE,
    ZOOMS,
    TileVariant,
    tile_of,
    to_pixels,
)
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
        projection=SYDNEY.projection,
        area=SYDNEY.bbox,
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
    pixel = to_pixels(
        point=to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION),
        mpp=8,
        projection=SYDNEY_PROJECTION,
    )
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


def test_meta_origin_is_sydney(inputs: Path, serial: TileReport) -> None:
    """The meta records the projection origin the tiles are drawn in."""
    assert serial.tiles > 0
    rows = read_rows(
        path=inputs / 'tiles-1.db',
        sql="SELECT value FROM meta WHERE key = 'origin'",
    )
    assert rows == [('151.2093,-33.8688',)]


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
    x, y = to_pixels(
        point=to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION),
        mpp=mpp,
        projection=SYDNEY_PROJECTION,
    )
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
        projection=SYDNEY.projection,
        area=SYDNEY.bbox,
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
        projection=SYDNEY.projection,
        area=SYDNEY.bbox,
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
        projection=SYDNEY.projection,
        area=SYDNEY.bbox,
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


def label_names(*, config: TileBuildConfig) -> set[str]:
    """
    Build the tiles and list the names of the suburb labels.

    Parameters
    ----------
    config : TileBuildConfig
        The build.

    Returns
    -------
    set[str]
        Names of the suburb labels at 8 m/px.
    """
    build_tiles(config=config)
    rows = read_rows(
        path=config.out,
        sql="SELECT name FROM labels WHERE zoom = 8 AND kind = 'suburb'",
    )
    return {str(name) for name, in rows}


def test_build_names_the_places_a_city_adds(
    inputs: Path,
    tmp_path: Path,
) -> None:
    """A city's extra place is labelled in the built tiles."""
    base = config_for(tmp_path=inputs, jobs=1)
    config = replace(
        base,
        out=tmp_path / 'tiles.db',
        work=tmp_path / 'work',
        zooms=(8,),
        places=MapPlaces(extras=(('Extra', -33.7985, 151.001),)),
    )
    assert 'E X T R A' in label_names(config=config)


def test_build_drops_the_kinds_a_city_leaves_out(
    inputs: Path,
    tmp_path: Path,
) -> None:
    """A suburb is not labelled when the city's kinds leave it out."""
    base = config_for(tmp_path=inputs, jobs=1)
    config = replace(
        base,
        out=tmp_path / 'tiles.db',
        work=tmp_path / 'work',
        zooms=(8,),
        places=MapPlaces(kinds=frozenset({'town'})),
    )
    assert label_names(config=config) == set()


def station_names(*, tmp_path: Path, places: MapPlaces) -> set[str]:
    """
    Build tiles around a ferry terminal and a rail station.

    Parameters
    ----------
    tmp_path : Path
        Directory for the inputs and the output.
    places : MapPlaces
        The city's map place rules.

    Returns
    -------
    set[str]
        Names of the station labels at 8 m/px.
    """
    osm = tmp_path / 'ferry.osm.pbf'
    with osmium.SimpleWriter(osm, overwrite=True) as writer:
        writer.add_node(
            Node(
                id=1,
                location=(151.000, -33.798),
                tags={'amenity': 'ferry_terminal', 'name': 'Quay Wharf'},
            ),
        )
        writer.add_node(
            Node(
                id=3,
                location=(151.001, -33.802),
                tags={'railway': 'tram_stop', 'name': 'Tram Stop'},
            ),
        )
        writer.add_node(
            Node(
                id=2,
                location=(151.003, -33.804),
                tags={'railway': 'station', 'name': 'Parramatta'},
            ),
        )
    write_timetable(path=tmp_path / 'timetable.db')
    config = replace(
        config_for(tmp_path=tmp_path, jobs=1),
        osm=osm,
        zooms=(8,),
        places=places,
    )
    build_tiles(config=config)
    rows = read_rows(
        path=config.out,
        sql="SELECT name FROM labels WHERE zoom = 8 AND kind = 'station'",
    )
    return {str(name) for name, in rows}


def test_build_labels_every_station_source_by_default(
    tmp_path: Path,
) -> None:
    """With the default rules rail, minor and ferry stations are named."""
    names = station_names(tmp_path=tmp_path, places=MapPlaces())
    assert names == {'Quay Wharf', 'Parramatta', 'Tram Stop'}


@pytest.mark.parametrize(
    ('left_out', 'gone'),
    [
        (StationSource.RAIL, 'Parramatta'),
        (StationSource.MINOR, 'Tram Stop'),
        (StationSource.FERRY, 'Quay Wharf'),
    ],
)
def test_build_leaves_out_a_station_source_when_told(
    tmp_path: Path,
    left_out: StationSource,
    gone: str,
) -> None:
    """A source the city leaves out has no label; the others keep theirs."""
    names = station_names(
        tmp_path=tmp_path,
        places=MapPlaces(stations=STATION_SOURCES - {left_out}),
    )
    assert names == {'Quay Wharf', 'Parramatta', 'Tram Stop'} - {gone}


def test_tiles_writes_the_track(inputs: Path, tmp_path: Path) -> None:
    """A build given a track file stores its graph and counts it."""
    rail = tmp_path / 'rail.osm.pbf'
    write_rail_osm(path=rail)
    config = replace(
        config_for(tmp_path=inputs, jobs=1),
        out=tmp_path / 'tiles-track.db',
        work=tmp_path / 'work-track',
        track_osm=(rail,),
    )
    build_tiles(config=config)
    track = read_track_db(path=config.out)
    assert track is not None
    assert len(track.xs) == 3
    assert [s.name for s in track.stations] == ['Halt One']
    meta = read_rows(
        path=config.out,
        sql="SELECT value FROM meta WHERE key = 'track_nodes'",
    )
    assert meta == [('3',)]
