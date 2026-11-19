"""Tests for Sydney's timetable sources."""

from paperroute_sydney.build.tfnsw_sources import SYDNEY_SOURCES


def test_each_source_declares_a_size_limit() -> None:
    """The bundle may reach 1 GB and each small zip 100 MB."""
    assert {spec.name: spec.size_limit for spec in SYDNEY_SOURCES} == {
        'sydneytrains': 100_000_000,
        'metro': 100_000_000,
        'bundle': 1_000_000_000,
    }
