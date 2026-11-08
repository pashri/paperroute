"""Tests for the tile repository."""

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest

from paperroute_transit.adapters.tiles_db import (
    NullTiles,
    SqliteTileRepository,
)
from paperroute_transit.build.tile_store import PlacedLabel, TileWriter
from paperroute_transit.core.tiles import (
    TILE,
    TILE_STYLE,
    LabelKind,
    TileVariant,
)
from paperroute_transit.render.tile_codec import pack_bits, pack_grey
from tests.projection import ORIGIN


def label(*, kind: LabelKind, name: str, x: float, y: float) -> PlacedLabel:
    """
    Make a label at a point.

    Parameters
    ----------
    kind : LabelKind
        Label kind.
    name : str
        Label text.
    x : float
        Anchor x.
    y : float
        Anchor y.

    Returns
    -------
    PlacedLabel
        The label.
    """
    return PlacedLabel(
        zoom=14,
        kind=kind,
        name=name,
        x=x,
        y=y,
        angle=0.25,
        width=30.0,
        start=(x - 10, y),
        end=(x + 10, y),
    )


def grey_tile(*, level: int) -> np.ndarray:
    """
    Make a tile of one grey level with a stripe.

    Parameters
    ----------
    level : int
        Grey value.

    Returns
    -------
    np.ndarray
        The tile.
    """
    grey = np.full((TILE, TILE), level, dtype=np.uint8)
    grey[:, :40] = 0
    return grey


def build(*, out: Path, level: int = 170) -> np.ndarray:
    """
    Write a file with one tile and a few labels.

    Parameters
    ----------
    out : Path
        Destination file.
    level : int
        Grey value of the tile.

    Returns
    -------
    np.ndarray
        The tile written.
    """
    grey = grey_tile(level=level)
    with TileWriter(out=out) as writer:
        writer.add_tile(
            zoom=14,
            col=3,
            row=4,
            grey=pack_grey(grey=grey),
            bits=pack_bits(paper=grey > 100),
        )
        writer.add_labels(
            labels=[
                label(kind=LabelKind.STREET, name='In', x=500.0, y=500.0),
                label(kind=LabelKind.STREET, name='Edge', x=690.0, y=520.0),
                label(kind=LabelKind.STREET, name='Far', x=900.0, y=500.0),
                label(kind=LabelKind.STREET, name='High', x=500.0, y=440.0),
                label(kind=LabelKind.STATION, name='Stn', x=500.0, y=500.0),
            ]
        )
        writer.finish(meta={})
    return grey


def test_tile_round_trips_grey_and_bits(tmp_path: Path) -> None:
    """Both variants read back what was written."""
    out = tmp_path / 'tiles.db'
    grey = build(out=out)
    with closing(SqliteTileRepository(path=out)) as repo:
        got = repo.tile(zoom=14, col=3, row=4, variant=TileVariant.GREY)
        bits = repo.tile(zoom=14, col=3, row=4, variant=TileVariant.BITS)
    assert got is not None
    assert bits is not None
    assert np.array_equal(got, grey)
    assert np.array_equal(bits, np.where(grey > 100, 255, 0))


def test_missing_tile_is_none(tmp_path: Path) -> None:
    """A tile that was never stored is None."""
    out = tmp_path / 'tiles.db'
    build(out=out)
    with closing(SqliteTileRepository(path=out)) as repo:
        assert repo.tile(
            zoom=14, col=9, row=9, variant=TileVariant.GREY
        ) is None


def test_street_labels_in_box_only(tmp_path: Path) -> None:
    """Only streets near the box are returned, not stations."""
    out = tmp_path / 'tiles.db'
    build(out=out)
    with closing(SqliteTileRepository(path=out)) as repo:
        found = repo.street_labels(
            zoom=14, box=(400.0, 480.0, 500.0, 520.0)
        )
        other = repo.street_labels(
            zoom=5, box=(400.0, 480.0, 500.0, 520.0)
        )
    assert sorted(item.name for item in found) == ['Edge', 'In']
    assert other == []
    inside = next(item for item in found if item.name == 'In')
    assert (inside.x, inside.y, inside.angle, inside.width) == (
        500.0,
        500.0,
        0.25,
        30.0,
    )
    assert inside.start == (490.0, 500.0)
    assert inside.end == (510.0, 500.0)


def test_missing_file_answers_empty_then_opens(tmp_path: Path) -> None:
    """A repository built before the file exists picks it up later."""
    out = tmp_path / 'tiles.db'
    with closing(SqliteTileRepository(path=out)) as repo:
        assert repo.version() is None
        assert repo.street_labels(zoom=14, box=(0, 0, 1, 1)) == []
        assert repo.tile(
            zoom=14, col=3, row=4, variant=TileVariant.GREY
        ) is None
        grey = build(out=out)
        got = repo.tile(zoom=14, col=3, row=4, variant=TileVariant.GREY)
    assert got is not None
    assert np.array_equal(got, grey)


def test_reopens_after_replace(tmp_path: Path) -> None:
    """A second build over the same path changes version()."""
    out = tmp_path / 'tiles.db'
    build(out=out)
    with closing(SqliteTileRepository(path=out)) as repo:
        first = repo.version()
        build(out=out, level=85)
        second = repo.version()
        got = repo.tile(zoom=14, col=3, row=4, variant=TileVariant.GREY)
    assert first is not None
    assert second is not None
    assert first != second
    assert got is not None
    assert got[0, 100] == 85


def test_null_tiles_is_empty() -> None:
    """NullTiles answers None and nothing."""
    tiles = NullTiles()
    assert tiles.tile(
        zoom=14, col=0, row=0, variant=TileVariant.BITS
    ) is None
    assert tiles.street_labels(zoom=14, box=(0, 0, 1, 1)) == []
    assert tiles.version() is None


def test_injected_connection_is_used_and_never_reopens(
    tmp_path: Path,
) -> None:
    """A repository given a connection reads it and ignores the path."""
    out = tmp_path / 'tiles.db'
    grey = build(out=out)
    conn = sqlite3.connect(f'file:{out}?mode=ro', uri=True)
    with closing(SqliteTileRepository(connection=conn)) as repo:
        assert repo.db.identity is None
        build(out=out, level=85)
        got = repo.tile(zoom=14, col=3, row=4, variant=TileVariant.GREY)
    assert got is not None
    assert np.array_equal(got, grey)


def garbage(*, out: Path) -> None:
    """
    Put a file that is not a database at a path, as a new file.

    Parameters
    ----------
    out : Path
        Destination file.
    """
    scratch = out.with_suffix('.tmp')
    scratch.write_bytes(b'not a database ' * 100)
    scratch.replace(out)


def test_unreadable_file_answers_empty_then_opens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file that fails the check reads as missing until replaced."""
    out = tmp_path / 'tiles.db'
    garbage(out=out)
    with (
        caplog.at_level(logging.WARNING),
        closing(SqliteTileRepository(path=out)) as repo,
    ):
        assert [r.message for r in caplog.records] == ['tiles file unusable']
        assert repo.version() is None
        assert repo.street_labels(zoom=14, box=(0, 0, 1, 1)) == []
        assert repo.tile(
            zoom=14, col=3, row=4, variant=TileVariant.GREY
        ) is None
        build(out=out)
        assert repo.version() is not None


def test_unreadable_replacement_keeps_the_old_file(tmp_path: Path) -> None:
    """A good file replaced by garbage keeps answering from the old one."""
    out = tmp_path / 'tiles.db'
    build(out=out)
    with closing(SqliteTileRepository(path=out)) as repo:
        first = repo.version()
        garbage(out=out)
        assert repo.version() == first


def raw_tiles(*, out: Path, blob: bytes | None) -> None:
    """
    Write a tiles file whose one tile, at zoom 14, column 3, row 4, holds
    a given BLOB in both variants.

    Parameters
    ----------
    out : Path
        Destination file.
    blob : bytes | None
        The stored tile, or None for NULL.
    """
    with closing(sqlite3.connect(out)) as conn, conn:
        conn.execute('CREATE TABLE meta (key TEXT, value TEXT)')
        conn.execute("INSERT INTO meta VALUES ('built_at', 'corrupt')")
        conn.execute(
            'CREATE TABLE tiles (zoom INTEGER, col INTEGER, row INTEGER,'
            ' grey BLOB, bits BLOB)'
        )
        conn.execute(
            'INSERT INTO tiles VALUES (14, 3, 4, ?, ?)', (blob, blob)
        )


@pytest.mark.parametrize(
    'blob',
    [pack_grey(grey=grey_tile(level=170))[:10], None],
    ids=['truncated', 'null'],
)
@pytest.mark.parametrize('variant', list(TileVariant))
def test_corrupt_tile_is_a_database_error(
    tmp_path: Path, blob: bytes | None, variant: TileVariant
) -> None:
    """A tile that can't be decoded reads as a database error."""
    out = tmp_path / 'tiles.db'
    raw_tiles(out=out, blob=blob)
    with (
        closing(SqliteTileRepository(path=out)) as repo,
        pytest.raises(sqlite3.DatabaseError, match='tile 14/3/4'),
    ):
        repo.tile(zoom=14, col=3, row=4, variant=variant)


def with_meta(*, out: Path, meta: dict[str, str]) -> Path:
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


def test_fits_this_release(tmp_path: Path) -> None:
    """A file built for this release's zooms and style fits."""
    out = with_meta(
        out=tmp_path / 'tiles.db',
        meta={'zooms': '3,5,8,14,25,45', 'style': TILE_STYLE},
    )
    with closing(SqliteTileRepository(path=out)) as repo:
        assert repo.fits(origin=ORIGIN)


@pytest.mark.parametrize(
    ('origin', 'fits'),
    [(ORIGIN, True), (None, True), ('0.0,51.5', False)],
)
def test_fit_follows_the_origin(
    tmp_path: Path, origin: str | None, fits: bool,
) -> None:
    """A recorded origin must be the city's; a missing one is accepted."""
    meta = {'zooms': '3,5,8,14,25,45', 'style': TILE_STYLE}
    if origin is not None:
        meta['origin'] = origin
    out = with_meta(out=tmp_path / 'tiles.db', meta=meta)
    with closing(SqliteTileRepository(path=out)) as repo:
        assert repo.fits(origin=ORIGIN) is fits
        assert repo.built_for()[2] == origin


def test_other_zooms_do_not_fit(tmp_path: Path) -> None:
    """A file without the 25 m/px level doesn't fit."""
    out = with_meta(
        out=tmp_path / 'tiles.db',
        meta={'zooms': '3,5,8,14,45', 'style': TILE_STYLE},
    )
    with closing(SqliteTileRepository(path=out)) as repo:
        assert not repo.fits(origin=ORIGIN)


def test_missing_style_does_not_fit(tmp_path: Path) -> None:
    """Today's meta keys, with no style, don't fit."""
    out = with_meta(
        out=tmp_path / 'tiles.db',
        meta={
            'bbox': '140.99,-37.51,153.64,-28.15',
            'zooms': '3,5,8,14,25,45',
            'origin': ORIGIN,
            'osm_timestamp': '2026-10-01T00:00:00Z',
            'tiles': '1',
        },
    )
    with closing(SqliteTileRepository(path=out)) as repo:
        assert not repo.fits(origin=ORIGIN)


def test_missing_file_does_not_fit(tmp_path: Path) -> None:
    """No file doesn't fit."""
    with closing(SqliteTileRepository(path=tmp_path / 'no.db')) as repo:
        assert not repo.fits(origin=ORIGIN)


def test_bad_zooms_do_not_fit(tmp_path: Path) -> None:
    """Zooms that don't parse don't fit."""
    out = with_meta(
        out=tmp_path / 'tiles.db',
        meta={'zooms': '3,five', 'style': TILE_STYLE},
    )
    with closing(SqliteTileRepository(path=out)) as repo:
        assert not repo.fits(origin=ORIGIN)


def test_unreadable_meta_does_not_fit(tmp_path: Path) -> None:
    """A meta table that can't be read doesn't fit."""
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.execute('CREATE TABLE meta (k TEXT)')
        repo = SqliteTileRepository(connection=conn)
        assert not repo.fits(origin=ORIGIN)
