"""Start and stop serving a web app on an address."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiohttp import web

logger = logging.getLogger(__name__)


class BindError(Exception):
    """The web server could not listen on its address."""


async def listen(*, runner: web.AppRunner, host: str, port: int) -> None:
    """
    Start listening, logging the address if it can't be used.

    Parameters
    ----------
    runner : web.AppRunner
        Set-up runner.
    host : str
        Address to listen on.
    port : int
        Port to listen on.

    Raises
    ------
    BindError
        If the address can't be bound.
    """
    try:
        await web.TCPSite(runner, host, port).start()
    except OSError as error:
        logger.exception('cannot serve on %s:%s', host, port)
        raise BindError(f'cannot serve on {host}:{port}') from error


@asynccontextmanager
async def serving(
    *,
    app: web.Application,
    host: str,
    port: int,
    shutdown_timeout: float = 1.0,
) -> AsyncIterator[None]:
    """
    Serve an app for the length of the block.

    Parameters
    ----------
    app : web.Application
        The app.
    host : str
        Address to listen on.
    port : int
        Port to listen on.
    shutdown_timeout : float
        Seconds open requests get to finish when the block ends.

    Yields
    ------
    None
        Once the app is listening.

    Raises
    ------
    BindError
        If the address can't be bound; the server is cleaned up first.
    """
    runner = web.AppRunner(app, shutdown_timeout=shutdown_timeout)
    try:
        await runner.setup()
        await listen(runner=runner, host=host, port=port)
        yield
    finally:
        await runner.cleanup()
