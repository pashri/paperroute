"""Build the tiles file from an OSM extract and the timetable's stops."""

import math
import time
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from paperroute.build.features import FeatureStore, load_features
from paperroute.build.labels import LabelIndex, place_labels
from paperroute.build.osm_source import header_timestamp
from paperroute.build.tile_draw import render_tile
from paperroute.build.tile_set import (
    BLOCK,
    coverage,
    coverage_lonlat,
    stop_points,
    tile_set,
)
from paperroute.build.tile_store import TileWriter
from paperroute.core.geo import MetreBox, to_metres
from paperroute.core.tiles import OPAL_BBOX, ZOOMS
from paperroute.rss import BYTES_PER_MB, max_rss_mb

STORE_NAME: Final[str] = 'features.db'
SCRATCH_NAMES: Final[tuple[str, ...]] = (
    STORE_NAME,
    STORE_NAME + '-journal',
)
ORIGIN: Final[str] = '151.2093,-33.8688'
PROGRESS_EVERY: Final[int] = 1000  # tiles
EVERYWHERE: Final[tuple[float, float, float, float]] = (
    -math.inf,
    -math.inf,
    math.inf,
    math.inf,
)

Tiles = Mapping[int, set[tuple[int, int]]]
Rendered = list[tuple[int, int, bytes, bytes]]


@dataclass(frozen=True)
class TileBuildConfig:
    """
    What to build the tiles from and where to put them.

    Attributes
    ----------
    osm : Path
        The ``.osm.pbf`` extract.
    timetable : Path
        Timetable database; tiles are built around its stops.
    out : Path
        The tiles file, replaced only by a finished build.
    work : Path
        Scratch directory, created if missing. Only the build's own
        files in it are removed.
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees; stops outside it are
        left out.
    zooms : tuple[int, ...]
        Metres per pixel to build.
    jobs : int
        Worker processes; 1 draws in this process.
    index : str
        Osmium node location store, such as ``flex_mem``.
    """

    osm: Path
    timetable: Path
    out: Path
    work: Path
    bbox: tuple[float, float, float, float] = OPAL_BBOX
    zooms: tuple[int, ...] = ZOOMS
    jobs: int = 1
    index: str = 'flex_mem'


@dataclass(frozen=True)
class TileReport:
    """
    The outcome of a tile build.

    Attributes
    ----------
    tiles : int
        Tiles written, over every zoom.
    labels : int
        Labels written, over every zoom.
    seconds : float
        Wall time of the build.
    max_rss_mb : float
        Peak memory of the building process, not counting workers.
    size_mb : float
        Size of the tiles file.
    """

    tiles: int
    labels: int
    seconds: float
    max_rss_mb: float
    size_mb: float


@dataclass(frozen=True)
class BlockJob:
    """
    One block of tiles for a worker to draw.

    Attributes
    ----------
    store : Path
        The finished scratch store, opened read-only.
    mpp : int
        Metres per pixel.
    tiles : tuple[tuple[int, int], ...]
        Column and row of each tile to draw.
    """

    store: Path
    mpp: int
    tiles: tuple[tuple[int, int], ...]


def progress(*, phase: str) -> None:
    """
    Print a progress line.

    Parameters
    ----------
    phase : str
        What the build is doing.
    """
    print(f'progress {phase}', flush=True)


def remove_scratch(*, work: Path) -> None:
    """
    Delete the files the build makes in its work directory.

    Parameters
    ----------
    work : Path
        The work directory. Nothing else in it is touched.
    """
    for name in SCRATCH_NAMES:
        (work / name).unlink(missing_ok=True)


def render_block(job: BlockJob) -> Rendered:
    """
    Draw one block of tiles from the scratch store.

    It runs in worker processes, so it takes and returns only plain,
    picklable values.

    Parameters
    ----------
    job : BlockJob
        The block to draw.

    Returns
    -------
    Rendered
        Column, row, packed grey tile and packed dithered tile, in the
        job's order.
    """
    store = FeatureStore(path=job.store, readonly=True)
    try:
        return [
            (
                col,
                row,
                *render_tile(store=store, mpp=job.mpp, col=col, row=row),
            )
            for col, row in job.tiles
        ]
    finally:
        store.conn.close()


def block_jobs(
    *,
    store: Path,
    mpp: int,
    wanted: set[tuple[int, int]],
) -> list[BlockJob]:
    """
    Split a zoom's tiles into blocks of ``BLOCK`` by ``BLOCK``.

    Parameters
    ----------
    store : Path
        The scratch store.
    mpp : int
        Metres per pixel.
    wanted : set[tuple[int, int]]
        Tiles to draw.

    Returns
    -------
    list[BlockJob]
        One job per block, blocks and tiles in sorted order.
    """
    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for col, row in sorted(wanted):
        groups.setdefault((col // BLOCK, row // BLOCK), []).append((col, row))
    return [
        BlockJob(store=store, mpp=mpp, tiles=tuple(groups[block]))
        for block in sorted(groups)
    ]


def wanted_tiles(*, config: TileBuildConfig) -> Tiles:
    """
    Find the stops in the box and the tiles around them.

    Parameters
    ----------
    config : TileBuildConfig
        The build.

    Returns
    -------
    Tiles
        The tiles at each zoom.

    Raises
    ------
    ValueError
        When the box holds no stops.
    """
    progress(phase='stops')
    stops = stop_points(timetable=config.timetable, bbox=config.bbox)
    if not stops:
        raise ValueError(f'no stops in the bbox {config.bbox}')
    return {mpp: tile_set(stops=stops, mpp=mpp) for mpp in config.zooms}


def metre_box(*, bbox: tuple[float, float, float, float]) -> MetreBox:
    """
    Turn a box in degrees into metres.

    Parameters
    ----------
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    west, south = to_metres(lon=bbox[0], lat=bbox[1])
    east, north = to_metres(lon=bbox[2], lat=bbox[3])
    return (west, south, east, north)


def read_features(
    *,
    config: TileBuildConfig,
    store: FeatureStore,
    tiles: Tiles,
) -> None:
    """
    Read the features under every tile into the store.

    The sea is filled only inside the box that was read, since coastline
    outside it is never read and would leave the sea misjudged.

    Parameters
    ----------
    config : TileBuildConfig
        The build.
    store : FeatureStore
        A new writable store.
    tiles : Tiles
        Tiles at each zoom.

    Raises
    ------
    FileNotFoundError
        When the OSM file is missing.
    """
    progress(phase='features')
    if not config.osm.is_file():
        raise FileNotFoundError(f'no OSM file at {config.osm}')
    bbox = coverage_lonlat(box=coverage(tiles=tiles))
    load_features(
        osm=config.osm,
        bbox=bbox,
        store=store,
        frame=metre_box(bbox=bbox),
        index=config.index,
    )


def add_labels(
    *,
    store: FeatureStore,
    tiles: Tiles,
) -> int:
    """
    Place the labels at each zoom and commit them to the store.

    Parameters
    ----------
    store : FeatureStore
        The store holding the features.
    tiles : Tiles
        Tiles at each zoom.

    Returns
    -------
    int
        Labels placed, over every zoom.
    """
    index = LabelIndex(conn=store.conn)
    count = 0
    for mpp, wanted in tiles.items():
        progress(phase=f'labels {mpp}')
        labels = list(
            place_labels(
                store=store,
                tiles={mpp: wanted},
                index=index,
            ),
        )
        store.add_labels(labels=labels)
        count += len(labels)
    return count


def drawn(
    *,
    jobs: Iterable[BlockJob],
    pool: ProcessPoolExecutor | None,
    workers: int,
) -> Iterator[Rendered]:
    """
    Draw blocks in this process or across a pool, in order.

    Parameters
    ----------
    jobs : Iterable[BlockJob]
        Blocks to draw.
    pool : ProcessPoolExecutor | None
        Worker pool, or None to draw here.
    workers : int
        Worker count, which bounds the blocks queued ahead.

    Returns
    -------
    Iterator[Rendered]
        Each block's tiles.
    """
    if pool is None:
        return map(render_block, jobs)
    return pool.map(render_block, jobs, buffersize=2 * workers)


def write_zoom(
    *,
    writer: TileWriter,
    mpp: int,
    blocks: Iterable[Rendered],
    total: int,
) -> int:
    """
    Write a zoom's drawn tiles, printing progress.

    Parameters
    ----------
    writer : TileWriter
        The open tiles file.
    mpp : int
        Metres per pixel.
    blocks : Iterable[Rendered]
        Each block's tiles.
    total : int
        Tiles expected at this zoom.

    Returns
    -------
    int
        Tiles written.
    """
    progress(phase=f'tiles {mpp} 0/{total}')
    done = 0
    for block in blocks:
        for col, row, grey, bits in block:
            writer.add_tile(zoom=mpp, col=col, row=row, grey=grey, bits=bits)
            done += 1
            if done % PROGRESS_EVERY == 0:
                progress(phase=f'tiles {mpp} {done}/{total}')
    return done


def write_tiles(
    *,
    writer: TileWriter,
    store: Path,
    tiles: Tiles,
    workers: int,
) -> int:
    """
    Draw and write every tile.

    Parameters
    ----------
    writer : TileWriter
        The open tiles file.
    store : Path
        The finished scratch store.
    tiles : Tiles
        Tiles at each zoom.
    workers : int
        Worker processes; 1 draws in this process.

    Returns
    -------
    int
        Tiles written, over every zoom.
    """
    with ExitStack() as stack:
        pool = (
            stack.enter_context(ProcessPoolExecutor(max_workers=workers))
            if workers > 1
            else None
        )
        return sum(
            write_zoom(
                writer=writer,
                mpp=mpp,
                blocks=drawn(
                    jobs=block_jobs(store=store, mpp=mpp, wanted=wanted),
                    pool=pool,
                    workers=workers,
                ),
                total=len(wanted),
            )
            for mpp, wanted in tiles.items()
        )


def check_count(*, writer: TileWriter, expected: int) -> None:
    """
    Check the tiles file holds every tile.

    Parameters
    ----------
    writer : TileWriter
        The open tiles file.
    expected : int
        Tiles wanted, over every zoom.

    Raises
    ------
    RuntimeError
        When the count differs.
    """
    progress(phase='check')
    (count,) = writer.database().execute(
        'SELECT count(*) FROM tiles',
    ).fetchone()
    if count != expected:
        raise RuntimeError(f'wrote {count} tiles, wanted {expected}')


def swap(
    *,
    config: TileBuildConfig,
    writer: TileWriter,
    store: FeatureStore,
    count: int,
) -> None:
    """
    Copy the labels and meta into the tiles file and move it into place.

    Parameters
    ----------
    config : TileBuildConfig
        The build.
    writer : TileWriter
        The open tiles file.
    store : FeatureStore
        The store holding the placed labels.
    count : int
        Tiles written.
    """
    progress(phase='swap')
    for mpp in config.zooms:
        writer.add_labels(labels=store.labels_in(zoom=mpp, box=EVERYWHERE))
    writer.finish(
        meta={
            'bbox': ','.join(str(value) for value in config.bbox),
            'zooms': ','.join(str(mpp) for mpp in config.zooms),
            'origin': ORIGIN,
            'osm_timestamp': header_timestamp(path=config.osm),
            'tiles': str(count),
        },
    )


def build_in(
    *,
    config: TileBuildConfig,
    store: FeatureStore,
) -> tuple[int, int]:
    """
    Run every phase of the build against an open scratch store.

    Parameters
    ----------
    config : TileBuildConfig
        The build.
    store : FeatureStore
        A new writable store in the work directory.

    Returns
    -------
    tuple[int, int]
        Tiles and labels written.
    """
    tiles = wanted_tiles(config=config)
    read_features(config=config, store=store, tiles=tiles)
    labels = add_labels(store=store, tiles=tiles)
    with TileWriter(out=config.out) as writer:
        count = write_tiles(
            writer=writer,
            store=config.work / STORE_NAME,
            tiles=tiles,
            workers=config.jobs,
        )
        check_count(
            writer=writer,
            expected=sum(len(wanted) for wanted in tiles.values()),
        )
        swap(config=config, writer=writer, store=store, count=count)
    return count, labels


def build_tiles(*, config: TileBuildConfig) -> TileReport:
    """
    Build the tiles file around the stops in a box.

    Parameters
    ----------
    config : TileBuildConfig
        The build.

    Returns
    -------
    TileReport
        Counts, time, peak memory and file size.
    """
    start = time.monotonic()
    config.work.mkdir(parents=True, exist_ok=True)
    remove_scratch(work=config.work)
    store = FeatureStore(path=config.work / STORE_NAME)
    try:
        tiles, labels = build_in(config=config, store=store)
    finally:
        store.conn.close()
        remove_scratch(work=config.work)
    return TileReport(
        tiles=tiles,
        labels=labels,
        seconds=time.monotonic() - start,
        max_rss_mb=max_rss_mb(),
        size_mb=config.out.stat().st_size / BYTES_PER_MB,
    )
