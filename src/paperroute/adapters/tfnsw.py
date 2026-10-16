"""Fetch GTFS-realtime feeds from the TfNSW Open Data API."""

from http import HTTPStatus
from typing import Final

import aiohttp

BASE_URL: Final[str] = 'https://api.transport.nsw.gov.au/'


class FeedError(Exception):
    """A feed couldn't be fetched."""


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
        Total time allowed per request, in seconds.
    """

    def __init__(
        self,
        *,
        api_key: str,
        session: aiohttp.ClientSession | None = None,
        base_url: str = BASE_URL,
        timeout_s: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.session = session
        self.owns_session = session is None
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)

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
