"""Tile repository over the built tiles file."""

import sqlite3
import zlib
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.sqlite_ro import ReadOnlyDatabase
from paperroute.core.tiles import (
    TILE_STYLE,
    ZOOMS,
    LabelKind,
    StreetLabel,
    TileVariant,
    zooms_of,
)
from paperroute.render.tile_codec import unpack_bits, unpack_grey

DEFAULT_TILES: Final[Path] = Path('/var/lib/paperroute/tiles.db')
GROW_X: Final[float] = 200.0
GROW_Y: Final[float] = 30.0
STREETS: Final[str] = """
SELECT name, x, y, angle, width, ax, ay, bx, by FROM labels
WHERE zoom = ? AND kind = ? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?
"""


class SqliteTileRepository:
    """
    Read-only access to the tiles file.

    Parameters
    ----------
    path : Path | None
        Tiles file; defaults to the device location. It may not exist yet.
    connection : sqlite3.Connection | None
        An open connection to use instead of opening path.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db = ReadOnlyDatabase(
            path=path or DEFAULT_TILES,
            connection=connection,
            probe='SELECT 1 FROM meta LIMIT 1',
            allow_missing=True,
            label='tiles',
        )

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
            The tile's pixels, or None when it isn't stored.

        Raises
        ------
        sqlite3.DatabaseError
            When the stored tile can't be decoded.
        """
        column = 'grey' if variant == TileVariant.GREY else 'bits'
        rows = self.db.fetch(
            sql=(
                f'SELECT {column} FROM tiles'
                ' WHERE zoom = ? AND col = ? AND row = ?'
            ),
            params=(zoom, col, row),
        )
        if not rows:
            return None
        try:
            return decode(value=rows[0][0], variant=variant)
        except (TypeError, ValueError, zlib.error) as error:
            message = f'tile {zoom}/{col}/{row} is corrupt: {error}'
            raise sqlite3.DatabaseError(message) from error

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Find street labels near a pixel box.

        Parameters
        ----------
        zoom : int
            Zoom.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in global pixels. Labels anchored
            up to 200 px beyond the sides and 30 px beyond the top and
            bottom are returned.
        kind : LabelKind
            Which labels; street names unless asked.

        Returns
        -------
        list[StreetLabel]
            Matching street labels.
        """
        left, top, right, bottom = box
        rows = self.db.fetch(
            sql=STREETS,
            params=(
                zoom,
                str(kind),
                left - GROW_X,
                right + GROW_X,
                top - GROW_Y,
                bottom + GROW_Y,
            ),
        )
        return [to_label(row=row) for row in rows]

    def version(self) -> str | None:
        """
        Say which build the file is.

        Returns
        -------
        str | None
            The build time, or None when there is no file.
        """
        rows = self.db.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('built_at',),
        )
        return str(rows[0][0]) if rows else None

    def built_for(self) -> tuple[tuple[int, ...] | None, str | None]:
        """
        Say which zoom levels and map style the file was built for.

        Returns
        -------
        tuple[tuple[int, ...] | None, str | None]
            The zoom levels and the style; each is None when the file,
            the key or a readable value is missing.
        """
        try:
            rows = self.db.fetch(
                sql="SELECT key, value FROM meta WHERE key IN (?, ?)",
                params=('zooms', 'style'),
            )
        except sqlite3.Error:
            return (None, None)
        values = {str(key): str(value) for key, value in rows}
        return (zooms_of(text=values.get('zooms')), values.get('style'))

    def fits(self) -> bool:
        """
        Say whether the file was built for this release's map.

        Returns
        -------
        bool
            True when its zoom levels and style match this release.
        """
        return self.built_for() == (ZOOMS, TILE_STYLE)

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()


def decode(*, value: object, variant: TileVariant) -> NDArray[np.uint8]:
    """
    Unpack a stored tile.

    Parameters
    ----------
    value : object
        The stored BLOB.
    variant : TileVariant
        Which rendering it is.

    Returns
    -------
    NDArray[np.uint8]
        The tile's pixels.

    Raises
    ------
    TypeError
        When the value is not bytes, such as NULL.
    ValueError
        When the tile holds the wrong number of pixels.
    zlib.error
        When the tile is not a whole zlib stream.
    """
    blob = bytes(value)  # type: ignore[call-overload]
    if variant == TileVariant.GREY:
        return unpack_grey(blob=blob)
    return unpack_bits(blob=blob)


def to_label(*, row: tuple[object, ...]) -> StreetLabel:
    """
    Turn a STREETS row into a StreetLabel.

    Parameters
    ----------
    row : tuple[object, ...]
        STREETS row.

    Returns
    -------
    StreetLabel
        The label.
    """
    name, x, y, angle, width, ax, ay, bx, by = row
    return StreetLabel(
        name=str(name),
        x=float(str(x)),
        y=float(str(y)),
        angle=float(str(angle)),
        width=float(str(width)),
        start=(float(str(ax)), float(str(ay))),
        end=(float(str(bx)), float(str(by))),
    )


class NullTiles:
    """A tile repository with nothing in it."""

    # pylint: disable=unused-argument

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
            Always None.
        """
        return None

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Find street labels near a pixel box.

        Parameters
        ----------
        zoom : int
            Zoom.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in global pixels.
        kind : LabelKind
            Ignored.

        Returns
        -------
        list[StreetLabel]
            Always empty.
        """
        return []

    def version(self) -> str | None:
        """
        Say which build the file is.

        Returns
        -------
        str | None
            Always None.
        """
        return None
