"""Tests for Trip Planner suggestions on the ways page."""

from dataclasses import replace
from datetime import datetime
from http import HTTPStatus
from typing import Final
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.board_head import BoardHead
from paperroute.adapters.board_pages import PinPages
from paperroute.adapters.web_board import SuggestPlug
from paperroute.adapters.web_display import WebDisplay
from paperroute.city import SYDNEY, SYDNEY_ZONE
from paperroute.core.trip_search import Journey, JourneyLeg, TripSearch
from paperroute_testkit.core.connection_factories import WORK
from paperroute_testkit.fakes import FakeTripPlanner
from tests.adapters.test_connection_editor import OPTIONS
from tests.adapters.test_web_device import sydney_site
from tests.adapters.test_web_pins import Pins

Client = TestClient[web.Request, web.Application]
NOW: Final[datetime] = datetime(2026, 10, 27, 10, 0, tzinfo=SYDNEY_ZONE)
SEARCH: Final[list[tuple[str, str]]] = [
    ('from', 'G2145615'), ('to', '214510'), ('mode', 'dep'),
    ('day', '2026-10-28'), ('time', '07:30'),
]
PLATFORMS: Final = replace(OPTIONS, parents={'2145648_1': '2145648'})


def leg(
    origin: str, destination: str, route: str, departs: str, arrives: str,
) -> JourneyLeg:
    """
    Make a ride on 28 October, times given in UTC.

    Parameters
    ----------
    origin : str
        Stop boarded.
    destination : str
        Stop left.
    route : str
        Route.
    departs : str
        UTC time it leaves, HH:MM.
    arrives : str
        UTC time it arrives, HH:MM.

    Returns
    -------
    JourneyLeg
        The ride.
    """
    return JourneyLeg(
        origin_id=origin, destination_id=destination, route=route,
        departs=datetime.fromisoformat(f'2026-10-27T{departs}:00Z'),
        arrives=datetime.fromisoformat(f'2026-10-27T{arrives}:00Z'),
    )


GOOD: Final[Journey] = Journey(legs=(
    leg('G2145615', 'G215254', '606', '20:48', '20:54'),
    leg('2145648_1', '214510', '660', '21:01', '21:12'),
))
UNKNOWN: Final[Journey] = Journey(legs=(
    leg('G2145615', 'NEW1', '607', '20:50', '20:56'),
    leg('NEW2', '214510', '661', '21:03', '21:20'),
))
THREE: Final[Journey] = Journey(legs=(
    leg('G2145615', 'G215254', '606', '20:40', '20:46'),
    leg('2145648', '214510', '660', '20:50', '20:55'),
    leg('214510', '214510', 'T1', '21:00', '21:05'),
    leg('214510', '214510', 'T5', '21:10', '21:25'),
))
DIRECT: Final[Journey] = Journey(legs=(
    leg('G2145615', '214510', '609', '20:30', '21:30'),
))


async def started(
    *,
    planner: FakeTripPlanner | None,
    status: dict[str, object] | None = None,
) -> Client:
    """
    Start a display with the fake timetable and planner.

    Parameters
    ----------
    planner : FakeTripPlanner | None
        The planner.
    status : dict[str, object] | None
        What the app reports; nothing by default.

    Returns
    -------
    Client
        A started client.
    """
    pins = Pins(pins=(WORK,))
    board = PinPages(
        head=BoardHead(),
        pins=pins.get,
        options=PLATFORMS,
        modes=SYDNEY.modes,
        plug=SuggestPlug(planner=planner, now=lambda: NOW),
    )
    display = WebDisplay(
        device=sydney_site().pages(), board=board, status=lambda: status or {},
        city=SYDNEY,
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


async def fetched(
    *, planner: FakeTripPlanner | None, fields: list[tuple[str, str]],
) -> tuple[int, str]:
    """
    Ask for suggestions.

    Parameters
    ----------
    planner : FakeTripPlanner | None
        The planner.
    fields : list[tuple[str, str]]
        The query.

    Returns
    -------
    tuple[int, str]
        Status and body.
    """
    client = await started(planner=planner)
    response = await client.get(f'/pins/ways/suggest?{urlencode(fields)}')
    text = await response.text()
    await client.close()
    return response.status, text


async def test_suggest_lists_journeys() -> None:
    """Each journey is a row of chips, times and where it changes."""
    planner = FakeTripPlanner(journeys=(GOOD, DIRECT))
    status, text = await fetched(planner=planner, fields=SEARCH)
    assert status == HTTPStatus.OK
    assert '<span class="chip">606</span>' in text
    assert '<span class="times">07:48 → 08:12</span>' in text
    assert 'via Constitution Hill, North West Twy' in text
    assert 'name="pick" value="G215254,2145648"' in text
    assert '<span class="via">direct</span>' in text
    assert 'name="page" value="earlier"' in text
    assert 'name="time" value="07:30"' in text
    assert planner.asked == [(
        'G2145615', '214510',
        TripSearch(when=datetime(2026, 10, 28, 7, 30, tzinfo=SYDNEY_ZONE),
                   arrive=False),
    )]
    _, part = await fetched(
        planner=planner, fields=[*SEARCH, ('part', 'suggestions')],
    )
    assert part.strip().startswith('<div class="suggestions"')
    assert '<html' not in part


async def test_pick_adds_way_with_distance_allow() -> None:
    """A picked journey's way allows 80 m a minute plus 2 for the walk."""
    client = await started(planner=FakeTripPlanner())
    query = urlencode([*SEARCH[:2], ('pick', 'G215254,2145648')])
    text = await (await client.get(f'/pins/ways?{query}')).text()
    await client.close()
    assert 'name="ways" value="G215254,2145648,4"' in text


async def test_unmappable_journey_greyed() -> None:
    """A journey through an unknown stop is greyed; the page renders."""
    planner = FakeTripPlanner(journeys=(UNKNOWN, GOOD))
    status, text = await fetched(planner=planner, fields=SEARCH)
    assert status == HTTPStatus.OK
    assert 'via NEW2' in text
    assert text.count('not supported yet') == 1
    assert text.count('name="pick"') == 1


async def test_school_bus_journey_greyed() -> None:
    """A journey on a school bus is greyed unless school buses are on."""
    school = Journey(legs=(
        replace(GOOD.legs[0], school=True), GOOD.legs[1],
    ))
    planner = FakeTripPlanner(journeys=(school,))
    _, text = await fetched(planner=planner, fields=SEARCH)
    assert '<li class="greyed">' in text
    assert 'school bus' in text
    assert 'name="pick"' not in text
    client = await started(
        planner=planner,
        status={'settings': {'skip_school_buses': 'off'}},
    )
    response = await client.get(f'/pins/ways/suggest?{urlencode(SEARCH)}')
    text = await response.text()
    await client.close()
    assert 'name="pick"' in text


async def test_three_change_journey_greyed() -> None:
    """A journey with three changes is greyed and cannot be picked."""
    _, text = await fetched(
        planner=FakeTripPlanner(journeys=(THREE,)), fields=SEARCH,
    )
    assert '<li class="greyed">' in text
    assert 'not supported yet' in text
    assert 'name="pick"' not in text


async def test_planner_failure_keeps_inputs() -> None:
    """A failed or missing planner shows one line and keeps the inputs."""
    for planner in (FakeTripPlanner(down=True), None):
        status, text = await fetched(planner=planner, fields=SEARCH)
        assert status == HTTPStatus.OK
        assert text.count("Trip Planner didn't answer. Try again in a minute.") == 1
        assert 'name="day" value="2026-10-28"' in text
        assert 'name="time" value="07:30"' in text
        assert 'value="dep" checked' in text
        assert '<option value="G2145615" selected>' in text
    _, empty = await fetched(planner=FakeTripPlanner(), fields=SEARCH)
    assert 'No trips found.' in empty


async def test_earlier_later_one_call_each() -> None:
    """Earlier asks arrive by a minute before; Later leave at a minute after."""
    planner = FakeTripPlanner(journeys=(GOOD,))
    edges = [('first', '2026-10-27T20:48:00+00:00'),
             ('last', '2026-10-27T20:58:00+00:00')]
    for mode in ('arr', 'dep'):
        fields = [*SEARCH[:2], ('mode', mode), *SEARCH[3:], *edges]
        for page in ('earlier', 'later'):
            await fetched(planner=planner, fields=[*fields, ('page', page)])
    await fetched(
        planner=planner, fields=[*SEARCH, ('first', 'x'), ('page', 'earlier')],
    )
    earlier = TripSearch(
        when=datetime(2026, 10, 28, 7, 47, tzinfo=SYDNEY_ZONE), arrive=True,
    )
    later = TripSearch(
        when=datetime(2026, 10, 28, 7, 59, tzinfo=SYDNEY_ZONE), arrive=False,
    )
    assert [search for _, _, search in planner.asked] == [
        earlier, later, earlier, later,
        TripSearch(
            when=datetime(2026, 10, 28, 7, 30, tzinfo=SYDNEY_ZONE),
            arrive=False,
        ),
    ]


async def test_edges_are_first_arrival_and_last_departure() -> None:
    """The page edges are the earliest arrival and the latest departure."""
    planner = FakeTripPlanner(journeys=(GOOD, DIRECT))
    for mode in ('arr', 'dep'):
        fields = [*SEARCH[:2], ('mode', mode), *SEARCH[3:]]
        _, text = await fetched(planner=planner, fields=fields)
        assert 'name="first" value="2026-10-27T21:12:00+00:00"' in text
        assert 'name="last" value="2026-10-27T20:48:00+00:00"' in text


async def test_default_search_on_ways_page() -> None:
    """Past 9:00 the page defaults to arrive by 9:00 the next weekday."""
    client = await started(planner=FakeTripPlanner())
    text = await (await client.get('/pins/ways?from=G2145615&to=214510')).text()
    script = await (await client.get('/suggest.js')).text()
    bad = await (await client.get(
        '/pins/ways?from=G2145615&to=214510&day=x&time=y&mode=arr',
    )).text()
    await client.close()
    assert 'value="arr" checked' in text
    assert 'name="day" value="2026-10-28"' in text
    assert 'name="time" value="09:00"' in text
    assert 'name="day" value="2026-10-28"' in bad
    assert 'export async function loadSuggestions' in script


async def test_shutdown_closes_planner() -> None:
    """Shutting the web app down closes the Trip Planner."""
    planner = FakeTripPlanner()
    client = await started(planner=planner)
    await client.close()
    assert planner.closed


async def test_busy_planner_says_so() -> None:
    """A rate-limited planner gets its own line."""
    _, text = await fetched(
        planner=FakeTripPlanner(busy=True), fields=SEARCH,
    )
    assert 'Trip Planner is busy. Try again in a minute.' in text
    assert "didn't answer" not in text


async def test_direct_journey_not_pickable() -> None:
    """A direct journey is greyed with a note that it always shows."""
    _, text = await fetched(
        planner=FakeTripPlanner(journeys=(DIRECT,)), fields=SEARCH,
    )
    assert '<li class="greyed">' in text
    assert 'Direct trips always show on the panel.' in text
    assert 'name="pick"' not in text
