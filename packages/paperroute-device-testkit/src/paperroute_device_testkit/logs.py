"""Put logging back after a test that configures it."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def kept_logging() -> Iterator[None]:
    """
    Restore the root and access loggers when the block ends.

    Handlers added to the root logger inside the block are closed.

    Yields
    ------
    None
        Control to the block.
    """
    root = logging.getLogger()
    access = logging.getLogger('aiohttp.access')
    handlers, level = list(root.handlers), root.level
    access_level = access.level
    try:
        yield
    finally:
        access.setLevel(access_level)
        for handler in list(root.handlers):
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)
