"""Tests for the tile file writer."""

import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest

from paperroute.build.tile_store import (
    TILE_FORMAT,
    PlacedLabel,
    TileWriter,
)
from paperroute.core.tiles import TILE, LabelKind
from paperroute.render.tile_codec import pack_bits, pack_grey


def write_tile(*, out: Path, meta: dict[str, str] | None = None) -> None:
    """
    Write a one-tile file.

    Parameters
    ----------
    out : Path
        Destination file.
    meta : dict[str, str] | None
        Extra meta rows.
    """
    grey = np.full((TILE, TILE), 255, dtype=np.uint8)
    with TileWriter(out=out) as writer:
        writer.add_tile(
            zoom=14,
            col=1,
            row=2,
            grey=pack_grey(grey=grey),
            bits=pack_bits(paper=grey > 0),
        )
        writer.add_labels(
            labels=[
                PlacedLabel(
                    zoom=14,
                    kind=LabelKind.STREET,
                    name='Church St',
                    x=1.0,
                    y=2.0,
                    angle=0.5,
                    width=40.0,
                    start=(0.0, 0.0),
                    end=(3.0, 4.0),
                    rank=2,
                )
            ]
        )
        writer.finish(meta=meta or {})


def test_writer_replaces_only_on_finish(tmp_path: Path) -> None:
    """Until finish() runs, the old file keeps its bytes."""
    out = tmp_path / 'tiles.db'
    out.write_bytes(b'old')
    with TileWriter(out=out) as writer:
        writer.add_labels(labels=[])
        assert out.read_bytes() == b'old'
        writer.finish(meta={})
    assert out.read_bytes() != b'old'
    assert not out.with_suffix('.new').exists()


def test_writer_failure_leaves_old_file(tmp_path: Path) -> None:
    """An exception inside the with keeps out and removes .new."""
    out = tmp_path / 'tiles.db'
    out.write_bytes(b'old')
    with pytest.raises(RuntimeError):
        with TileWriter(out=out) as writer:
            writer.add_labels(labels=[])
            raise RuntimeError('boom')
    assert out.read_bytes() == b'old'
    assert not out.with_suffix('.new').exists()


def test_writer_deletes_stale_new_file(tmp_path: Path) -> None:
    """A leftover .new from a crashed build is discarded."""
    out = tmp_path / 'tiles.db'
    out.with_suffix('.new').write_bytes(b'stale')
    write_tile(out=out)
    assert out.read_bytes()[:6] == b'SQLite'


def test_writer_meta_has_format_and_built_at(tmp_path: Path) -> None:
    """finish() adds the format and build time to the given meta."""
    out = tmp_path / 'tiles.db'
    write_tile(out=out, meta={'source': 'osm'})
    with closing(sqlite3.connect(out)) as conn:
        meta = dict(conn.execute('SELECT key, value FROM meta'))
        mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        rank = conn.execute('SELECT rank FROM labels').fetchone()[0]
    assert meta['format'] == TILE_FORMAT
    assert meta['source'] == 'osm'
    assert 'T' in meta['built_at']
    assert '.' in meta['built_at']
    assert mode == 'delete'
    assert rank == 2
