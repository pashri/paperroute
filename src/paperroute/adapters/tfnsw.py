"""Fetch GTFS-realtime feeds from the TfNSW Open Data API."""

import os
import sys
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Final

import aiohttp

from paperroute.adapters.rate_limit import StartLimiter

BASE_URL: Final[str] = 'https://api.transport.nsw.gov.au/'


class FeedError(Exception):
    """A feed couldn't be fetched."""


class NoApiKey(Exception):
    """The key file is missing, blank or unreadable."""


def key_path_from_env() -> Path:
    """
    Name the key file from PAPERROUTE_API_KEY_FILE.

    Exits with a message when the variable is unset.

    Returns
    -------
    Path
        The key file, which may not exist yet.
    """
    key_file = os.getenv('PAPERROUTE_API_KEY_FILE')
    if not key_file:
        sys.exit(
            'Set PAPERROUTE_API_KEY_FILE to the file holding the '
            'TfNSW API key.'
        )
    return Path(key_file).expanduser()


def api_key_from_env() -> str:
    """
    Read the TfNSW key from the file named by PAPERROUTE_API_KEY_FILE.

    Exits with a message when the variable is unset.

    Returns
    -------
    str
        The key.

    Raises
    ------
    NoApiKey
        When the file is missing, blank or unreadable. The message names
        the file, never its contents.
    """
    path = key_path_from_env()
    try:
        key = path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError) as error:
        raise NoApiKey(f'No TfNSW API key in {path}') from error
    if not key:
        raise NoApiKey(f'No TfNSW API key in {path}')
    return key


class AiohttpFeedsClient:
    """
    TfNSW feed client; the API key is sent but never logged or raised.

    Parameters
    ----------
    key : Callable[[], str | None]
        Gives the TfNSW Open Data API key at each fetch, or None when
        there is none.
    session : aiohttp.ClientSession | None
        Session to use; one is made on first fetch otherwise.
    base_url : str
        API root, ending in '/'.
    timeout_s : float
        Total time allowed per request, in seconds. Time spent waiting
        for the limiter does not count.
    limiter : StartLimiter | None
        Spaces out request starts; none means no limit.
    """

    def __init__(
        self,
        *,
        key: Callable[[], str | None],
        session: aiohttp.ClientSession | None = None,
        base_url: str = BASE_URL,
        timeout_s: float = 10.0,
        limiter: StartLimiter | None = None,
    ) -> None:
        self.key = key
        self.session = session
        self.owns_session = session is None
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.limiter = limiter

    async def fetch(self, *, feed: str) -> bytes:
        """
        Download one feed.

        Parameters
        ----------
        feed : str
            Feed path, for example 'v1/gtfs/realtime/buses'.

        Returns
        -------
        bytes
            Protobuf body.

        Raises
        ------
        FeedError
            With no key, on a non-200 status, a timeout or a connection
            error. With no key nothing is sent.
        """
        key = self.key()
        if key is None:
            raise FeedError(f'{feed}: no API key')
        return await self.downloaded(feed=feed, key=key)

    async def downloaded(self, *, feed: str, key: str) -> bytes:
        """
        Download one feed with a key, waiting for the rate limit.

        Parameters
        ----------
        feed : str
            Feed path.
        key : str
            The API key.

        Returns
        -------
        bytes
            Protobuf body.

        Raises
        ------
        FeedError
            On a non-200 status, a timeout or a connection error.
        """
        self.session = self.session or aiohttp.ClientSession()
        if self.limiter:
            await self.limiter.wait()
        try:
            async with self.session.get(
                self.base_url + feed,
                headers={
                    'Authorization': f'apikey {key}',
                    'Accept': 'application/x-google-protobuf',
                },
                timeout=self.timeout,
            ) as response:
                if response.status != HTTPStatus.OK:
                    raise FeedError(f'{feed}: HTTP {response.status}')
                return await response.read()
        except (aiohttp.ClientError, TimeoutError) as error:
            raise FeedError(f'{feed}: {type(error).__name__}') from error

    async def close(self) -> None:
        """Close the HTTP session, unless it was passed in."""
        if self.session and self.owns_session:
            await self.session.close()
            self.session = None
