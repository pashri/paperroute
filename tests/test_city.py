"""Tests for Sydney's city values."""

from paperroute.city import SYDNEY


def test_sydney_city_values() -> None:
    """Sydney runs on Sydney time and covers today's NSW box."""
    assert SYDNEY.zone.key == 'Australia/Sydney'
    assert SYDNEY.bbox == (140.99, -37.51, 153.64, -28.15)
