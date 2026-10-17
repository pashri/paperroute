"""NSW TrainLink trips are Regional and match the nswtrains live feed."""

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from paperroute.adapters.gtfs_rt import parse_trip_updates
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.feeds import feed_for
from paperroute.core.live import LiveDeparture, resolve
from paperroute.core.model import SYDNEY, Mode, Pin
from paperroute.core.realtime import RealtimeSnapshot

REAL = Path(__file__).parent / 'fixtures' / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=SYDNEY)
DAY_START = datetime(2026, 10, 16, tzinfo=SYDNEY)
CASINO: Final[str] = '24701'
CALLS: Final[str] = """
  (SELECT stop_id FROM stop_times WHERE trip_id = t.trip_id AND pickup <> 1
     ORDER BY seq LIMIT 1),
  (SELECT stop_id FROM stop_times WHERE trip_id = t.trip_id AND dropoff <> 1
     ORDER BY seq DESC LIMIT 1)
"""
COACH_PINS: Final[str] = f"""
SELECT t.trip_id, {CALLS}
FROM trips t JOIN routes r ON r.route_id = t.route_id
WHERE r.agency_id = '711' ORDER BY t.trip_id
"""
XPT_PINS: Final[str] = f"""
SELECT t.trip_id, {CALLS}
FROM trips t WHERE t.agency_id = '711' AND t.source = 'sydneytrains'
ORDER BY t.trip_id
"""


def build(*, tmp_path: Path, days: int = 1) -> Path:
    """
    Build the real slice, Sydney Trains feed first, from 2026-10-16.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    days : int
        Window length.

    Returns
    -------
    Path
        The timetable file.
    """
    db = tmp_path / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=days),
        out=db,
    )
    return db


def snapshot() -> RealtimeSnapshot:
    """
    The recorded nswtrains slice, fetched at AT.

    Returns
    -------
    RealtimeSnapshot
        Snapshot.
    """
    data = (REAL / 'v1_gtfs_realtime_nswtrains.pb').read_bytes()
    return parse_trip_updates(data=data, fetched_at=AT)


def resolved(*, db: Path, query: str) -> list[list[LiveDeparture]]:
    """
    For each trip the query names, every departure leaving with it.

    Parameters
    ----------
    db : Path
        Timetable file.
    query : str
        SQL giving trip ID, first and last calling stop.

    Returns
    -------
    list[list[LiveDeparture]]
        Per trip, the departures between its first and last stops that leave
        within two minutes of it, resolved against the recording.
    """
    with closing(sqlite3.connect(db)) as conn:
        pins = conn.execute(query).fetchall()
    live = snapshot()
    repo = SqliteTimetableRepository(path=db)
    try:
        result = []
        for trip, origin, destination in pins:
            found = repo.departures(
                pin=Pin(origin=origin, destination=destination),
                start=DAY_START,
                end=DAY_START + timedelta(days=1),
            )
            mine = [d for d in found if d.trip_id == trip]
            result.append(
                [
                    resolve(departure=d, snapshot=live, now=AT)
                    for d in found
                    if mine
                    and abs((d.departs - mine[0].departs).total_seconds())
                    <= 120
                ]
            )
        return result
    finally:
        repo.close()


def test_regional_coaches_are_live_from_nswtrains(tmp_path: Path) -> None:
    """Real slice: TrainLink coaches are Regional, poll nswtrains, go live."""
    groups = resolved(db=build(tmp_path=tmp_path), query=COACH_PINS)
    found = [item for group in groups for item in group]
    assert found
    assert all(item.departure.mode is Mode.REGIONAL for item in found)
    assert feed_for(mode=Mode.REGIONAL) == 'v1/gtfs/realtime/nswtrains'
    assert any(
        item.live and item.departs != item.departure.departs
        for item in found
    )


def test_xpt_appears_once_and_goes_live(tmp_path: Path) -> None:
    """Real slice: an XPT kept from the Sydney Trains feed shows once, live."""
    groups = resolved(db=build(tmp_path=tmp_path), query=XPT_PINS)
    assert groups
    assert all(len(group) == 1 for group in groups)
    assert all(g[0].departure.mode is Mode.REGIONAL for g in groups)
    assert any(group[0].live for group in groups)


def test_central_to_casino_xpt_listed_once(tmp_path: Path) -> None:
    """Real slice, three days: each Casino XPT shows once a day."""
    repo = SqliteTimetableRepository(path=build(tmp_path=tmp_path, days=3))
    try:
        days = [
            repo.departures(
                pin=Pin(origin='200060', destination=CASINO),
                start=DAY_START + timedelta(days=n),
                end=DAY_START + timedelta(days=n + 1),
            )
            for n in range(3)
        ]
    finally:
        repo.close()
    assert all(days)
    for found in days:
        times = [d.departs for d in found]
        assert len(times) == len(set(times))
    assert all(d.mode is Mode.REGIONAL for d in days[0])
