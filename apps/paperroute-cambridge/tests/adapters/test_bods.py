"""Tests for the BODS feeds client, against a real local server."""

import asyncio
import traceback
from collections.abc import AsyncIterator, Callable
from http import HTTPStatus
from pathlib import Path
from typing import Final, cast

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from paperroute_cambridge.__main__ import bods_feeds
from paperroute_cambridge.adapters.bods import (
    BODY_LIMIT,
    BodsFeedsClient,
    BodsRequest,
    key_path_from_env,
    rt_box,
    siri_box,
)
from paperroute_cambridge.area import CAMBRIDGE_BBOX
from paperroute_transit.adapters.feed_errors import (
    FeedError,
    FeedThrottled,
    FeedTimedOut,
    FeedUnavailable,
)
from tests.adapters.feed_bodies import (
    AT_990,
    labels,
    positions_body,
    siri_body,
)

KEY: Final[str] = 'k-secret'
RT_BOX: Final[str] = '-0.482,52.005,0.499,52.471'
SIRI_BOX: Final[str] = '-0.482,52.005,0.499,52.471'
ONE_COACH: Final[bytes] = positions_body(
    vehicles=(('217', '', '', '', 990), ('3300', 'VJ1', '57428', '', 990)),
)
NATX_217: Final[bytes] = siri_body(
    activities=(('NATX', '217', '777', '777', AT_990),),
)
NO_COACH: Final[bytes] = siri_body(activities=())
CHUNK_SIZE: Final[int] = 64 * 1024  # bytes
Answer = tuple[int, bytes]


class BodsStub:
    """
    A local BODS server and the clients made on it.

    Attributes
    ----------
    rt : Answer
        Status and body of the positions path.
    siri : dict[str, Answer]
        Status and body of the SIRI path, by operator.
    seen : list[tuple[str, dict[str, str]]]
        Path and query of each request.
    delay : float
        Seconds each answer waits.
    declared : int
        Content-Length the positions answer claims, when not 0.
    chunked : int
        Bytes the positions answer streams without a Content-Length.
    redirect : bool
        Whether the positions path answers 301 to '/elsewhere/'.
    """

    def __init__(self) -> None:
        self.rt: Answer = (HTTPStatus.OK, b'')
        self.siri: dict[str, Answer] = {}
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.delay = 0.0
        self.declared = 0
        self.chunked = 0
        self.redirect = False
        self.server = TestServer(self.app(), skip_url_asserts=True)
        self.clients: list[BodsFeedsClient] = []

    def app(self) -> web.Application:
        """
        Build the application, serving both paths.

        Returns
        -------
        web.Application
            The application.
        """
        app = web.Application()
        app.router.add_get('/gtfsrtdatafeed/', self.answer)
        app.router.add_get('/datafeed/', self.answer)
        app.router.add_get('/elsewhere/', self.answer)
        return app

    async def answer(self, request: web.Request) -> web.StreamResponse:
        """
        Record a request and give its configured answer.

        Parameters
        ----------
        request : web.Request
            The request.

        Returns
        -------
        web.Response
            The answer.
        """
        self.seen.append((request.path, dict(request.query)))
        await asyncio.sleep(self.delay)
        if self.redirect and request.path == '/gtfsrtdatafeed/':
            raise web.HTTPMovedPermanently(location='/elsewhere/')
        if self.chunked and request.path == '/gtfsrtdatafeed/':
            response = web.StreamResponse()
            response.enable_chunked_encoding()
            await response.prepare(request)
            for _ in range(self.chunked // CHUNK_SIZE):
                await response.write(b'\x00' * CHUNK_SIZE)
            await response.write_eof()
            return response
        if self.declared and request.path == '/gtfsrtdatafeed/':
            response = web.StreamResponse()
            response.content_length = self.declared
            await response.prepare(request)
            return response
        if request.path == '/gtfsrtdatafeed/':
            status, body = self.rt
        else:
            status, body = self.siri.get(
                request.query['operatorRef'], (HTTPStatus.OK, NO_COACH),
            )
        return web.Response(status=status, body=body)

    def client(
        self,
        *,
        key: Callable[[], str | None],
        session: aiohttp.ClientSession | None = None,
        base_url: str | None = None,
        timeout_s: float = 10.0,
    ) -> BodsFeedsClient:
        """
        Make a client on the server, to be closed at the test's end.

        Parameters
        ----------
        key : Callable[[], str | None]
            The key.
        session : aiohttp.ClientSession | None
            An injected session.
        base_url : str | None
            Another root; the server's when None.
        timeout_s : float
            Time allowed per request.

        Returns
        -------
        BodsFeedsClient
            The client.
        """
        made = BodsFeedsClient(
            key=key,
            box=CAMBRIDGE_BBOX,
            session=session,
            base_url=base_url or str(self.server.make_url('/')),
            timeout_s=timeout_s,
        )
        self.clients.append(made)
        return made


@pytest.fixture(name='stub')
async def _stub() -> AsyncIterator[BodsStub]:
    """
    Run a BODS stub and close everything it made.

    Yields
    ------
    BodsStub
        The stub.
    """
    stub = BodsStub()
    await stub.server.start_server()
    yield stub
    for client in stub.clients:
        await client.close()
    await stub.server.close()


class RaisingSession:
    """
    A session whose requests fail with an error that holds the keyed URL.

    Parameters
    ----------
    error : aiohttp.ClientError
        What each request raises.
    """

    def __init__(self, *, error: aiohttp.ClientError) -> None:
        self.error = error

    def get(self, *args: object, **kwargs: object) -> None:
        """
        Raise the error.

        Parameters
        ----------
        *args : object
            Ignored.
        **kwargs : object
            Ignored.

        Raises
        ------
        aiohttp.ClientError
            Always.
        """
        del args, kwargs
        raise self.error


KEYED_URL: Final[URL] = URL('http://bods.test/gtfsrtdatafeed/?api_key=k-secret')


def leaking_error(*, case: str) -> aiohttp.ClientError:
    """
    Build an aiohttp error whose text holds the keyed URL.

    Parameters
    ----------
    case : str
        'response-error' or 'invalid-url'.

    Returns
    -------
    aiohttp.ClientError
        The error.
    """
    if case == 'invalid-url':
        return aiohttp.InvalidURL(KEYED_URL)
    info = aiohttp.RequestInfo(
        url=KEYED_URL,
        method='GET',
        headers=CIMultiDictProxy(CIMultiDict()),
        real_url=KEYED_URL,
    )
    return aiohttp.ClientResponseError(info, (), status=HTTPStatus.OK)


async def failure_of(stub: BodsStub, case: str) -> Exception | None:
    """
    Set the stub up for a failure, fetch, and give what was raised.

    Parameters
    ----------
    stub : BodsStub
        The stub.
    case : str
        'forbidden', 'siri-500', 'closed-port', 'timeout', 'response-error' or
        'invalid-url'.

    Returns
    -------
    Exception | None
        The exception raised, or None when the fetch returned.
    """
    stub.rt = (HTTPStatus.OK, ONE_COACH)
    stub.siri = {}
    client = stub.client(key=lambda: KEY)
    if case in {'response-error', 'invalid-url'}:
        session = cast(
            aiohttp.ClientSession,
            RaisingSession(error=leaking_error(case=case)),
        )
        client = stub.client(key=lambda: KEY, session=session)
    elif case == 'forbidden':
        stub.rt = (HTTPStatus.FORBIDDEN, b'')
    elif case == 'siri-500':
        stub.siri = {'NATX': (HTTPStatus.INTERNAL_SERVER_ERROR, b'')}
    elif case == 'closed-port':
        client = stub.client(key=lambda: KEY, base_url='http://127.0.0.1:1/')
    else:
        stub.delay = 0.5
        client = stub.client(key=lambda: KEY, timeout_s=0.05)
    try:
        await client.fetch(feed='bods/positions')
    except FeedError as error:
        return error
    return None


def test_box_strings_follow_each_endpoints_order() -> None:
    """Box strings follow each endpoints order."""
    assert rt_box(box=CAMBRIDGE_BBOX) == RT_BOX
    assert siri_box(box=CAMBRIDGE_BBOX) == SIRI_BOX


async def test_fetch_joins_the_coach_names_into_the_positions(
    stub: BodsStub,
) -> None:
    """Fetch joins the coach names into the positions."""
    stub.rt = (HTTPStatus.OK, ONE_COACH)
    stub.siri = {'NATX': (HTTPStatus.OK, NATX_217)}
    body = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert labels(body) == ['777', '']
    assert sorted(stub.seen, key=str) == [
        (
            '/datafeed/',
            {'operatorRef': 'NATX', 'boundingBox': SIRI_BOX, 'api_key': KEY},
        ),
        ('/gtfsrtdatafeed/', {'boundingBox': RT_BOX, 'api_key': KEY}),
    ]


@pytest.mark.parametrize(
    'answer',
    [
        (HTTPStatus.INTERNAL_SERVER_ERROR, b''),
        (HTTPStatus.OK, b'<Siri><unclosed>'),
        (HTTPStatus.TOO_MANY_REQUESTS, b''),
    ],
)
async def test_fetch_returns_the_positions_when_siri_fails(
    stub: BodsStub, caplog: pytest.LogCaptureFixture, answer: Answer,
) -> None:
    """Fetch returns the positions when siri fails."""
    stub.rt = (HTTPStatus.OK, ONE_COACH)
    stub.siri = {'NATX': answer}
    body = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert labels(body) == ['', '']
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1


async def test_fetch_without_a_key_sends_nothing(stub: BodsStub) -> None:
    """Fetch without a key sends nothing."""
    with pytest.raises(FeedError, match='bods/positions: no API key'):
        await stub.client(key=lambda: None).fetch(feed='bods/positions')
    assert not stub.seen


@pytest.mark.parametrize(
    ('status', 'error'),
    [
        (HTTPStatus.TOO_MANY_REQUESTS, FeedThrottled),
        (HTTPStatus.BAD_GATEWAY, FeedUnavailable),
        (HTTPStatus.FORBIDDEN, FeedError),
    ],
)
async def test_fetch_raises_for_a_refused_positions_request(
    stub: BodsStub, status: HTTPStatus, error: type[FeedError],
) -> None:
    """Fetch raises for a refused positions request."""
    stub.rt = (status, b'')
    with pytest.raises(error):
        await stub.client(key=lambda: KEY).fetch(feed='bods/positions')


LEAK_CASES: Final[dict[str, tuple[type[FeedError] | None, str]]] = {
    'forbidden': (FeedError, 'HTTP 403'),
    'siri-500': (None, ''),
    'closed-port': (FeedError, 'ClientConnectorError'),
    'timeout': (FeedTimedOut, 'TimeoutError'),
    'response-error': (FeedError, 'ClientResponseError'),
    'invalid-url': (FeedError, 'InvalidURL'),
}


@pytest.mark.parametrize('case', list(LEAK_CASES))
async def test_fetch_errors_and_logs_never_hold_the_key(
    stub: BodsStub, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    """Fetch errors and logs never hold the key."""
    expected, message = LEAK_CASES[case]
    raised = await failure_of(stub, case)
    if expected is None:
        assert raised is None
        assert 'no coach names' in caplog.text
    else:
        assert type(raised) is expected
        assert message in str(raised)
        assert raised.__cause__ is None
        assert raised.__context__ is None
    chain = traceback.format_exception(raised) if raised else []
    for text in (*chain, caplog.text):
        assert KEY not in text
        assert 'api_key' not in text
        assert 'http://' not in text


async def test_fetch_refuses_a_body_over_the_limit(stub: BodsStub) -> None:
    """Fetch refuses a body over the limit."""
    stub.rt = (HTTPStatus.OK, b'\x00' * (BODY_LIMIT + 1))
    with pytest.raises(FeedError, match='bods/positions: body too large'):
        await stub.client(key=lambda: KEY).fetch(feed='bods/positions')


async def test_fetch_reads_a_body_longer_than_one_chunk(
    stub: BodsStub,
) -> None:
    """Fetch reads a body longer than one chunk."""
    many = tuple((str(n), f'VJ{n}', '57428', '', 990) for n in range(6_000))
    stub.rt = (HTTPStatus.OK, positions_body(vehicles=many))
    body = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert len(labels(body)) == 6_000


async def test_fetch_refuses_an_unknown_feed(stub: BodsStub) -> None:
    """Fetch refuses an unknown feed."""
    with pytest.raises(FeedError, match='nope: unknown feed'):
        await stub.client(key=lambda: KEY).fetch(feed='nope')
    assert not stub.seen


async def test_close_leaves_an_injected_session_open(stub: BodsStub) -> None:
    """Close leaves an injected session open."""
    async with aiohttp.ClientSession() as session:
        await stub.client(key=lambda: KEY, session=session).close()
        assert not session.closed


async def test_fetch_refuses_a_declared_length_over_the_limit(
    stub: BodsStub,
) -> None:
    """Fetch refuses a declared length over the limit."""
    stub.declared = BODY_LIMIT + 1
    with pytest.raises(FeedError, match='bods/positions: body too large'):
        await stub.client(key=lambda: KEY).fetch(feed='bods/positions')


@pytest.mark.parametrize(
    'body',
    [
        b'',
        b'\xff\xfe<a>\xff</a>',
        b'<?xml version="1.0" encoding="UTF-8"?><a>\xff</a>',
        b'<a>\x00</a>',
        b'<?xml version="1.0" encoding="nope"?><a/>',
        b'<html><body><p>x<br></body></html>',
    ],
)
async def test_fetch_returns_the_positions_for_any_coach_body(
    stub: BodsStub, caplog: pytest.LogCaptureFixture, body: bytes,
) -> None:
    """Fetch returns the positions for any coach body."""
    stub.rt = (HTTPStatus.OK, ONE_COACH)
    stub.siri = {'NATX': (HTTPStatus.OK, body)}
    got = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert labels(got) == ['', '']
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1


async def test_request_repr_hides_the_key(stub: BodsStub) -> None:
    """A request's repr does not hold the key."""
    async with aiohttp.ClientSession() as session:
        request = BodsRequest(session=session, key=KEY, box=CAMBRIDGE_BBOX)
        assert KEY not in repr(request)


def test_key_path_from_env_reads_the_bods_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The key path comes from PAPERROUTE_KEY_FILE_BODS."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_BODS', str(tmp_path / 'key'))
    assert key_path_from_env() == tmp_path / 'key'


async def test_fetch_returns_the_positions_for_valid_xml_that_is_not_siri(
    stub: BodsStub, caplog: pytest.LogCaptureFixture,
) -> None:
    """Valid XML that is not SIRI names nothing and does not warn."""
    stub.rt = (HTTPStatus.OK, ONE_COACH)
    stub.siri = {'NATX': (HTTPStatus.OK, b'<a><b/></a>')}
    got = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert labels(got) == ['', '']
    assert not [r for r in caplog.records if r.levelname == 'WARNING']


async def test_fetch_refuses_a_chunked_body_over_the_limit(
    stub: BodsStub,
) -> None:
    """A body with no declared length is refused once it passes the limit."""
    stub.chunked = BODY_LIMIT + CHUNK_SIZE
    with pytest.raises(FeedError, match='bods/positions: body too large'):
        await stub.client(key=lambda: KEY).fetch(feed='bods/positions')


async def test_fetch_does_not_follow_a_redirect(stub: BodsStub) -> None:
    """A redirect is an error and its target is never requested."""
    stub.redirect = True
    with pytest.raises(FeedError, match='bods/positions: HTTP 301'):
        await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert '/elsewhere/' not in [path for path, _ in stub.seen]


async def test_bods_feeds_without_a_key_file_sends_nothing(
    stub: BodsStub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A missing key file gives a client that refuses and sends nothing."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_BODS', str(tmp_path / 'absent'))
    client = bods_feeds()
    assert isinstance(client, BodsFeedsClient)
    client.base_url = str(stub.server.make_url('/'))
    with pytest.raises(FeedError, match='bods/positions: no API key'):
        await client.fetch(feed='bods/positions')
    assert not stub.seen
    await client.close()
