"""Fixtures applied to every test under tests/core."""

from collections.abc import Iterator

import pytest

from paperroute_transit.core.gauge_buses import HERO_STOPS
from paperroute_transit.core.live import RESOLVED_MEMO
from paperroute_transit.core.map_vehicles import HEARD_MEMO
from paperroute_transit.core.route_trips import SPANS
from paperroute_transit.core.state_views import STOP_VIEWS
from paperroute_transit.core.strip_follow import REACHES, STEADY, common_pairs


@pytest.fixture(name='strip_merge_cache', autouse=True)
def _strip_merge_cache() -> Iterator[None]:
    """
    Empty the strip station merge cache around each test.

    Yields
    ------
    None
        Control to the test.
    """
    common_pairs.cache_clear()
    yield
    common_pairs.cache_clear()


@pytest.fixture(name='per_poll_memos', autouse=True)
def _per_poll_memos() -> Iterator[None]:
    """
    Empty the per-poll and per-frame memos around each test.

    Yields
    ------
    None
        Control to the test.
    """
    HEARD_MEMO.clear()
    RESOLVED_MEMO.clear()
    STEADY.clear()
    REACHES.clear()
    STOP_VIEWS.clear()
    yield
    HEARD_MEMO.clear()
    RESOLVED_MEMO.clear()
    STEADY.clear()
    REACHES.clear()
    STOP_VIEWS.clear()


@pytest.fixture(name='gauge_memory', autouse=True)
def _gauge_memory() -> Iterator[None]:
    """
    Empty the gauge's remembered stop mappings and trip spans.

    Yields
    ------
    None
        Control to the test.
    """
    HERO_STOPS.clear()
    SPANS.clear()
    yield
    HERO_STOPS.clear()
    SPANS.clear()
