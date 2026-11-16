"""Tests for the BODS feeds client, against a real local server."""

import asyncio
import traceback
from collections.abc import AsyncIterator, Callable
from http import HTTPStatus
from typing import Final

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from paperroute_cambridge.adapters.bods import (
    BODY_LIMIT,
    BodsFeedsClient,
    rt_box,
    siri_box,
)
from paperroute_cambridge.area import CAMBRIDGE_BBOX
from paperroute_transit.adapters.feed_errors import (
    FeedError,
    FeedThrottled,
    FeedUnavailable,
)
from tests.adapters.feed_bodies import (
    AT_990,
    labels,
    positions_body,
    siri_body,
)

KEY: Final[str] = 'k-secret'
RT_BOX: Final[str] = '52.005,52.471,-0.482,0.499'
SIRI_BOX: Final[str] = '-0.482,52.005,0.499,52.471'
ONE_COACH: Final[bytes] = positions_body(
    vehicles=(('217', '', '', '', 990), ('3300', 'VJ1', '57428', '', 990)),
)
NATX_217: Final[bytes] = siri_body(
    activities=(('NATX', '217', '777', '777', AT_990),),
)
NO_COACH: Final[bytes] = siri_body(activities=())
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
    """

    def __init__(self) -> None:
        self.rt: Answer = (HTTPStatus.OK, b'')
        self.siri: dict[str, Answer] = {}
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.delay = 0.0
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
        return app

    async def answer(self, request: web.Request) -> web.Response:
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


async def failure_of(stub: BodsStub, case: str) -> Exception | None:
    """
    Set the stub up for a failure, fetch, and give what was raised.

    Parameters
    ----------
    stub : BodsStub
        The stub.
    case : str
        'forbidden', 'siri-500', 'closed-port' or 'timeout'.

    Returns
    -------
    Exception | None
        The exception raised, or None when the fetch returned.
    """
    stub.rt = (HTTPStatus.OK, ONE_COACH)
    stub.siri = {}
    client = stub.client(key=lambda: KEY)
    if case == 'forbidden':
        stub.rt = (HTTPStatus.FORBIDDEN, b'')
    elif case == 'siri-500':
        stub.siri = dict.fromkeys(
            ('NATX', 'FLIX'), (HTTPStatus.INTERNAL_SERVER_ERROR, b''),
        )
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
    stub.siri = {
        'NATX': (HTTPStatus.OK, NATX_217),
        'FLIX': (HTTPStatus.OK, NO_COACH),
    }
    body = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert labels(body) == ['777', '']
    assert sorted(stub.seen, key=str) == [
        (
            '/datafeed/',
            {'operatorRef': 'FLIX', 'boundingBox': SIRI_BOX, 'api_key': KEY},
        ),
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
    stub.siri = {'NATX': answer, 'FLIX': answer}
    body = await stub.client(key=lambda: KEY).fetch(feed='bods/positions')
    assert labels(body) == ['', '']
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 2


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


@pytest.mark.parametrize(
    'case', ['forbidden', 'siri-500', 'closed-port', 'timeout'],
)
async def test_fetch_errors_and_logs_never_hold_the_key(
    stub: BodsStub, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    """Fetch errors and logs never hold the key."""
    raised = await failure_of(stub, case)
    if case == 'siri-500':
        assert raised is None
    else:
        assert raised is not None
    chain = traceback.format_exception(raised) if raised else []
    if case == 'siri-500':
        assert 'no coach names' in caplog.text
    for text in (*chain, caplog.text):
        assert KEY not in text
        assert 'api_key' not in text
        assert 'http://' not in text
    if raised:
        assert raised.__context__ is None or raised.__suppress_context__


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
