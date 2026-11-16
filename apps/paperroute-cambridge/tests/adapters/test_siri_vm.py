"""Tests for the SIRI-VM coach sightings reader."""

import pytest

from paperroute_cambridge.adapters.siri_vm import (
    CoachSighting,
    coach_sightings,
)
from paperroute_transit.adapters.feed_errors import FeedError
from tests.adapters.feed_bodies import AT_990, siri_body


def test_coach_sightings_read_operator_vehicle_line_and_time() -> None:
    """Coach sightings read operator vehicle line and time."""
    data = siri_body(
        activities=(
            ('NATX', '217', '777', '777', AT_990),
            ('FLIX', '9', 'UK083', '', '1970-01-01T01:16:40+01:00'),
        ),
    )
    assert coach_sightings(data=data) == (
        CoachSighting(operator='NATX', vehicle='217', line='777', recorded=990),
        CoachSighting(
            operator='FLIX', vehicle='9', line='UK083', recorded=1_000,
        ),
    )


@pytest.mark.parametrize(
    'activity',
    [
        ('NATX', '', '777', '777', AT_990),
        ('NATX', '217', '', '', AT_990),
        ('NATX', '217', '777', '777', 'yesterday'),
        ('NATX', '217', '777', '777', ''),
        ('NATX', '217', '777', '777', '1970-01-01T00:16:30'),
    ],
)
def test_coach_sightings_skip_an_incomplete_activity(
    activity: tuple[str, str, str, str, str],
) -> None:
    """Coach sightings skip an incomplete activity."""
    assert coach_sightings(data=siri_body(activities=(activity,))) == ()


def test_coach_sightings_raise_feed_error_on_bad_xml() -> None:
    """Coach sightings raise feed error on bad xml."""
    with pytest.raises(FeedError):
        coach_sightings(data=b'<Siri><unclosed>')
