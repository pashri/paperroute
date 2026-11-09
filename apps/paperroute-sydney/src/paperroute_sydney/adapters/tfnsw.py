"""Fetch GTFS-realtime feeds from the TfNSW Open Data API."""

import functools
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from pathlib import Path
from typing import Final

import aiohttp

from paperroute_device.adapters.rate_limit import StartLimiter
from paperroute_device.core.device_model import Credential
from paperroute_transit.adapters.feed_errors import (
    FeedError,
    FeedThrottled,
    FeedTimedOut,
    FeedUnavailable,
)

BASE_URL: Final[str] = 'https://api.transport.nsw.gov.au/'
TFNSW_STARTS_PER_S: Final[float] = 3.0  # TfNSW allows 5 a second per key
REFUSALS: Final[frozenset[int]] = frozenset(
    {HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.FORBIDDEN},
)
BODY_EXCERPT: Final[int] = 200  # characters
TFNSW_KEY: Final[Credential] = Credential(
    label='TfNSW API key',
    refusal="That doesn't look like a TfNSW API key.",
    not_saved="Not saved: that doesn't look like a TfNSW API key.",
)


class NoApiKey(Exception):
    """The key file is missing, blank or unreadable."""


def read_retry_after(*, value: str | None, now: datetime) -> float | None:
    """
    Read a Retry-After header as seconds from now.

    Parameters
    ----------
    value : str | None
        The header, in seconds or as an HTTP date; None when absent.
    now : datetime
        The current time, timezone-aware.

    Returns
    -------
    float | None
        Seconds to wait, never below 0; None when absent or unreadable.
    """
    value = (value or '').strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    return max((when - now).total_seconds(), 0.0)


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


async def refusal_detail(*, response: aiohttp.ClientResponse) -> str:
    """
    Say why TfNSW refused a request, from the response alone.

    Only the response's X-Error-Detail header and body are read; the
    request headers hold the API key and are never touched.

    Parameters
    ----------
    response : aiohttp.ClientResponse
        The response.

    Returns
    -------
    str
        The header and the start of the body for a 429 or 403, on one
        line; empty for any other status or when neither is there.
    """
    if response.status not in REFUSALS:
        return ''
    body = (await response.read()).decode('utf-8', errors='replace')
    excerpt = ' '.join(body[:BODY_EXCERPT].split())
    header = response.headers.get('X-Error-Detail')
    parts = [
        f'X-Error-Detail: {header}' if header else '',
        f'body: {excerpt}' if excerpt else '',
    ]
    return '; '.join(part for part in parts if part)


def check_status(
    *, feed: str, response: aiohttp.ClientResponse, detail: str,
) -> None:
    """
    Raise for a feed response that isn't 200.

    Parameters
    ----------
    feed : str
        Feed path.
    response : aiohttp.ClientResponse
        The response.
    detail : str
        Why the server refused the request, kept on the error.

    Raises
    ------
    FeedThrottled
        On HTTP 429, with any Retry-After.
    FeedUnavailable
        On HTTP 5xx.
    FeedError
        On any other status but 200.
    """
    if response.status == HTTPStatus.TOO_MANY_REQUESTS:
        raise FeedThrottled(
            feed=feed,
            retry_after_s=read_retry_after(
                value=response.headers.get('Retry-After'),
                now=datetime.now(tz=UTC),
            ),
            detail=detail,
        )
    if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        raise FeedUnavailable(f'{feed}: HTTP {response.status}')
    if response.status != HTTPStatus.OK:
        raise FeedError(f'{feed}: HTTP {response.status}', detail=detail)


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
        FeedThrottled
            On HTTP 429.
        FeedTimedOut
            On a timeout.
        FeedUnavailable
            On HTTP 5xx.
        FeedError
            On another non-200 status or a connection error.
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
                detail = await refusal_detail(response=response)
                check_status(feed=feed, response=response, detail=detail)
                return await response.read()
        except TimeoutError as error:
            raise FeedTimedOut(f'{feed}: TimeoutError') from error
        except aiohttp.ClientError as error:
            raise FeedError(f'{feed}: {type(error).__name__}') from error

    async def close(self) -> None:
        """Close the HTTP session, unless it was passed in."""
        if self.session and self.owns_session:
            await self.session.close()
            self.session = None


@functools.cache
def tfnsw_limiter() -> StartLimiter:
    """
    Give the one limiter every TfNSW request in this process waits on.

    The limit is per API key, so the feeds and the Trip Planner share it.

    Returns
    -------
    StartLimiter
        The process's limiter, at `TFNSW_STARTS_PER_S`.
    """
    return StartLimiter(per_second=TFNSW_STARTS_PER_S)
