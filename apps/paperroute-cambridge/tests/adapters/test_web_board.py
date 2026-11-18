"""Tests for Cambridge's board pages."""

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import closing
from http import HTTPStatus
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_cambridge.adapters.web_board import (
    CAMBRIDGE_CREDITS,
    CAMBRIDGE_NAV,
    cambridge_status_line,
)
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.pin_options import SqlitePinOptions
from paperroute_transit.core.messages import Event, PinSaved
from paperroute_transit.observers import PinsSeen
from tests.build.test_rail_build import built
from tests.conftest import RECORDED_DAY

Client = TestClient[web.Request, web.Application]

XC = 'XC:Birmingham & Stansted'
LIVERPOOL = 'GA:Liverpool Street'


@pytest.fixture(name='network', scope='module')
def network_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteNetworkRepository]:
    """
    Open the network of the recorded day with trains.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the timetable.

    Yields
    ------
    SqliteNetworkRepository
        The network.
    """
    out = tmp_path_factory.mktemp('pins') / 'timetable.db'
    path: Path = built(out=out, start=RECORDED_DAY, days=1)
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=path),
    ) as network:
        yield network


@pytest.fixture(name='posted')
def posted_fixture() -> list[Event]:
    """
    Collect the events the pages send.

    Returns
    -------
    list[Event]
        The events, in order.
    """
    return []


@pytest.fixture(name='client')
async def client_fixture(
    network: SqliteNetworkRepository, posted: list[Event],
) -> AsyncIterator[Client]:
    """
    Serve Cambridge's pin pages over the recorded timetable.

    Parameters
    ----------
    network : SqliteNetworkRepository
        The network.
    posted : list[Event]
        Where the events go.

    Yields
    ------
    Client
        The client.
    """
    pages = PinPages(
        head=BoardHead(nav=CAMBRIDGE_NAV),
        pins=lambda: PinsSeen(pins=(), shown=None, revision=1),
        options=SqlitePinOptions(network=network),
        modes=CAMBRIDGE.modes,
        plug=None,
    )
    app = web.Application()
    app.add_routes(
        pages.routes(
            send=posted.append, reported=lambda: {'skip_school_buses': 'off'},
        ),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()


def saved_routes(*, posted: list[Event]) -> tuple[str, ...]:
    """
    Read the routes of the pin saved.

    Parameters
    ----------
    posted : list[Event]
        The events sent.

    Returns
    -------
    tuple[str, ...]
        The pin's routes.
    """
    (event,) = posted
    assert isinstance(event, PinSaved)
    return event.pin.routes


def test_credits_name_the_licence_and_the_map() -> None:
    """Credits name the licence, the train data and the map."""
    assert CAMBRIDGE_CREDITS == (
        'Bus and coach data: contains public sector information licensed '
        'under the <a href="https://www.nationalarchives.gov.uk/doc/'
        'open-government-licence/version/3/">Open Government Licence '
        'v3.0</a>.',
        'Train timetables: Rail Delivery Group and National Rail '
        'Enquiries, through <a href="https://github.com/planarnetwork/'
        'gb-transit">gb-transit</a>. Station data: Department for '
        'Transport.',
        'Map data © <a href="https://www.openstreetmap.org/copyright">'
        'OpenStreetMap</a> contributors.',
    )


def test_status_line_reports_the_positions_parse() -> None:
    """Status line reports the positions parse."""
    status = {
        'frame_age_s': 1.4,
        'last_render_ms': 80.5,
        'feeds': {'bods/positions': {'parse_ms': 12.2}},
    }
    assert cambridge_status_line(status=status) == (
        'Frame age 1 s, render 81 ms, positions parse 12 ms'
    )


async def test_a_named_line_round_trips_through_the_form(
    client: Client, posted: list[Event],
) -> None:
    """The line chosen for the owner is shown, posted back and saved."""
    page = await client.get(
        '/pins/edit', params={'stop': 'rail:CBG', 'kind': 'train',
                              'to': 'rail:ELY'},
    )
    text = await page.text()
    assert 'value="XC:Birmingham &amp; Stansted" selected' in text
    assert 'XC Birmingham &amp; Stansted · chosen for you' in text
    assert 'name="suggested" value="XC:Birmingham &amp; Stansted"' in text
    response = await client.post(
        '/pins/save',
        data={
            'stop': 'rail:CBG', 'kind': 'train', 'to': 'rail:ELY',
            'route': XC, 'suggested': XC,
        },
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'].startswith('/pins')
    assert saved_routes(posted=posted) == (XC,)


async def test_the_destination_lists_every_station_a_train_reaches(
    client: Client,
) -> None:
    """The Destination offers None, then the nine stations."""
    page = await client.get(
        '/pins/edit', params={'stop': 'rail:CBG', 'kind': 'train'},
    )
    text = await page.text()
    select = text.split('name="to"')[1].split('</select>')[0]
    assert re.findall(r'>([^<]+)</option>', select) == [
        'None: every departure',
        'Birmingham New Street', 'Brighton', 'Cambridge North',
        'Cambridge South', 'Ely', 'London Kings Cross',
        'London Liverpool Street', 'London St Pancras International',
        'Royston',
    ]


async def test_a_suggested_line_follows_a_new_destination_on_save(
    client: Client, posted: list[Event],
) -> None:
    """A line the owner did not pick follows the Destination chosen."""
    response = await client.post(
        '/pins/save',
        data={
            'stop': 'rail:CBG', 'kind': 'train', 'to': 'rail:ELY',
            'route': LIVERPOOL, 'suggested': LIVERPOOL,
        },
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert saved_routes(posted=posted) == (XC,)


async def test_a_picked_line_not_offered_is_refused_on_save(
    client: Client, posted: list[Event],
) -> None:
    """A line the From does not have is refused."""
    response = await client.post(
        '/pins/save',
        data={
            'stop': 'rail:CBG', 'kind': 'train', 'to': 'rail:ELY',
            'route': 'GA:Nowhere', 'suggested': XC,
        },
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted


async def test_save_without_a_route_or_suggestion_is_refused(
    client: Client, posted: list[Event],
) -> None:
    """An empty route is refused even though a line could be suggested."""
    response = await client.post(
        '/pins/save',
        data={
            'stop': 'rail:CBG', 'kind': 'train', 'to': 'rail:ELY',
            'route': '',
        },
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted
