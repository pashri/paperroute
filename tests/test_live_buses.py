"""Tests for live bus times from a real trip-updates feed and timetable."""

from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.gtfs_rt import parse_trip_updates
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.city import SYDNEY, SYDNEY_ZONE, SydneyFeed
from paperroute_testkit.core.factories import STALE_LIMIT
from paperroute_transit.core.live import LiveDeparture, resolved
from paperroute_transit.core.model import Pin
from paperroute_transit.core.realtime import RealtimeSnapshot

REAL: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'
FEED: Final[Path] = REAL / 'v1_gtfs_realtime_buses_606.pb'
M9B_DB: Final[Path] = Path(__file__).parents[1] / 'data' / 'timetable-m9b.db'
FETCHED: Final[datetime] = datetime(
    2026, 10, 31, 9, 20, 47, tzinfo=SYDNEY_ZONE,
)


@pytest.fixture(name='_snapshot', scope='module')
def _snapshot_fixture() -> RealtimeSnapshot:
    """
    Parse the route 606 trips of a real buses feed.

    Returns
    -------
    RealtimeSnapshot
        The feed as downloaded at FETCHED.
    """
    return parse_trip_updates(data=FEED.read_bytes(), fetched_at=FETCHED)


def live_from(
    *, snapshot: RealtimeSnapshot, origin: str, destination: str
) -> tuple[LiveDeparture, ...]:
    """
    Resolve the next three hours of departures between two stops.

    Parameters
    ----------
    snapshot : RealtimeSnapshot
        The buses feed.
    origin : str
        From stop ID.
    destination : str
        Destination stop ID.

    Returns
    -------
    tuple[LiveDeparture, ...]
        The departures, with live times where the feed has them.
    """
    with closing(
        SqliteTimetableRepository(
            path=M9B_DB,
            city=SYDNEY,
        ),
    ) as timetable:
        pin = Pin(
            origin=timetable.stop(stop_id=origin),
            destination=timetable.stop(stop_id=destination),
        )
        departures = timetable.departures(
            pin=pin, start=FETCHED, end=FETCHED + timedelta(hours=3),
        )
    return resolved(
        departures=departures,
        snapshots={
            SYDNEY.feeds.trip_feed(source=SydneyFeed.BUS) or '': snapshot,
        },
        now=FETCHED,
        limit=STALE_LIMIT,
        zone=SYDNEY_ZONE,
        plan=SYDNEY.feeds,
    )


@pytest.mark.skipif(
    not M9B_DB.exists(), reason='needs data/timetable-m9b.db',
)
def test_resolved_trip_with_only_no_data_updates_is_not_live(
    _snapshot: RealtimeSnapshot,
) -> None:
    """Trips the feed lists with every stop NO_DATA keep timetable times."""
    found = live_from(
        snapshot=_snapshot, origin='G215254', destination='215020',
    )
    first, second = found[:2]
    assert [item.departure.trip_id for item in (first, second)] == [
        '2707972',
        '2704259',
    ]
    assert first.departure.trip_id in _snapshot.trips
    assert not _snapshot.trips[first.departure.trip_id].updates
    assert not any(item.live for item in found)


@pytest.mark.skipif(
    not M9B_DB.exists(), reason='needs data/timetable-m9b.db',
)
def test_resolved_started_trip_is_live(_snapshot: RealtimeSnapshot) -> None:
    """Running 606 trips with predictions count down further along."""
    found = live_from(
        snapshot=_snapshot, origin='G2153298', destination='G2153299',
    )
    assert [item.live for item in found[:4]] == [True, True, True, False]
