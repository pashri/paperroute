"""Shared helpers for the rail build tests."""

import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from paperroute_cambridge.build.__main__ import CAMBRIDGE_BUILD
from paperroute_cambridge.build.bods_sources import CAMBRIDGE_SOURCES
from paperroute_cambridge.build.rail_cut import cut_rail
from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_REF, CAMBRIDGE_ZONE
from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build import swap
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.refresh import refresh
from paperroute_transit.build.source_state import SourceError, SourceState
from paperroute_transit.build.sources import SourceSpec, file_sha256
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.model import Pin, StopPlace
from tests.build.test_rail_cut import XC_TRIP
from tests.conftest import FIXTURES

BODS: Final[Path] = FIXTURES / 'bods.zip'


RAIL: Final[Path] = FIXTURES / 'rail.zip'


STATION: Final[StopPlace] = StopPlace(
    stop_id='rail:CBG', name='Cambridge', point=None,
)


BIRMINGHAM: Final[StopPlace] = StopPlace(
    stop_id='rail:BHM', name='Birmingham New Street', point=None,
)


HOSPITAL: Final[StopPlace] = StopPlace(
    stop_id='0500CCITY517', name='Hospital Outpatients', point=None,
)


CITY_CENTRE: Final[StopPlace] = StopPlace(
    stop_id='0500CCITY001', name='City centre', point=None,
)


XC_BUILT: Final[str] = f'rail:{XC_TRIP}'


SUNDAY: Final[date] = date(2026, 11, 29)


@dataclass
class Shelf:
    """Hand out timetable zips from local files, as the network would."""

    files: dict[str, Path]
    gone: set[str] = field(default_factory=set)

    def head(
        self,
        *,
        spec: SourceSpec,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> SourceState:
        """
        Give a source's validators, its file's digest as the ETag.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        timeout_s : float
            Ignored.
        limiter : SyncStartLimiter | None
            Ignored.

        Returns
        -------
        SourceState
            The validators.

        Raises
        ------
        SourceError
            For a source that is gone.
        """
        del timeout_s, limiter
        if spec.name in self.gone:
            raise SourceError(f'{spec.name}: 404')
        etag = f'"{file_sha256(path=self.files[spec.name])}"'
        return SourceState(etag=etag, last_modified=None, sha256=None)

    def download(
        self,
        *,
        spec: SourceSpec,
        out: Path,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> str:
        """
        Copy a source's file.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        out : Path
            File to write.
        timeout_s : float
            Ignored.
        limiter : SyncStartLimiter | None
            Ignored.

        Returns
        -------
        str
            The copy's SHA-256.

        Raises
        ------
        SourceError
            For a source that is gone.
        """
        del timeout_s, limiter
        if spec.name in self.gone:
            raise SourceError(f'{spec.name}: 404')
        shutil.copyfile(self.files[spec.name], out)
        return file_sha256(path=out)


def built(
    *, out: Path, start: date, days: int, rail: Path = RAIL,
) -> Path:
    """
    Build Cambridge's timetable from the bus fixture and the rail cut.

    Parameters
    ----------
    out : Path
        Timetable file.
    start : date
        First day.
    days : int
        Days covered.
    rail : Path
        National rail timetable to cut.

    Returns
    -------
    Path
        The timetable file.
    """
    cut = out.with_name('rail-cut.zip')
    cut_rail(archive=rail, out=cut)
    bods, trains = CAMBRIDGE_SOURCES
    build_timetable(
        sources=[
            Source(name=bods.name, archive=BODS),
            Source(name=trains.name, archive=cut, optional=trains.optional),
        ],
        window=Window(start=start, days=days),
        out=out,
        city_ref=CAMBRIDGE_REF,
    )
    return out


def departures(
    *, path: Path, day: date, place: StopPlace = STATION,
) -> list[tuple[str, ...]]:
    """
    Read a day's departures from a station.

    Parameters
    ----------
    path : Path
        Timetable file.
    day : date
        Service day.
    place : StopPlace
        Station to read.

    Returns
    -------
    list[tuple[str, ...]]
        Time, badge, platform, headsign, source and kind of each.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=CAMBRIDGE_ZONE)
    pin = Pin(origin=place, kind='train')
    with closing(SqliteTimetableRepository(city=CAMBRIDGE, path=path)) as repo:
        found = repo.departures(
            pin=pin, start=start, end=start + timedelta(days=1),
        )
    return [
        (
            dep.departs.astimezone(CAMBRIDGE_ZONE).strftime('%H:%M'),
            dep.route, dep.platform, dep.headsign, dep.source,
            str(dep.facts.kind),
        )
        for dep in found
    ]


def trip_counts(*, data_dir: Path) -> dict[str, int]:
    """
    Count the live timetable's trips by source.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    dict[str, int]
        Trips by source name.
    """
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        rows = conn.execute(
            'SELECT source, COUNT(*) FROM trips GROUP BY source',
        ).fetchall()
    return {str(source): int(count) for source, count in rows}


def night(
    *, data_dir: Path, shelf: Shelf, hand_over: bool = False,
) -> swap.RefreshReport:
    """
    Refresh Cambridge's one-day timetable from the shelf.

    Parameters
    ----------
    data_dir : Path
        Data directory.
    shelf : Shelf
        Where the sources come from.
    hand_over : bool
        Exec into the extras program after the load.

    Returns
    -------
    swap.RefreshReport
        The refresh's report.
    """
    return refresh(
        data_dir=data_dir,
        city=CAMBRIDGE,
        parts=replace(CAMBRIDGE_BUILD, downloader=shelf),
        days=1,
        force=True,
        hand_over=hand_over,
    )


def rail_date(*, data_dir: Path) -> date | None:
    """
    Read the date of the rail source in the live timetable.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    date | None
        The rail source's date, or None when the timetable has none.
    """
    path = data_dir / 'timetable.db'
    with closing(SqliteTimetableRepository(city=CAMBRIDGE, path=path)) as repo:
        return repo.source_dates().get('rail')
