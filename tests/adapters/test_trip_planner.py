"""Tests for the Trip Planner adapter; no network, no key."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from paperroute_sydney.adapters.tfnsw import tfnsw_limiter
from paperroute_sydney.adapters.trip_planner import (
    KeyedTripPlanner,
    TfnswTripPlanner,
    parse_journeys,
    trip_planner_for,
)
from paperroute_sydney.core.trip_search import (
    PlannerBusy,
    PlannerUnavailable,
    TripSearch,
)
from tests.planner_fakes import FakeTripPlanner

FIXTURES: Final[Path] = Path(__file__).parents[1] / 'fixtures' / 'trip_planner'
WHEN: Final[datetime] = datetime(
    2026, 10, 27, 9, 0, tzinfo=ZoneInfo('Australia/Sydney'),
)


def fixture(*, name: str) -> object:
    """
    Read a hand-built Trip Planner reply.

    Parameters
    ----------
    name : str
        File name without the extension.

    Returns
    -------
    object
        The parsed JSON.
    """
    return json.loads((FIXTURES / f'{name}.json').read_text('utf-8'))


class Recorder:
    """
    A stand-in for the HTTP call that records what it was asked.

    Parameters
    ----------
    data : object
        What it answers with.
    """

    def __init__(self, *, data: object) -> None:
        self.data = data
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    async def __call__(
        self, url: str, params: Mapping[str, str],
    ) -> object:
        """
        Record a call.

        Parameters
        ----------
        url : str
            Address asked.
        params : Mapping[str, str]
            Query parameters sent.

        Returns
        -------
        object
            The canned reply.
        """
        self.calls.append((url, params))
        return self.data


def planner(*, fetch: Recorder) -> TfnswTripPlanner:
    """
    Make a planner over a recording fetch.

    Parameters
    ----------
    fetch : Recorder
        The fake HTTP call.

    Returns
    -------
    TfnswTripPlanner
        The planner.
    """
    return TfnswTripPlanner(
        session_factory=aiohttp.ClientSession,
        key='not-a-key',
        fetch_json=fetch,
    )


async def test_arrive_by_params() -> None:
    """Arrive-by asks for arrivals at the date and time given."""
    fetch = Recorder(data=fixture(name='arrive_by_work'))
    journeys = await planner(fetch=fetch).trips(
        origin_id='20001',
        destination_id='20004',
        search=TripSearch(when=WHEN, arrive=True),
    )
    url, params = fetch.calls[0]
    assert url.endswith('v1/tp/trip')
    assert params['itdTripDateTimeDepArr'] == 'arr'
    assert params['depArrMacro'] == 'arr'
    assert (params['itdDate'], params['itdTime']) == ('20261027', '0900')
    assert params['name_origin'] == '20001'
    assert params['name_destination'] == '20004'
    assert params['outputFormat'] == 'rapidJSON'
    assert 'not-a-key' not in ''.join(params.values())
    assert len(journeys) == 2


async def test_leave_at_params() -> None:
    """Leave-at asks for departures and omits the arrive flag."""
    fetch = Recorder(data=fixture(name='walk_only'))
    await planner(fetch=fetch).trips(
        origin_id='1',
        destination_id='2',
        search=TripSearch(when=WHEN, arrive=False),
    )
    params = fetch.calls[0][1]
    assert params['depArrMacro'] == 'dep'
    assert 'itdTripDateTimeDepArr' not in params
    assert params['calcNumberOfTrips'] == '5'


def test_parse_drops_walk_legs() -> None:
    """Walking legs go; the transit legs keep their stops and routes."""
    first, direct = parse_journeys(data=fixture(name='arrive_by_work'))
    assert [leg.route for leg in first.legs] == ['606', 'T1']
    assert [(leg.origin_id, leg.destination_id) for leg in first.legs] == [
        ('20001', '20002'), ('20003', '20004'),
    ]
    assert first.changes == 1
    assert first.legs[0].departs == datetime.fromisoformat(
        '2026-10-26T21:33:00+00:00',
    )
    assert direct.changes == 0


def test_parse_marks_school_buses() -> None:
    """A leg of product class 11 is a school bus; others are not."""
    data = fixture(name='arrive_by_work')
    assert isinstance(data, dict)
    data['journeys'][0]['legs'][0]['transportation']['product']['class'] = 11
    first, direct = parse_journeys(data=data)
    assert [leg.school for leg in first.legs] == [True, False]
    assert not any(leg.school for leg in direct.legs)


def test_parse_three_changes() -> None:
    """Four transit legs make three changes."""
    (journey,) = parse_journeys(data=fixture(name='three_changes'))
    assert journey.changes == 3


def test_parse_walk_only_is_empty() -> None:
    """A journey of only walking is not offered."""
    assert parse_journeys(data=fixture(name='walk_only')) == ()


def test_bad_journey_skipped() -> None:
    """A malformed journey is dropped; the good one stays."""
    data = fixture(name='arrive_by_work')
    assert isinstance(data, dict)
    broken = [
        {'legs': [{'origin': {'id': 'x'}}]},
        {'nolegs': True},
        'text',
    ]
    data['journeys'] = [*broken, *data['journeys']]
    assert len(parse_journeys(data=data)) == 2
    assert parse_journeys(data=['not', 'a', 'reply']) == ()
    assert parse_journeys(data={'journeys': 'x'}) == ()


async def serve(
    *, status: int, delay_s: float = 0.0,
) -> AsyncIterator[TestServer]:
    """
    Run a local server that answers every request one way.

    Parameters
    ----------
    status : int
        HTTP status to answer.
    delay_s : float
        Seconds to wait before answering.

    Yields
    ------
    TestServer
        The running server.
    """
    async def answer(request: web.Request) -> web.Response:
        del request
        await asyncio.sleep(delay_s)
        return web.json_response({'journeys': []}, status=status)

    app = web.Application()
    app.router.add_get('/v1/tp/trip', answer)
    server = TestServer(app)
    await server.start_server()
    yield server
    await server.close()


async def ask(*, server: TestServer, timeout_s: float = 10.0) -> None:
    """
    Ask a local server for a trip through the real HTTP path.

    Parameters
    ----------
    server : TestServer
        The server to ask.
    timeout_s : float
        Time allowed for the request.
    """
    async with aiohttp.ClientSession() as session:
        await TfnswTripPlanner(
            session_factory=lambda: session,
            key='not-a-key',
            base_url=str(server.make_url('/')),
            timeout_s=timeout_s,
        ).trips(
            origin_id='1',
            destination_id='2',
            search=TripSearch(when=WHEN, arrive=True),
        )


@pytest.mark.parametrize('status', [
    HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.INTERNAL_SERVER_ERROR,
])
async def test_429_unavailable(status: int) -> None:
    """A 429 or any other non-200 status says the planner is unavailable."""
    async for server in serve(status=status):
        with pytest.raises(PlannerUnavailable):
            await ask(server=server)


async def test_429_is_busy() -> None:
    """A 429 says the planner is busy; another failure does not."""
    async for server in serve(status=HTTPStatus.TOO_MANY_REQUESTS):
        with pytest.raises(PlannerBusy):
            await ask(server=server)
    async for server in serve(status=HTTPStatus.INTERNAL_SERVER_ERROR):
        with pytest.raises(PlannerUnavailable) as caught:
            await ask(server=server)
        assert not isinstance(caught.value, PlannerBusy)


async def test_ok_reply_parses() -> None:
    """A 200 reply goes through the real HTTP path."""
    async for server in serve(status=HTTPStatus.OK):
        await ask(server=server)


async def test_timeout_unavailable() -> None:
    """A slow answer is unavailable once the time is up."""
    async for server in serve(status=HTTPStatus.OK, delay_s=1.0):
        with pytest.raises(PlannerUnavailable):
            await ask(server=server, timeout_s=0.05)


async def test_connection_error_unavailable() -> None:
    """A refused connection is unavailable."""
    async with aiohttp.ClientSession() as session:
        with pytest.raises(PlannerUnavailable):
            await TfnswTripPlanner(
                session_factory=lambda: session,
                key='not-a-key',
                base_url='http://127.0.0.1:9/',
            ).trips(
                origin_id='1',
                destination_id='2',
                search=TripSearch(when=WHEN, arrive=True),
            )


async def test_fake_planner_records_and_fails() -> None:
    """The fake gives its journeys, and raises when told it is down."""
    search = TripSearch(when=WHEN, arrive=True)
    fake = FakeTripPlanner(journeys=parse_journeys(
        data=fixture(name='three_changes'),
    ))
    found = await fake.trips(origin_id='a', destination_id='b', search=search)
    assert len(found) == 1
    assert fake.asked == [('a', 'b', search)]
    fake.down = True
    with pytest.raises(PlannerUnavailable):
        await fake.trips(origin_id='a', destination_id='b', search=search)


async def test_keyed_planner_reads_key_each_time() -> None:
    """No key fails; a key is set before each request."""
    keys: list[str | None] = [None, 'first-fake-key', 'second-fake-key']
    fetch = Recorder(data={'journeys': []})
    keyed = KeyedTripPlanner(key=lambda: keys[0], planner=planner(fetch=fetch))
    search = TripSearch(when=WHEN, arrive=True)
    with pytest.raises(PlannerUnavailable):
        await keyed.trips(origin_id='A', destination_id='B', search=search)
    keys.pop(0)
    assert await keyed.trips(
        origin_id='A', destination_id='B', search=search,
    ) == ()
    assert keyed.planner.key == 'first-fake-key'
    assert len(fetch.calls) == 1


def test_planner_only_for_live_feeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replayed feeds get no planner; live feeds one over the key file."""
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(tmp_path / 'key'))
    assert trip_planner_for(live=False) is None
    live = trip_planner_for(live=True)
    assert live is not None
    assert live.key() is None
    assert live.planner.limiter is tfnsw_limiter()


async def test_close_closes_session() -> None:
    """Closing ends the session made on first use; unused, it is a no-op."""
    made: list[aiohttp.ClientSession] = []

    def session() -> aiohttp.ClientSession:
        made.append(aiohttp.ClientSession())
        return made[-1]

    async for server in serve(status=HTTPStatus.OK):
        tfnsw = TfnswTripPlanner(
            session_factory=session,
            key='not-a-key',
            base_url=str(server.make_url('/')),
        )
        keyed = KeyedTripPlanner(key=lambda: 'not-a-key', planner=tfnsw)
        await keyed.close()
        await keyed.trips(
            origin_id='1', destination_id='2',
            search=TripSearch(when=WHEN, arrive=True),
        )
        await keyed.close()
    assert made[0].closed
    assert tfnsw.session is None
