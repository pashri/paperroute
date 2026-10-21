"""Fetch GTFS-realtime feeds from the TfNSW Open Data API."""

import os
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Final

import aiohttp

from paperroute.adapters.rate_limit import StartLimiter

BASE_URL: Final[str] = 'https://api.transport.nsw.gov.au/'


class FeedError(Exception):
    """A feed couldn't be fetched."""


def api_key_from_env() -> str:
    """
    Read the TfNSW key from the file named by PAPERROUTE_API_KEY_FILE.

    Exits with a message when the variable is unset.

    Returns
    -------
    str
        The key.
    """
    key_file = os.getenv('PAPERROUTE_API_KEY_FILE')
    if not key_file:
        sys.exit(
            'Set PAPERROUTE_API_KEY_FILE to the file holding the '
            'TfNSW API key.'
        )
    return Path(key_file).expanduser().read_text(encoding='utf-8').strip()


class AiohttpFeedsClient:
    """
    TfNSW feed client; the API key is sent but never logged or raised.

    Parameters
    ----------
    api_key : str
        TfNSW Open Data API key.
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
        api_key: str,
        session: aiohttp.ClientSession | None = None,
        base_url: str = BASE_URL,
        timeout_s: float = 10.0,
        limiter: StartLimiter | None = None,
    ) -> None:
        self.api_key = api_key
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
            On a non-200 status, a timeout or a connection error.
        """
        self.session = self.session or aiohttp.ClientSession()
        headers = {
            'Authorization': f'apikey {self.api_key}',
            'Accept': 'application/x-google-protobuf',
        }
        if self.limiter:
            await self.limiter.wait()
        try:
            async with self.session.get(
                self.base_url + feed, headers=headers, timeout=self.timeout
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
