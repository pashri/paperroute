"""Fetch BODS vehicle positions, naming coaches from SIRI-VM."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Final

import aiohttp

from paperroute_cambridge.adapters.bods_positions import named
from paperroute_cambridge.adapters.siri_vm import (
    CoachSighting,
    coach_sightings,
)
from paperroute_cambridge.cambridge_feeds import POSITIONS_FEED
from paperroute_device.adapters import secrets_file
from paperroute_device.core.device_model import Credential
from paperroute_transit.adapters.feed_errors import (
    FeedError,
    FeedThrottled,
    FeedTimedOut,
    FeedUnavailable,
)
from paperroute_transit.core.geo import DegreeBox

logger = logging.getLogger(__name__)

BASE_URL: Final[str] = 'https://data.bus-data.dft.gov.uk/api/v1/'
RT_PATH: Final[str] = 'gtfsrtdatafeed/'
SIRI_PATH: Final[str] = 'datafeed/'
COACH_OPERATORS: Final[tuple[str, ...]] = ('NATX', 'FLIX')
BODY_LIMIT: Final[int] = 4 * 1024 * 1024  # bytes
CHUNK: Final[int] = 64 * 1024  # bytes
BODS_KEY_NAME: Final[str] = 'bods'
BODS_DISPLAY_NAME: Final[str] = 'Bus data'
BODS_KEY: Final[Credential] = Credential(
    label='Bus data API key',
    refusal="That doesn't look like a bus data API key.",
    not_saved="Not saved: that doesn't look like a bus data API key.",
)


def rt_box(*, box: DegreeBox) -> str:
    """
    Write a box as the GTFS-RT endpoint takes it.

    Parameters
    ----------
    box : DegreeBox
        West, south, east, north.

    Returns
    -------
    str
        'minLon,minLat,maxLon,maxLat'.
    """
    west, south, east, north = box
    return f'{west},{south},{east},{north}'


def siri_box(*, box: DegreeBox) -> str:
    """
    Write a box as the SIRI-VM endpoint takes it.

    Parameters
    ----------
    box : DegreeBox
        West, south, east, north.

    Returns
    -------
    str
        'minLon,minLat,maxLon,maxLat'.
    """
    west, south, east, north = box
    return f'{west},{south},{east},{north}'


def key_path_from_env() -> Path:
    """
    Name the BODS key file from PAPERROUTE_KEY_FILE_BODS.

    Exits with a message when the variable is unset.

    Returns
    -------
    Path
        The key file, which may not exist yet.
    """
    return secrets_file.key_path_from_env(name=BODS_KEY_NAME)


@dataclass(frozen=True)
class BodsRequest:
    """
    What one BODS request needs.

    Attributes
    ----------
    session : aiohttp.ClientSession
        The session to send it on.
    key : str
        The BODS API key.
    box : DegreeBox
        West, south, east, north.
    base_url : str
        API root, ending in '/'.
    timeout : float
        Total time allowed, in seconds.
    """

    session: aiohttp.ClientSession
    key: str = field(repr=False)
    box: DegreeBox
    base_url: str = BASE_URL
    timeout: float = 10.0  # seconds


async def sent(
    *, request: BodsRequest, path: str, params: dict[str, str], feed: str,
) -> bytes:
    """
    Send one request and read its body.

    Parameters
    ----------
    request : BodsRequest
        The session, key and timeout.
    path : str
        Endpoint path under the base URL.
    params : dict[str, str]
        Query parameters, without the key.
    feed : str
        Feed path, for error messages.

    Returns
    -------
    bytes
        The body.

    Raises
    ------
    FeedThrottled
        On HTTP 429.
    FeedUnavailable
        On HTTP 5xx.
    FeedTimedOut
        On a timeout.
    FeedError
        On another non-200 status, a connection error or a body over the
        limit. Every one hides the request URL, which holds the key.
    """
    failure: FeedError | None = None
    try:
        async with request.session.get(
            request.base_url + path,
            params={**params, 'api_key': request.key},
            timeout=aiohttp.ClientTimeout(total=request.timeout),
            allow_redirects=False,
        ) as response:
            status = response.status
            if (response.content_length or 0) > BODY_LIMIT:
                raise FeedError(f'{feed}: body too large')
            body = bytearray()
            async for chunk in response.content.iter_chunked(CHUNK):
                body += chunk
                if len(body) > BODY_LIMIT:
                    raise FeedError(f'{feed}: body too large')
    except TimeoutError:
        failure = FeedTimedOut(f'{feed}: TimeoutError')
    except aiohttp.ClientError as error:
        failure = FeedError(f'{feed}: {type(error).__name__}')
    if failure is not None:
        raise failure
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        raise FeedThrottled(feed=feed, retry_after_s=None, detail='')
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        raise FeedUnavailable(f'{feed}: HTTP {status}')
    if status != HTTPStatus.OK:
        raise FeedError(f'{feed}: HTTP {status}')
    return bytes(body)


async def raw_positions(
    *, request: BodsRequest, feed: str = POSITIONS_FEED,
) -> bytes:
    """
    Download the GTFS-RT vehicle positions in the box.

    Parameters
    ----------
    request : BodsRequest
        The session, key, box and timeout.
    feed : str
        Feed path, for error messages.

    Returns
    -------
    bytes
        The protobuf body.

    Raises
    ------
    FeedError
        See ``sent``.
    """
    return await sent(
        request=request,
        path=RT_PATH,
        params={'boundingBox': rt_box(box=request.box)},
        feed=feed,
    )


async def raw_coaches(
    *, request: BodsRequest, operator: str, feed: str = POSITIONS_FEED,
) -> bytes:
    """
    Download one operator's SIRI-VM activities in the box.

    Parameters
    ----------
    request : BodsRequest
        The session, key, box and timeout.
    operator : str
        The operator's code.
    feed : str
        Feed path, for error messages.

    Returns
    -------
    bytes
        The XML body.

    Raises
    ------
    FeedError
        See ``sent``.
    """
    return await sent(
        request=request,
        path=SIRI_PATH,
        params={
            'operatorRef': operator,
            'boundingBox': siri_box(box=request.box),
        },
        feed=feed,
    )


def sightings_of(
    *, operator: str, result: bytes | BaseException, feed: str,
) -> tuple[CoachSighting, ...]:
    """
    Read one operator's sightings, or log why there are none.

    Parameters
    ----------
    operator : str
        The operator's code.
    result : bytes | BaseException
        The body, or what the request raised.
    feed : str
        Feed path, for the log.

    Returns
    -------
    tuple[CoachSighting, ...]
        The sightings; empty when the request or the body failed.

    Raises
    ------
    BaseException
        Anything that is not a ``FeedError``.
    """
    try:
        if isinstance(result, BaseException):
            raise result
        return coach_sightings(data=result)
    except FeedError as error:
        logger.warning(
            '%s: no coach names from %s: %s', feed, operator, error,
        )
        return ()


def joined(
    *,
    positions: bytes,
    coaches: list[bytes | BaseException],
    feed: str,
) -> bytes:
    """
    Read the coach bodies and name the coaches in the positions.

    Parameters
    ----------
    positions : bytes
        The protobuf body.
    coaches : list[bytes | BaseException]
        One result per operator, in ``COACH_OPERATORS`` order.
    feed : str
        Feed path, for the log.

    Returns
    -------
    bytes
        Protobuf body, with coach route numbers in the labels.
    """
    sightings = [
        found
        for operator, result in zip(COACH_OPERATORS, coaches)
        for found in sightings_of(
            operator=operator, result=result, feed=feed,
        )
    ]
    return named(data=positions, sightings=sightings)


class BodsFeedsClient:
    """
    BODS feed client; the API key is sent but never logged or raised.

    Parameters
    ----------
    key : Callable[[], str | None]
        Gives the BODS API key at each fetch, or None when there is none.
    box : DegreeBox
        West, south, east, north.
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
        key: Callable[[], str | None],
        box: DegreeBox,
        session: aiohttp.ClientSession | None = None,
        base_url: str = BASE_URL,
        timeout_s: float = 10.0,
    ) -> None:
        self.key = key
        self.box = box
        self.session = session
        self.owns_session = session is None
        self.base_url = base_url
        self.timeout_s = timeout_s

    async def fetch(self, *, feed: str) -> bytes:
        """
        Download the positions and name the coaches in them.

        Parameters
        ----------
        feed : str
            Must be the positions feed.

        Returns
        -------
        bytes
            Protobuf body, with coach route numbers in the labels.

        Raises
        ------
        FeedError
            For an unknown feed, with no key, or when the positions
            request fails. Nothing is sent in the first two cases. A
            failed coach request only leaves its coaches unnamed.
        """
        if feed != POSITIONS_FEED:
            raise FeedError(f'{feed}: unknown feed')
        key = self.key()
        if key is None:
            raise FeedError(f'{feed}: no API key')
        self.session = self.session or aiohttp.ClientSession()
        request = BodsRequest(
            session=self.session,
            key=key,
            box=self.box,
            base_url=self.base_url,
            timeout=self.timeout_s,
        )
        positions, *coaches = await asyncio.gather(
            raw_positions(request=request, feed=feed),
            *(
                raw_coaches(request=request, operator=operator, feed=feed)
                for operator in COACH_OPERATORS
            ),
            return_exceptions=True,
        )
        if isinstance(positions, BaseException):
            raise positions
        return await asyncio.to_thread(
            joined, positions=positions, coaches=coaches, feed=feed,
        )

    async def close(self) -> None:
        """Close the HTTP session, unless it was passed in."""
        if self.session and self.owns_session:
            await self.session.close()
            self.session = None
