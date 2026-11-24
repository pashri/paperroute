"""Fixtures applied to every test under tests/render."""

from collections.abc import Iterator

import pytest

from paperroute_transit.render.others import NUMBERS_MEMO


@pytest.fixture(name='numbers_memo', autouse=True)
def _numbers_memo() -> Iterator[None]:
    """
    Empty the route number placement memo around each test.

    Yields
    ------
    None
        Control to the test.
    """
    NUMBERS_MEMO.clear()
    yield
    NUMBERS_MEMO.clear()
