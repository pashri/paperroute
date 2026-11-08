"""Tests for GTFS-realtime parsing."""

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from google.transit import gtfs_realtime_pb2

from paperroute.adapters.gtfs_rt import (
    TripIndex,
    entity_blobs,
    parse_positions,
    parse_trip_updates,
)
from paperroute.adapters.tfnsw import FeedError
from paperroute.city import SYDNEY_PROJECTION, SYDNEY_ZONE
from paperroute_testkit.adapters.rt_factory import RtStop, RtTrip, feed

NOW = datetime(2026, 10, 16, 6, 30, tzinfo=SYDNEY_ZONE)
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


def test_parse_empty_body_is_a_feed_error() -> None:
    """An empty 200 body is a failed fetch, not an empty feed."""
    with pytest.raises(FeedError):
        parse_trip_updates(data=b'', fetched_at=NOW)


def test_parse_malformed_start_date_becomes_none() -> None:
    """A bad start_date is ignored rather than raised when trips convert."""
    data = feed(trips=[RtTrip(trip_id='a', start_date='garbage')])
    trip = parse_trip_updates(data=data, fetched_at=NOW).trips['a']
    assert trip.start_date is None


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


def vehicle_feed(
    *, vehicles: list[dict[str, Any]], timestamp: int = 1792093000
) -> bytes:
    """
    Serialise a FeedMessage of vehicle positions.

    Parameters
    ----------
    vehicles : list[dict[str, Any]]
        Keys: lat, bearing, speed, timestamp, start_date (all optional; no lat
        leaves the position unset).
    timestamp : int
        Header timestamp.

    Returns
    -------
    bytes
        Serialised message.
    """
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = '2.0'
    message.header.timestamp = timestamp
    for index, spec in enumerate(vehicles):
        body = message.entity.add(id=str(index)).vehicle
        body.trip.trip_id = f't{index}'
        body.trip.route_id = 'x_12'
        if 'start_date' in spec:
            body.trip.start_date = spec['start_date']
        if 'lat' in spec:
            body.position.latitude = spec['lat']
            body.position.longitude = 151.0
        for name in ('bearing', 'speed'):
            if name in spec:
                setattr(body.position, name, spec[name])
        if 'timestamp' in spec:
            body.timestamp = spec['timestamp']
    return bytes(message.SerializeToString())


def test_parse_positions_reads_the_bus_recording() -> None:
    """The bus recording gives every positioned entity as a vehicle."""
    data = (REAL / 'v1_gtfs_vehiclepos_buses.pb').read_bytes()
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.header_time == 1792092232
    assert len(snapshot.vehicles) == 1380
    found = [v for v in snapshot.vehicles if v.trip_id == '2662023']
    assert found[0].route_id == '2503_80'
    assert found[0].label == '80'
    assert found[0].bearing == 314.0


def test_trains_without_speed_have_no_bearing() -> None:
    """The Sydney Trains recording leaves bearing and speed unset."""
    data = (REAL / 'v2_gtfs_vehiclepos_sydneytrains.pb').read_bytes()
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.vehicles
    assert all(v.bearing is None for v in snapshot.vehicles)


def test_bearing_zero_without_speed_is_none() -> None:
    """A bearing of 0 with no speed is not a heading."""
    data = vehicle_feed(vehicles=[{'lat': -33.8, 'bearing': 0.0}])
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.vehicles[0].bearing is None


def test_bearing_unset_with_speed_is_none() -> None:
    """A speed alone gives no heading."""
    data = vehicle_feed(vehicles=[{'lat': -33.8, 'speed': 5.0}])
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.vehicles[0].bearing is None


def test_start_date_is_read() -> None:
    """The trip's start date comes through."""
    data = vehicle_feed(
        vehicles=[{'lat': -33.8, 'start_date': '20261016'}, {'lat': -33.8}]
    )
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.vehicles[0].start_date == date(2026, 10, 16)
    assert snapshot.vehicles[1].start_date is None


def test_zero_vehicle_timestamp_uses_header_time() -> None:
    """A vehicle timestamp of 0 counts as unset."""
    data = vehicle_feed(
        vehicles=[{'lat': -33.8, 'timestamp': 0}], timestamp=1000
    )
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.vehicles[0].timestamp == 1000


def test_bearing_zero_with_speed_is_north() -> None:
    """A bearing of 0 with a speed is north."""
    data = vehicle_feed(
        vehicles=[{'lat': -33.8, 'bearing': 0.0, 'speed': 5.0}]
    )
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.vehicles[0].bearing == 0.0


def test_missing_vehicle_timestamp_uses_header_time() -> None:
    """A vehicle without a timestamp takes the header's."""
    data = vehicle_feed(
        vehicles=[{'lat': -33.8}, {'lat': -33.8, 'timestamp': 42}],
        timestamp=1000,
    )
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert [v.timestamp for v in snapshot.vehicles] == [1000, 42]


def test_vehicle_without_position_is_skipped() -> None:
    """Entities with no position produce no vehicle."""
    data = vehicle_feed(vehicles=[{}, {'lat': -33.8}])
    snapshot = parse_positions(
        data=data,
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert [v.trip_id for v in snapshot.vehicles] == ['t1']


def test_entity_fallback_uses_fetch_time_as_header() -> None:
    """When whole-message decoding fails, the fetch time stands in."""
    data = vehicle_feed(vehicles=[{'lat': -33.8}, {'lat': -33.8}])
    snapshot = parse_positions(
        data=data[:-3],
        fetched_at=NOW,
        projection=SYDNEY_PROJECTION,
    )
    assert snapshot.header_time == int(NOW.timestamp())
    assert len(snapshot.vehicles) == 1
    assert snapshot.vehicles[0].timestamp == int(NOW.timestamp())
