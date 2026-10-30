"""Tests for shortening stop names."""

import pytest

from paperroute.core.stop_names import short_name


@pytest.mark.parametrize(
    ('name', 'short'),
    [
        ('Hollis St Opp Constitution Hill Library', 'Hollis St'),
        ('Old Hume Hwy at Camden Valley Way', 'Old Hume Hwy'),
        ('Campbelltown Station, Platform 2', 'Campbelltown'),
        ('Town Hall Stn', 'Town Hall'),
        ('Parramatta', 'Parramatta'),
    ],
)
def test_short_name_drops_the_qualifier(name: str, short: str) -> None:
    """Qualifiers after the place's own name are dropped."""
    assert short_name(name=name) == short


def test_short_name_never_empties() -> None:
    """A drop that would leave nothing is skipped."""
    assert short_name(name='Station') == 'Station'
    assert short_name(name=', Stand A') == ', Stand A'
