"""Tests for GTFS-realtime parsing."""

from datetime import date, datetime
from pathlib import Path

import pytest

from paperroute.adapters.gtfs_rt import (
    TripIndex,
    entity_blobs,
    parse_trip_updates,
)
from paperroute.adapters.tfnsw import FeedError
from paperroute.core.model import SYDNEY
from tests.adapters.rt_factory import RtStop, RtTrip, feed

NOW = datetime(2026, 10, 16, 6, 30, tzinfo=SYDNEY)
REAL = Path(__file__).parent.parent / 'fixtures' / 'real'


def test_parse_delays_and_cancellation() -> None:
    """Delays and cancellations are read per trip."""
    data = feed(
        trips=[
            RtTrip(
                trip_id='a',
                stops=(RtStop(seq=3, stop_id='x', dep_delay=120),),
            ),
            RtTrip(trip_id='b', cancelled=True),
        ]
    )
    snapshot = parse_trip_updates(data=data, fetched_at=NOW)
    assert snapshot.fetched_at == NOW
    assert snapshot.trips['a'].updates[0].departure_delay == 120
    assert snapshot.trips['a'].updates[0].stop_id == 'x'
    assert snapshot.trips['b'].cancelled


def test_parse_skips_garbage() -> None:
    """A corrupt entity fails the whole decode; the fallback keeps the rest."""
    good = feed(
        trips=[RtTrip(trip_id='a', stops=(RtStop(seq=1, arr_delay=60),))]
    )
    data = good + b'\x12\x03\xff\xff\xff'
    snapshot = parse_trip_updates(data=data, fetched_at=NOW)
    assert list(snapshot.trips) == ['a']


def test_parse_truncated_data_does_not_raise() -> None:
    """A cut-off download gives whatever entities were complete."""
    data = feed(trips=[RtTrip(trip_id='a'), RtTrip(trip_id='b')])
    snapshot = parse_trip_updates(data=data[:-3], fetched_at=NOW)
    assert 'a' in snapshot.trips


def test_entity_blobs_counts_entities() -> None:
    """Each entity comes out as its own byte string."""
    data = feed(trips=[RtTrip(trip_id='a'), RtTrip(trip_id='b')])
    assert len(list(entity_blobs(data=data))) == 2


def test_parse_real_bus_slice() -> None:
    """The recorded bus slice parses into trip updates."""
    data = (REAL / 'v1_gtfs_realtime_buses.pb').read_bytes()
    assert parse_trip_updates(data=data, fetched_at=NOW).trips


def test_lookup_converts_only_the_requested_trip() -> None:
    """Trips stay raw until looked up; membership checks don't convert."""
    data = feed(
        trips=[
            RtTrip(trip_id='a', stops=(RtStop(seq=1, dep_delay=30),)),
            RtTrip(trip_id='b', stops=(RtStop(seq=1, dep_delay=90),)),
        ]
    )
    trips = parse_trip_updates(data=data, fetched_at=NOW).trips
    assert isinstance(trips, TripIndex)
    assert 'b' in trips
    assert len(trips) == 2
    assert list(trips) == ['a', 'b']
    assert trips['a'].updates[0].departure_delay == 30
    assert list(trips.converted) == ['a']


def test_parse_drops_no_data_stop_updates() -> None:
    """Stop updates marked NO_DATA carry no live information."""
    data = feed(
        trips=[
            RtTrip(
                trip_id='a',
                stops=(
                    RtStop(seq=1, dep_delay=0, no_data=True),
                    RtStop(seq=2, arr_delay=60),
                ),
            )
        ]
    )
    updates = parse_trip_updates(data=data, fetched_at=NOW).trips['a'].updates
    assert [update.sequence for update in updates] == [2]


def test_parse_raises_when_nothing_decodes() -> None:
    """A non-empty body with no readable entity is a failed fetch."""
    with pytest.raises(FeedError):
        parse_trip_updates(data=b'<html>rate limited</html>', fetched_at=NOW)


def test_parse_empty_body_is_empty_snapshot() -> None:
    """An empty body is a valid feed with no trips."""
    assert not parse_trip_updates(data=b'', fetched_at=NOW).trips


def test_parse_reads_start_date() -> None:
    """The run's service date comes from the trip descriptor."""
    data = feed(trips=[RtTrip(trip_id='a', start_date='20261016')])
    trips = parse_trip_updates(data=data, fetched_at=NOW).trips
    assert trips['a'].start_date == date(2026, 10, 16)


def test_parse_raises_when_header_missing() -> None:
    """A body that decodes but lacks the required header is not a feed."""
    with pytest.raises(FeedError):
        parse_trip_updates(data=b'\x08\x01', fetched_at=NOW)


def test_parse_real_nswtrains_slice() -> None:
    """The recorded NSW TrainLink slice parses into trip updates."""
    data = (REAL / 'v1_gtfs_realtime_nswtrains.pb').read_bytes()
    assert parse_trip_updates(data=data, fetched_at=NOW).trips
