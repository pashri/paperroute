"""Close every database connection a Cambridge test opens."""

from collections.abc import Iterator

import pytest

from tests.build.timetable_rows import OPENED


@pytest.fixture(autouse=True)
def _close_connections() -> Iterator[None]:
    """
    Close and forget each connection the test builders opened.

    Yields
    ------
    None
        Once, while the test runs.
    """
    yield
    while OPENED:
        OPENED.pop().close()
