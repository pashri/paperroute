"""Tests for Cambridge's board pages."""

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import closing
from http import HTTPStatus
from pathlib import Path
from typing import Final

import pytest
from aiohttp import ClientResponse, web
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
from paperroute_transit.core.model import Pin, StopPlace, pin_ref
from paperroute_transit.observers import PinsSeen
from tests.build.test_rail_build import built
from tests.conftest import RECORDED_DAY

Client = TestClient[web.Request, web.Application]

LIVERPOOL: Final[str] = 'GA:Liverpool Street'
KINGS_LYNN: Final[str] = 'GN:Kings Lynn & Kings Cross'


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


@pytest.fixture(name='saved')
def saved_fixture() -> list[Pin]:
    """
    Hold the pins the app has reported.

    Returns
    -------
    list[Pin]
        The pins, empty to start.
    """
    return []


@pytest.fixture(name='client')
async def client_fixture(
    network: SqliteNetworkRepository, posted: list[Event], saved: list[Pin],
) -> AsyncIterator[Client]:
    """
    Serve Cambridge's pin pages over the recorded timetable.

    Parameters
    ----------
    network : SqliteNetworkRepository
        The network.
    posted : list[Event]
        Where the events go.
    saved : list[Pin]
        The pins the app has reported.

    Yields
    ------
    Client
        The client.
    """
    pages = PinPages(
        head=BoardHead(nav=CAMBRIDGE_NAV),
        pins=lambda: PinsSeen(pins=tuple(saved), shown=None, revision=1),
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


async def test_a_train_pin_needs_no_line(
    client: Client, posted: list[Event],
) -> None:
    """A train form posted with a From and a To saves no routes."""
    response = await client.post(
        '/pins/save',
        data={'stop': 'rail:CBG', 'kind': 'train', 'to': 'rail:ELY'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'].startswith('/pins')
    assert saved_routes(posted=posted) == ()


async def test_the_train_form_offers_no_line(client: Client) -> None:
    """The train editor has a Destination and no route select."""
    page = await client.get(
        '/pins/edit', params={'stop': 'rail:CBG', 'kind': 'train'},
    )
    text = await page.text()
    assert 'name="route"' not in text
    assert 'name="to"' in text


async def test_an_old_train_pin_opens_with_its_from_and_to(
    client: Client, saved: list[Pin], posted: list[Event],
) -> None:
    """A stored line is not shown, and saving the pin drops it."""
    pin = Pin(
        origin=CAMBRIDGE_STATION, destination=ELY, kind='train',
        routes=(KINGS_LYNN,),
    )
    saved.append(pin)
    ref = pin_ref(pin=pin)
    page = await client.get('/pins/edit', params={'ref': ref})
    text = await page.text()
    assert 'name="route"' not in text
    assert '<option value="rail:ELY" selected>' in text
    await client.post(
        '/pins/save',
        data={
            'ref': ref, 'stop': 'rail:CBG', 'kind': 'train',
            'to': 'rail:ELY',
        },
        allow_redirects=False,
    )
    assert saved_routes(posted=posted) == ()


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


CAMBRIDGE_STATION: Final[StopPlace] = StopPlace(
    stop_id='rail:CBG', name='Cambridge', point=None,
)
ELY: Final[StopPlace] = StopPlace(stop_id='rail:ELY', name='Ely', point=None)


async def ways_save(
    client: Client, *, pin: Pin, to: str, origin: str | None = None,
) -> ClientResponse:
    """
    Save a saved pin on the ways page with a Destination.

    Parameters
    ----------
    client : Client
        The client.
    pin : Pin
        The saved pin.
    to : str
        The Destination ID posted.
    origin : str | None
        The From ID posted; the pin's own when None.

    Returns
    -------
    ClientResponse
        The response.
    """
    return await client.post(
        '/pins/ways/save',
        data={
            'ref': pin_ref(pin=pin),
            'from': origin or pin.origin.stop_id,
            'to': to,
        },
        allow_redirects=False,
    )


async def test_a_ways_save_clears_an_old_train_pins_routes(
    client: Client, posted: list[Event], saved: list[Pin],
) -> None:
    """A saved train pin with a line saves none, however it is edited."""
    pin = Pin(
        origin=CAMBRIDGE_STATION, destination=ELY, kind='train',
        routes=(LIVERPOOL,),
    )
    saved.append(pin)
    for to, origin in (
        ('rail:ELY', None), ('rail:RYS', None), ('rail:CBG', 'rail:ELY'),
    ):
        posted.clear()
        response = await ways_save(client, pin=pin, to=to, origin=origin)
        assert response.status == HTTPStatus.SEE_OTHER
        assert saved_routes(posted=posted) == ()


async def test_a_ways_save_leaves_a_bus_pin_without_routes(
    client: Client, posted: list[Event], saved: list[Pin],
) -> None:
    """A bus pin storing a route ends with none after a ways save."""
    pin = Pin(
        origin=StopPlace(
            stop_id='0500CCITY227', name='Drummer St', point=None,
        ),
        destination=StopPlace(
            stop_id='0500SCOTO007', name='Scotsdales', point=None,
        ),
        kind='bus',
        routes=('4',),
    )
    saved.append(pin)
    response = await ways_save(client, pin=pin, to='0500SCOTO007')
    assert response.status == HTTPStatus.SEE_OTHER
    assert saved_routes(posted=posted) == ()


async def test_the_destination_ids_used_by_the_ways_tests_exist(
    client: Client,
) -> None:
    """Royston and Birmingham are stations a train reaches."""
    page = await client.get(
        '/pins/edit', params={'stop': 'rail:CBG', 'kind': 'train'},
    )
    text = await page.text()
    assert 'value="rail:RYS"' in text
    assert 'value="rail:BHM"' in text
