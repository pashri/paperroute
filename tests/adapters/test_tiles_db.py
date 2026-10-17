"""Tests for the tile repository."""

import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np

from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.build.tile_store import PlacedLabel, TileWriter
from paperroute.core.tiles import TILE, LabelKind, TileVariant
from paperroute.render.tile_codec import pack_bits, pack_grey


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


def test_unreadable_file_answers_empty_then_opens(tmp_path: Path) -> None:
    """A file that fails the check reads as missing until replaced."""
    out = tmp_path / 'tiles.db'
    garbage(out=out)
    with closing(SqliteTileRepository(path=out)) as repo:
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
