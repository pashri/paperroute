"""Tests for the checked tiles file."""

from pathlib import Path
from typing import cast

from paperroute_transit.adapters.checked_tiles import CheckedTiles
from paperroute_transit.adapters.tiles_db import SqliteTileRepository


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

    def fits(self) -> bool:
        """Note the build another caller would compare with."""
        assert self.checked is not None
        self.seen_during_check.append(self.checked.seen)
        return self.fitting

    def built_for(self) -> tuple[tuple[int, ...] | None, str | None]:
        """Give the zoom levels and style the file was built for."""
        return None, None


def checked_over(*, tiles: FakeTiles) -> CheckedTiles:
    """Wrap a fake file in a `CheckedTiles`."""
    checked = CheckedTiles(
        tiles=cast(SqliteTileRepository, tiles), path=Path('tiles.db'),
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
