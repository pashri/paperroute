"""Writer for the tiles file the device reads."""

import os
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from paperroute.core.tiles import LabelKind, StreetLabel

TILE_FORMAT: Final[str] = 'z2'
LABELS_TABLE: Final[str] = (
    'CREATE TABLE labels (id INTEGER PRIMARY KEY, zoom INTEGER NOT NULL,\n'
    '  kind TEXT NOT NULL, name TEXT NOT NULL, rank INTEGER NOT NULL,\n'
    '  x REAL NOT NULL, y REAL NOT NULL, angle REAL NOT NULL,\n'
    '  width REAL NOT NULL, ax REAL NOT NULL, ay REAL NOT NULL,\n'
    '  bx REAL NOT NULL, by REAL NOT NULL);\n'
)
TILES_SCHEMA: Final[str] = (
    """
CREATE TABLE tiles (zoom INTEGER NOT NULL, col INTEGER NOT NULL,
  row INTEGER NOT NULL, grey BLOB NOT NULL, bits BLOB NOT NULL,
  PRIMARY KEY (zoom, col, row));
"""
    + LABELS_TABLE
    + """CREATE INDEX labels_place ON labels (zoom, kind, x);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
)
INSERT_LABEL: Final[str] = (
    'INSERT INTO labels (zoom, kind, name, rank, x, y, angle, width,'
    ' ax, ay, bx, by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
)


@dataclass(frozen=True)
class PlacedLabel(StreetLabel):
    """A label fitted to the map, in the zoom's global pixels.

    Position, angle, width, start and end are those of ``StreetLabel``.
    For a station or place, start and end are both the feature's own
    point.

    Attributes
    ----------
    zoom : int
        Zoom the label belongs to.
    kind : LabelKind
        Label kind.
    rank : int
        Place rank; 0 gets the largest station square.
    """

    zoom: int
    kind: LabelKind
    rank: int = 0


class TileWriter:
    """
    Builds a tiles file beside its destination and swaps it in.

    Parameters
    ----------
    out : Path
        Destination file. It is only replaced by a finished build.

    Notes
    -----
    The file uses SQLite's rollback journal, not WAL, because the device
    opens it read-only and a WAL file needs a writable shared-memory file.
    """

    def __init__(self, *, out: Path) -> None:
        self.out = out
        self.staged = out.with_suffix('.new')
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        """
        Start a fresh staged file.

        Returns
        -------
        Self
            This writer.
        """
        self.staged.unlink(missing_ok=True)
        self.conn = sqlite3.connect(self.staged)
        self.conn.executescript(TILES_SCHEMA)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """
        Discard the staged file unless finish() already moved it.

        Parameters
        ----------
        exc_type : type[BaseException] | None
            Exception type, if any.
        exc : BaseException | None
            Exception, if any.
        trace : TracebackType | None
            Traceback, if any.
        """
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.staged.unlink(missing_ok=True)

    def database(self) -> sqlite3.Connection:
        """
        Return the open staged database.

        Returns
        -------
        sqlite3.Connection
            The connection.

        Raises
        ------
        RuntimeError
            When used outside the with block.
        """
        if self.conn is None:
            raise RuntimeError('TileWriter is not open')
        return self.conn

    def add_tile(
        self, *, zoom: int, col: int, row: int, grey: bytes, bits: bytes
    ) -> None:
        """
        Store one tile in both variants.

        Parameters
        ----------
        zoom : int
            Zoom.
        col : int
            Tile column.
        row : int
            Tile row.
        grey : bytes
            Packed grey variant.
        bits : bytes
            Packed black and white variant.
        """
        self.database().execute(
            'INSERT INTO tiles (zoom, col, row, grey, bits)'
            ' VALUES (?, ?, ?, ?, ?)',
            (zoom, col, row, grey, bits),
        )

    def add_labels(self, *, labels: Iterable[PlacedLabel]) -> None:
        """
        Store placed labels.

        Parameters
        ----------
        labels : Iterable[PlacedLabel]
            Labels to add.
        """
        self.database().executemany(
            INSERT_LABEL,
            (
                (
                    item.zoom,
                    str(item.kind),
                    item.name,
                    item.rank,
                    item.x,
                    item.y,
                    item.angle,
                    item.width,
                    item.start[0],
                    item.start[1],
                    item.end[0],
                    item.end[1],
                )
                for item in labels
            ),
        )

    def finish(self, *, meta: Mapping[str, str]) -> None:
        """
        Write the meta rows, check the file and move it into place.

        Parameters
        ----------
        meta : Mapping[str, str]
            Meta rows; format and built_at are added.

        Raises
        ------
        RuntimeError
            When the integrity check fails.
        """
        conn = self.database()
        rows = {
            **meta,
            'format': TILE_FORMAT,
            'built_at': datetime.now(UTC).isoformat(timespec='microseconds'),
        }
        conn.executemany(
            'INSERT INTO meta (key, value) VALUES (?, ?)', rows.items()
        )
        conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise RuntimeError('tiles file failed its integrity check')
        conn.close()
        self.conn = None
        os.replace(self.staged, self.out)
