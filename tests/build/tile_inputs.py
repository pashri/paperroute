"""Write a tiny timetable of stops for tile build tests."""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.timetable import SCHEMA
from paperroute_transit.core.tiles import from_pixels

STOPS: Final[tuple[tuple[str, float, float], ...]] = (
    ('s1', 151.000, -33.800),
    ('s2', 151.004, -33.805),
)


def write_timetable(*, path: Path) -> None:
    """
    Write a timetable holding only two stops inside ``TINY_BBOX``.

    Parameters
    ----------
    path : Path
        Destination database file.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO stops VALUES (?, ?, NULL, ?, ?, '')",
            [(stop_id, stop_id, lat, lon) for stop_id, lon, lat in STOPS],
        )
        conn.commit()


def pixel_vertices(
    *, pixels: Sequence[tuple[float, float]], mpp: int,
) -> NDArray[np.float64]:
    """
    Turn global pixels of a zoom into metre vertices.

    Parameters
    ----------
    pixels : Sequence[tuple[float, float]]
        Vertices in global pixels.
    mpp : int
        Zoom the pixels are in.

    Returns
    -------
    NDArray[np.float64]
        The vertices in metres.
    """
    return np.array(
        [
            from_pixels(pixel=pixel, mpp=mpp, projection=SYDNEY_PROJECTION)
            for pixel in pixels
        ],
    )
