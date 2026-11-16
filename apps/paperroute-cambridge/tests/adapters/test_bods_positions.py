"""Tests for the BODS positions reader."""

from dataclasses import replace

import pytest

from paperroute_cambridge.adapters.bods_positions import named, parse_positions
from paperroute_cambridge.adapters.siri_vm import CoachSighting
from paperroute_cambridge.area import CAMBRIDGE_PROJECTION
from paperroute_transit.adapters.feed_errors import FeedError
from tests.adapters.feed_bodies import NOW, labels, positions_body

COACH = CoachSighting(operator='NATX', vehicle='217', line='777', recorded=990)


def test_named_labels_a_tripless_vehicle_and_clears_other_labels() -> None:
    """Named labels a tripless vehicle and clears other labels."""
    raw = positions_body(
        vehicles=(
            ('217', '', '', '217', 990),
            ('3300', 'VJ1', '57428', '3300', 990),
        ),
    )
    assert labels(named(data=raw, sightings=(COACH,))) == ['777', '']


def test_named_never_names_a_vehicle_that_has_a_trip() -> None:
    """Named never names a vehicle that has a trip."""
    raw = positions_body(vehicles=(('217', 'VJ1', '57428', '', 990),))
    assert labels(named(data=raw, sightings=(COACH,))) == ['']


def test_named_ignores_a_sighting_recorded_at_another_time() -> None:
    """Named ignores a sighting recorded at another time."""
    raw = positions_body(vehicles=(('217', '', '', '', 700),))
    assert labels(named(data=raw, sightings=(COACH,))) == ['']


def test_named_names_neither_of_two_tripless_vehicles_with_one_id() -> None:
    """Named names neither of two tripless vehicles with one id."""
    raw = positions_body(
        vehicles=(('217', '', '', '', 990), ('217', '', '', '', 700)),
    )
    assert labels(named(data=raw, sightings=(COACH,))) == ['', '']


def test_named_leaves_a_shared_vehicle_ref_unnamed() -> None:
    """Named leaves a shared vehicle ref unnamed."""
    other = replace(COACH, operator='FLIX', line='UK083')
    raw = positions_body(vehicles=(('217', '', '', '', 990),))
    assert labels(named(data=raw, sightings=(COACH, other))) == ['']


def test_named_raises_feed_error_on_a_body_that_is_not_protobuf() -> None:
    """Named raises feed error on a body that is not protobuf."""
    with pytest.raises(FeedError):
        named(data=b'\xff\xff\xff', sightings=())


def test_parse_positions_accepts_a_body_with_no_vehicle() -> None:
    """Parse positions accepts a body with no vehicle."""
    snapshot = parse_positions(
        data=positions_body(vehicles=()),
        fetched_at=NOW,
        projection=CAMBRIDGE_PROJECTION,
    )
    assert snapshot.vehicles == ()
    assert snapshot.header_time == 1_000


def test_parse_positions_keeps_trip_route_point_and_timestamp() -> None:
    """Parse positions keeps trip route point and timestamp."""
    body = positions_body(vehicles=(('3300', 'VJ1', '57428', '', 990),))
    (vehicle,) = parse_positions(
        data=body, fetched_at=NOW, projection=CAMBRIDGE_PROJECTION,
    ).vehicles
    assert (vehicle.trip_id, vehicle.route_id, vehicle.timestamp) == (
        'VJ1', '57428', 990,
    )
    assert vehicle.point == pytest.approx((0.0, 0.0), abs=1.0)


def test_parse_positions_gives_an_unnamed_vehicle_the_label_empty() -> None:
    """Parse positions gives an unnamed vehicle the label empty."""
    body = positions_body(vehicles=(('3300', 'VJ1', '57428', '', 990),))
    assert labels(body) == ['']


def test_parse_positions_raises_feed_error_on_an_empty_body() -> None:
    """Parse positions raises feed error on an empty body."""
    with pytest.raises(FeedError):
        parse_positions(
            data=b'', fetched_at=NOW, projection=CAMBRIDGE_PROJECTION,
        )


def test_parse_positions_reads_entity_by_entity_past_a_broken_tail() -> None:
    """Parse positions reads entity by entity past a broken tail."""
    body = positions_body(
        vehicles=(('3300', 'VJ1', '57428', 'L7', 990),),
    ) + b'\x12\x05\xff'
    snapshot = parse_positions(
        data=body, fetched_at=NOW, projection=CAMBRIDGE_PROJECTION,
    )
    assert [(v.trip_id, v.label) for v in snapshot.vehicles] == [('VJ1', 'L7')]
    assert snapshot.header_time == int(NOW.timestamp())


def test_parse_positions_falls_back_to_fetch_time_without_a_header_time() -> None:
    """Parse positions falls back to fetch time without a header time."""
    body = positions_body(vehicles=(('3300', 'VJ1', '57428', '', 0),), header=0)
    snapshot = parse_positions(
        data=body, fetched_at=NOW, projection=CAMBRIDGE_PROJECTION,
    )
    assert snapshot.header_time == int(NOW.timestamp())
    assert snapshot.vehicles[0].timestamp == int(NOW.timestamp())
