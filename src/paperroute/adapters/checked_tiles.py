"""A tiles file checked against this release whenever its build changes."""

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import SqliteTileRepository
from paperroute.core.tiles import (
    TILE_STYLE,
    ZOOMS,
    LabelKind,
    StreetLabel,
    TileVariant,
    levels_text,
)

logger = logging.getLogger(__name__)


def warn_unfit(
    *, path: Path, built: tuple[tuple[int, ...] | None, str | None]
) -> None:
    """
    Log that a tiles file doesn't fit this release, so the map is off.

    Parameters
    ----------
    path : Path
        The tiles file.
    built : tuple[tuple[int, ...] | None, str | None]
        The zoom levels and style it was built for.
    """
    zooms, style = built
    logger.warning(
        'tiles: %s was built for zoom levels %s and style %s; this '
        'release needs %s and %s, so the map is off',
        path,
        levels_text(zooms=zooms),
        style or 'none',
        levels_text(zooms=ZOOMS),
        TILE_STYLE,
    )


class CheckedTiles:
    """
    A tiles file that reads as empty while it doesn't fit this release.

    The file may be missing at first. Its zoom levels and style are
    checked again each time `version` sees a new build, so a file
    installed while the app runs is checked too. Reads between two
    `version` calls follow the last check. A build is marked as seen
    only once its check is done, so a caller on another thread never
    reads it with the previous build's fit.

    Parameters
    ----------
    tiles : SqliteTileRepository
        The file, which fitted or was missing when opened.
    path : Path
        Its path, for the warning.
    """

    def __init__(self, *, tiles: SqliteTileRepository, path: Path) -> None:
        self.tiles = tiles
        self.path = path
        self.seen = tiles.version()
        self.fit = True

    def version(self) -> str | None:
        """
        Say which build the file is, checking a new one.

        Returns
        -------
        str | None
            The build time; None with no file or a file that doesn't fit.
        """
        found = self.tiles.version()
        if found != self.seen:
            self.fit = found is None or self.checked()
            self.seen = found
        return found if self.fit else None

    def checked(self) -> bool:
        """
        Check the file against this release, warning when it doesn't fit.

        Returns
        -------
        bool
            True when its zoom levels and style match this release.
        """
        if self.tiles.fits():
            return True
        warn_unfit(path=self.path, built=self.tiles.built_for())
        return False

    def tile(
        self, *, zoom: int, col: int, row: int, variant: TileVariant
    ) -> NDArray[np.uint8] | None:
        """
        Read one tile.

        Parameters
        ----------
        zoom : int
            Zoom.
        col : int
            Tile column.
        row : int
            Tile row.
        variant : TileVariant
            Which rendering to read.

        Returns
        -------
        NDArray[np.uint8] | None
            The tile's pixels; None when it isn't stored or the file
            doesn't fit.
        """
        if not self.fit:
            return None
        return self.tiles.tile(zoom=zoom, col=col, row=row, variant=variant)

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Find labels near a pixel box.

        Parameters
        ----------
        zoom : int
            Zoom.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in global pixels.
        kind : LabelKind
            Which labels; street names unless asked.

        Returns
        -------
        list[StreetLabel]
            Matching labels; none when the file doesn't fit.
        """
        if not self.fit:
            return []
        return self.tiles.street_labels(zoom=zoom, box=box, kind=kind)

    def close(self) -> None:
        """Close the file."""
        self.tiles.close()
