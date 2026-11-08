"""Tests for the checked tiles file."""

import logging
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from paperroute_transit.adapters.checked_tiles import CheckedTiles
from paperroute_transit.adapters.tiles_db import Built, SqliteTileRepository
from paperroute_transit.build.tile_store import TileWriter
from paperroute_transit.core.tiles import TILE_STYLE
from tests.projection import ORIGIN


class FakeTiles:
    """A tiles file whose build changes, noting what a reader would see."""

    def __init__(self) -> None:
        self.built: str | None = None
        self.fitting = False
        self.checked: CheckedTiles | None = None
        self.seen_during_check: list[str | None] = []

    def version(self) -> str | None:
        """Give the current build."""
        return self.built

    def fits(self, *, origin: str) -> bool:
        """Note the build another caller would compare with."""
        assert origin == ORIGIN
        assert self.checked is not None
        self.seen_during_check.append(self.checked.seen)
        return self.fitting

    def built_for(self) -> Built:
        """Give the zoom levels, style and origin the file was built for."""
        return None, None, None


def checked_over(*, tiles: FakeTiles) -> CheckedTiles:
    """Wrap a fake file in a `CheckedTiles`."""
    checked = CheckedTiles(
        tiles=cast(SqliteTileRepository, tiles),
        path=Path('tiles.db'),
        origin=ORIGIN,
    )
    tiles.checked = checked
    return checked


def test_version_hides_a_new_build_until_it_is_checked() -> None:
    """A concurrent caller never sees the new build with the old fit."""
    tiles = FakeTiles()
    checked = checked_over(tiles=tiles)
    tiles.built = 'new'
    assert checked.version() is None
    assert tiles.seen_during_check == [None]
    assert checked.seen == 'new'
    assert not checked.fit


def test_version_reports_a_fitting_build() -> None:
    """A new build that fits is reported."""
    tiles = FakeTiles()
    tiles.fitting = True
    checked = checked_over(tiles=tiles)
    tiles.built = 'new'
    assert checked.version() == 'new'


def tiles_from(*, path: Path, origin: str | None) -> Path:
    """
    Write a tiles file for today's zooms and style, from an origin.

    Parameters
    ----------
    path : Path
        Destination file.
    origin : str | None
        The origin meta; None to leave it out.

    Returns
    -------
    Path
        The file.
    """
    meta = {'zooms': '3,5,8,14,25,45', 'style': TILE_STYLE}
    if origin is not None:
        meta['origin'] = origin
    with TileWriter(out=path) as writer:
        writer.finish(meta=meta)
    return path


@pytest.mark.parametrize(
    ('origin', 'fits'),
    [('0.0,51.5', False), (ORIGIN, True), (None, True)],
)
def test_other_origin_refused(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    origin: str | None,
    fits: bool,
) -> None:
    """A file from another origin is refused, with one warning."""
    path = tmp_path / 'tiles.db'
    checked = CheckedTiles(
        tiles=SqliteTileRepository(path=path),
        path=path,
        origin=ORIGIN,
    )
    with closing(checked), caplog.at_level(logging.WARNING):
        tiles_from(path=path, origin=origin)
        found = checked.version()
    assert (found is not None) is fits
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    if fits:
        assert not warnings
    else:
        (warning,) = warnings
        assert '0.0,51.5' in warning
        assert ORIGIN in warning
