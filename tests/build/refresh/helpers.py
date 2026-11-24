"""Shared helpers for the refresh tests."""

import sqlite3
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute_sydney.build.tfnsw_sources import (
    SYDNEY_SOURCES,
    TfnswDownloader,
)
from paperroute_sydney.city import SYDNEY, SYDNEY_REF
from paperroute_transit.build.refresh import refresh
from paperroute_transit.build.sources import BuildParts
from paperroute_transit.build.swap import RefreshReport
from tests.build.feed_server import FeedServer
from tests.build.feeds import bus_feed

REAL: Final[Path] = Path(__file__).parents[2] / 'fixtures' / 'real'


KEY: Final[str] = 'secret-key-0123456789'


METRO: Final[str] = '/v2/gtfs/schedule/metro'


DAYS: Final[int] = 9


START: Final[date] = date(2026, 10, 16)


def metro_zip(*, tmp_path: Path, comment: bytes = b'') -> bytes:
    """
    A small feed standing in for Metro.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.
    comment : bytes
        Zip comment, to change the bytes but not the contents.

    Returns
    -------
    bytes
        The zip file.
    """
    path = bus_feed(path=tmp_path / 'metro-src.zip')
    with zipfile.ZipFile(path, 'a') as archive:
        archive.comment = comment
    return path.read_bytes()


def parts_for(*, server: FeedServer) -> BuildParts:
    """
    Sydney's build parts with a fake key, against the test server.

    Parameters
    ----------
    server : FeedServer
        Feed server.

    Returns
    -------
    BuildParts
        The parts.
    """
    return BuildParts(
        city_ref=SYDNEY_REF,
        sources=SYDNEY_SOURCES,
        downloader=TfnswDownloader(key=lambda: KEY, base_url=server.base_url),
    )


def run(
    *,
    server: FeedServer,
    data_dir: Path,
    force: bool = False,
) -> RefreshReport:
    """
    Refresh a two-day timetable against the test server.

    Parameters
    ----------
    server : FeedServer
        Feed server.
    data_dir : Path
        Data directory.
    force : bool
        Rebuild even when nothing changed.

    Returns
    -------
    RefreshReport
        The refresh's report.
    """
    return refresh(
        data_dir=data_dir,
        city=SYDNEY,
        parts=parts_for(server=server),
        days=DAYS,
        force=force,
    )


def snapshot(*, data_dir: Path) -> dict[str, bytes]:
    """
    The bytes of the timetable, the stored zips and the states.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    dict[str, bytes]
        Relative path to contents.
    """
    files = [data_dir / 'timetable.db', *(data_dir / 'sources').iterdir()]
    return {
        str(path.relative_to(data_dir)): path.read_bytes()
        for path in files
    }


def window_start(*, data_dir: Path) -> str:
    """
    The window start recorded in the live timetable.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    str
        The ISO date.
    """
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        (start,) = conn.execute(
            "SELECT value FROM meta WHERE key = 'window_start'",
        ).fetchone()
    return str(start)
