"""Fixtures applied to every test under tests/core."""

from collections.abc import Iterator

import pytest

from paperroute_transit.core.strip_follow import common_pairs


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
