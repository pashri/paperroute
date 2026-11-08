"""End-to-end tests of adding a connection pin from the Pins page."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from http import HTTPStatus
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_board import SuggestPlug
from paperroute.city import SYDNEY, SYDNEY_ZONE, SydneyMode
from paperroute.core.trip_search import Journey, JourneyLeg
from paperroute_testkit.core.connection_factories import (
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
)
from paperroute_testkit.core.factories import place
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.adapters.web_display import WebDisplay
from paperroute_transit.core.messages import Event, PinSaved
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.pin_choices import ChangeStop, StopMatch
from paperroute_transit.observers import PinsSeen
from tests.adapters.test_web_device import sydney_site
from tests.planner_fakes import FakeTripPlanner

Client = TestClient[web.Request, web.Application]
NOW: Final[datetime] = datetime(2026, 10, 27, 10, 0, tzinfo=SYDNEY_ZONE)
TOWN_HALL: Final[StopPlace] = place(
    '200070', name='Town Hall Station', point=(900.0, -9000.0),
)
OPTIONS: Final[FakePinOptions] = FakePinOptions(
    places={
        stop.stop_id: stop
        for stop in (HOLLIS, HAMMERS, CONSTITUTION, TOWN_HALL)
    },
    found=(
        StopMatch(place=HOLLIS, kinds=(SydneyMode.BUS,), routes=('606',)),
        StopMatch(place=TOWN_HALL, kinds=(SydneyMode.TRAIN,), routes=('T1',)),
    ),
    changes=(ChangeStop(place=CONSTITUTION, metres=150),),
    reached=(TOWN_HALL,),
    reached_by={HOLLIS.stop_id: (HAMMERS,)},
    between=('606',),
    parents={'2145648_1': CONSTITUTION.stop_id},
)
JOURNEY: Final[Journey] = Journey(legs=(
    JourneyLeg(
        origin_id=HOLLIS.stop_id, destination_id=HAMMERS.stop_id,
        route='606',
        departs=datetime(2026, 10, 27, 21, 48, tzinfo=SYDNEY_ZONE),
        arrives=datetime(2026, 10, 27, 21, 54, tzinfo=SYDNEY_ZONE),
    ),
    JourneyLeg(
        origin_id='2145648_1', destination_id=TOWN_HALL.stop_id,
        route='660',
        departs=datetime(2026, 10, 27, 22, 1, tzinfo=SYDNEY_ZONE),
        arrives=datetime(2026, 10, 27, 22, 40, tzinfo=SYDNEY_ZONE),
    ),
))
VIA: Final[str] = 'via Constitution Hill, North West Twy'


@dataclass
class App:
    """
    Stands in for the app: saving a pin adds it and bumps the revision.

    Attributes
    ----------
    seen : PinsSeen
        The pins last reported.
    posted : list[Event]
        Events posted.
    """

    seen: PinsSeen = field(
        default_factory=lambda: PinsSeen(pins=(), shown=None, revision=3),
    )
    posted: list[Event] = field(default_factory=list)

    def get(self) -> PinsSeen:
        """
        Give the latest report.

        Returns
        -------
        PinsSeen
            The pins.
        """
        return self.seen

    def post(self, event: Event) -> None:
        """
        Take an event, adding a saved pin.

        Parameters
        ----------
        event : Event
            The event.
        """
        self.posted.append(event)
        if isinstance(event, PinSaved):
            self.seen = PinsSeen(
                pins=(*self.seen.pins, event.pin), shown=0,
                revision=self.seen.revision + 1,
            )


class FormReader(HTMLParser):
    """
    Read the fields a browser with scripts would submit from one form.

    Parameters
    ----------
    marker : str
        Text in the form's start tag that picks it.
    """

    def __init__(self, *, marker: str) -> None:
        super().__init__()
        self.marker = marker
        self.fields: list[tuple[str, str]] = []
        self.inside = False
        self.hidden = False
        self.select: str | None = None
        self.picked: list[tuple[str, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """
        Note a field.

        Parameters
        ----------
        tag : str
            Tag name.
        attrs : list[tuple[str, str | None]]
            Its attributes.
        """
        found = {name: value or '' for name, value in attrs}
        if tag == 'form':
            self.inside = self.marker in (self.get_starttag_text() or '')
        elif tag == 'noscript':
            self.hidden = True
        elif self.inside and not self.hidden:
            self.field(tag=tag, found=found)

    def field(self, *, tag: str, found: dict[str, str]) -> None:
        """
        Keep a field's value when a browser would send it.

        Parameters
        ----------
        tag : str
            Tag name.
        found : dict[str, str]
            Its attributes.
        """
        kind = found.get('type', 'text')
        if tag == 'select':
            self.select = found['name']
        elif tag == 'option' and self.select and 'selected' in found:
            self.fields.append((self.select, found['value']))
        elif tag == 'input' and (
            kind not in ('checkbox', 'radio') or 'checked' in found
        ):
            self.fields.append((found['name'], found.get('value', '')))

    def handle_endtag(self, tag: str) -> None:
        """
        Leave a form, select or noscript.

        Parameters
        ----------
        tag : str
            Tag name.
        """
        if tag == 'form':
            self.inside = False
        elif tag == 'noscript':
            self.hidden = False
        elif tag == 'select':
            self.select = None


def form_fields(*, text: str, marker: str) -> list[tuple[str, str]]:
    """
    Read the fields a form would submit.

    Parameters
    ----------
    text : str
        The page.
    marker : str
        Text in the form's start tag.

    Returns
    -------
    list[tuple[str, str]]
        The fields, in order.
    """
    reader = FormReader(marker=marker)
    reader.feed(text)
    return reader.fields


def chose(
    *, fields: list[tuple[str, str]], **choices: str,
) -> list[tuple[str, str]]:
    """
    Set fields as a person would.

    Parameters
    ----------
    fields : list[tuple[str, str]]
        The form's fields.
    **choices : str
        Values by field name; replace or add.

    Returns
    -------
    list[tuple[str, str]]
        The fields with the choices.
    """
    kept = [(name, value) for name, value in fields if name not in choices]
    return kept + list(choices.items())


@pytest.fixture(name='app')
def _app() -> App:
    """The stand-in app, with no pins."""
    return App()


@pytest.fixture(name='client')
async def _client(app: App) -> AsyncIterator[Client]:
    """A client of a display with the fake timetable and planner."""
    board = PinPages(
        head=BoardHead(),
        pins=app.get,
        options=OPTIONS,
        modes=SYDNEY.modes,
        plug=SuggestPlug(
            planner=FakeTripPlanner(journeys=(JOURNEY,)), now=lambda: NOW,
        ),
    )
    display = WebDisplay(
        device=sydney_site().pages(),
        board=board,
        city=SYDNEY,
    )
    display.bind(post=app.post)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    yield client
    await client.close()


async def found_id(*, client: Client, text: str) -> str:
    """
    Search for a stop as Tom Select does and give the match's ID.

    Parameters
    ----------
    client : Client
        The client.
    text : str
        The name typed.

    Returns
    -------
    str
        The ID of the first match named with the text.
    """
    response = await client.get(f'/pins/stops.json?{urlencode({"q": text})}')
    matches: list[dict[str, str]] = await response.json()
    return next(match['id'] for match in matches if text in match['name'])


async def ways_page(*, client: Client) -> list[tuple[str, str]]:
    """
    Open Add a connection from the Pins page and pick From and To.

    Parameters
    ----------
    client : Client
        The client.

    Returns
    -------
    list[tuple[str, str]]
        The ways page's fields with From and To chosen.
    """
    pins = await (await client.get('/pins')).text()
    assert '<form method="get" action="/pins/ways">' in pins
    page = await (await client.get('/pins/ways')).text()
    assert '<legend>From and to</legend>' in page
    assert 'Suggest with Trip Planner' in page
    assert '>Add a way by hand</button>' in page
    fields = form_fields(text=page, marker='class="editor ways"')
    origin = await found_id(client=client, text='Hollis')
    destination = await found_id(client=client, text='Town Hall')
    return chose(fields=fields, **{'from': origin, 'to': destination})


async def saved_and_listed(
    *, client: Client, app: App, fields: list[tuple[str, str]],
) -> str:
    """
    Save the ways page and read the Pins page.

    Parameters
    ----------
    client : Client
        The client.
    app : App
        The stand-in app.
    fields : list[tuple[str, str]]
        The ways page's fields.

    Returns
    -------
    str
        The Pins page after the save.
    """
    response = await client.post(
        '/pins/ways/save', data=fields, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert len(app.posted) == 1
    return await (await client.get(response.headers['Location'])).text()


async def test_add_connection_with_trip_planner(
    app: App, client: Client,
) -> None:
    """Pins, Add a connection, From and To, suggest, pick, save, listed."""
    fields = await ways_page(client=client)
    suggested = await (
        await client.get(f'/pins/ways/suggest?{urlencode(fields)}')
    ).text()
    pick = 'G215254,2145648'
    assert f'name="pick" value="{pick}"' in suggested
    fields = form_fields(text=suggested, marker='class="editor ways"')
    picked = await (await client.get(
        f'/pins/ways?{urlencode([*fields, ("pick", pick)])}',
    )).text()
    assert 'name="way"\n               value="0" checked>' in picked
    fields = form_fields(text=picked, marker='class="editor ways"')
    listed = await saved_and_listed(client=client, app=app, fields=fields)
    saved = app.seen.pins[0]
    assert (saved.origin, saved.destination) == (HOLLIS, TOWN_HALL)
    assert len(saved.ways) == 1 and saved.ways[0].ticked
    assert VIA in listed


async def test_add_connection_by_hand(app: App, client: Client) -> None:
    """Add a way by hand keeps From and To, builds leg 1 and saves."""
    fields = await ways_page(client=client)
    fields = chose(fields=fields, nickname='Office')
    response = await client.get(
        f'/pins/ways/hand?{urlencode([*fields, ("hand", "")])}',
    )
    page = await response.text()
    assert urlsplit(str(response.url)).path == '/pins/connect'
    assert 'name="kind"' not in page
    marker = 'data-steps'
    steps = chose(
        fields=form_fields(text=page, marker=marker), off=HAMMERS.stop_id,
    )
    page = await (await client.get(f'/pins/connect?{urlencode(steps)}')).text()
    assert 'Constitution Hill, North West Twy · 150 m' in page
    steps = chose(
        fields=form_fields(text=page, marker=marker),
        on=CONSTITUTION.stop_id,
    )
    page = await (await client.get(f'/pins/connect?{urlencode(steps)}')).text()
    assert 'Then on to Town Hall Station.' in page
    response = await client.post(
        '/pins/connect/save', data=form_fields(text=page, marker=marker),
    )
    page = await response.text()
    assert urlsplit(str(response.url)).path == '/pins/ways'
    fields = form_fields(text=page, marker='class="editor ways"')
    assert ('nickname', 'Office') in fields
    listed = await saved_and_listed(client=client, app=app, fields=fields)
    assert VIA in listed
    assert 'Office' in listed


async def test_add_connection_without_js(app: App, client: Client) -> None:
    """Without scripts, typed names search; the matches fill the selects."""
    pins = await client.get(
        '/pins/new?q=Hollis&connect=1', allow_redirects=False,
    )
    assert pins.headers['Location'] == '/pins/ways?from_q=Hollis'
    page = await (await client.get(
        '/pins/ways?from_q=Hollis&to_q=Town+Hall',
    )).text()
    assert '<noscript>' in page
    assert 'name="from_q"\n                   value="Hollis"' in page
    assert 'value="200070">Town Hall Station</option>' in page
    fields = [
        *parse_qsl('from_q=Hollis&to_q=Town+Hall'),
        ('from', HOLLIS.stop_id), ('to', TOWN_HALL.stop_id),
    ]
    page = await (await client.get(f'/pins/ways?{urlencode(fields)}')).text()
    assert '<option value="200070" selected>Town Hall Station' in page
    missing = await client.post(
        '/pins/ways/save', data=[('from', HOLLIS.stop_id)],
    )
    assert missing.status == HTTPStatus.BAD_REQUEST
    assert '<legend>From and to</legend>' in await missing.text()
    assert app.posted == []


async def test_suggest_needs_from_and_to(client: Client) -> None:
    """Suggest without both stops asks for them instead of calling."""
    text = await (await client.get(
        '/pins/ways/suggest?from=G2145615&part=suggestions',
    )).text()
    assert 'Pick From and To first.' in text
