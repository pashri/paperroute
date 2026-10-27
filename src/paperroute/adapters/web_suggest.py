"""Suggest ways to go with the Trip Planner."""

import html
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Final

from aiohttp import web
from multidict import MultiMapping

from paperroute.adapters.web_common import asset
from paperroute.adapters.web_pins import html_page
from paperroute.adapters.web_ways import SUGGEST, WaysPages
from paperroute.core.model import MAX_CHANGES, SYDNEY, StopPlace
from paperroute.core.trip_search import (
    Journey,
    PlannerUnavailable,
    TripSearch,
    default_search,
)
from paperroute.ports import PinOptions, TripPlanner

FAILURE: Final[str] = "Trip Planner didn't answer. Try again in a minute."
NO_TRIPS: Final[str] = 'No trips found.'
NOT_YET: Final[str] = 'not supported yet'
ARROW: Final[str] = '\u2192'
STEP: Final[timedelta] = timedelta(minutes=1)
PAGES: Final[dict[str, str]] = {'earlier': 'first', 'later': 'last'}
CONTROLS: Final[str] = """
        <fieldset class="when">
          <legend>When</legend>
          <label><input type="radio" name="mode" value="arr"{arr}>
            Arrive by</label>
          <label><input type="radio" name="mode" value="dep"{dep}>
            Leave at</label>
          <p>
            <label for="trip-day">Day</label>
            <input id="trip-day" type="date" name="day" value="{day}">
          </p>
          <p>
            <label for="trip-time">Time</label>
            <input id="trip-time" type="time" name="time" value="{time}">
          </p>
        </fieldset>"""
LIST: Final[str] = """
        <div class="suggestions" aria-live="polite" data-suggestions>{body}
        </div>"""
EDGES: Final[str] = """
          <input type="hidden" name="first" value="{first}">
          <input type="hidden" name="last" value="{last}">"""
PAGE_BUTTONS: Final[str] = """
          <p class="buttons">
            <button type="submit" name="page" value="earlier"
                    class="secondary" formaction="/pins/ways/suggest"
                    data-page>Earlier</button>
            <button type="submit" name="page" value="later"
                    class="secondary" formaction="/pins/ways/suggest"
                    data-page>Later</button>
          </p>"""
PICK: Final[str] = """
            <li><button type="submit" name="pick" value="{pick}"
                        formaction="/pins/ways"
                        class="journey">{text}</button></li>"""
GREYED: Final[str] = """
            <li class="greyed"><span class="journey">{text}</span>
              <span class="tag">{tag}</span></li>"""
PLAIN: Final[str] = """
            <li><span class="journey">{text}</span></li>"""


@dataclass(frozen=True)
class SuggestionRow:
    """
    One journey as the list shows it.

    Attributes
    ----------
    journey : Journey
        The journey.
    boards : tuple[str, ...]
        Name of each stop changed onto; the Trip Planner ID when unknown.
    pick : str | None
        The way's change stops as `alight,board|...`; None when it cannot
        be picked.
    greyed : bool
        True when a stop is unknown or there are too many changes.
    """

    journey: Journey
    boards: tuple[str, ...]
    pick: str | None
    greyed: bool


def search_of(*, query: MultiMapping[str], now: datetime) -> TripSearch:
    """
    Read the search asked for, shifted by Earlier or Later.

    Parameters
    ----------
    query : MultiMapping[str]
        Fields `mode` (`arr` or `dep`), `day`, `time`, and `page` with
        `first` and `last`.
    now : datetime
        The current time.

    Returns
    -------
    TripSearch
        The search; the default for missing or unreadable fields.
    """
    default = default_search(now=now)
    arrive = query.get('mode', 'arr') != 'dep'
    try:
        moment = datetime.combine(
            date.fromisoformat(query['day']),
            time.fromisoformat(query['time']),
            tzinfo=SYDNEY,
        )
    except (KeyError, ValueError):
        moment = default.when
    return shifted(query=query, search=TripSearch(when=moment, arrive=arrive))


def shifted(*, query: MultiMapping[str], search: TripSearch) -> TripSearch:
    """
    Move a search to just before the first or after the last journey.

    Parameters
    ----------
    query : MultiMapping[str]
        Fields `page`, `first` and `last`.
    search : TripSearch
        The search typed.

    Returns
    -------
    TripSearch
        One minute before `first` for Earlier, after `last` for Later;
        else the search unchanged.
    """
    page = query.get('page', '')
    if page not in PAGES:
        return search
    try:
        edge = datetime.fromisoformat(query.get(PAGES[page], ''))
    except ValueError:
        return search
    step = -STEP if page == 'earlier' else STEP
    return replace(search, when=(edge + step).astimezone(SYDNEY))


def controls(*, search: TripSearch) -> str:
    """
    Draw the Arrive by and Leave at controls.

    Parameters
    ----------
    search : TripSearch
        The search to show.

    Returns
    -------
    str
        The fieldset.
    """
    when = search.when.astimezone(SYDNEY)
    return CONTROLS.format(
        arr=' checked' if search.arrive else '',
        dep='' if search.arrive else ' checked',
        day=when.date().isoformat(),
        time=when.strftime('%H:%M'),
    )


def row_of(*, options: PinOptions, journey: Journey) -> SuggestionRow:
    """
    Map a journey's change stops onto the timetable.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    journey : Journey
        The journey.

    Returns
    -------
    SuggestionRow
        The row; greyed when a change stop is unknown or there are more
        than `MAX_CHANGES` changes.
    """
    legs = journey.legs
    ids = [
        (first.destination_id, second.origin_id)
        for first, second in zip(legs, legs[1:])
    ]
    places = [
        (options.trip_stop(stop_id=alight), options.trip_stop(stop_id=board))
        for alight, board in ids
    ]
    boards = tuple(
        board_name(place=board, stop_id=stop_id)
        for (_, board), (_, stop_id) in zip(places, ids, strict=True)
    )
    known = all(a is not None and b is not None for a, b in places)
    greyed = not known or journey.changes > MAX_CHANGES
    pick = None if greyed or not places else '|'.join(
        f'{alight.stop_id},{board.stop_id}'
        for alight, board in places
        if alight is not None and board is not None
    )
    return SuggestionRow(
        journey=journey, boards=boards, pick=pick, greyed=greyed,
    )


def board_name(*, place: StopPlace | None, stop_id: str) -> str:
    """
    Name a stop changed onto.

    Parameters
    ----------
    place : StopPlace | None
        The timetable's place, or None.
    stop_id : str
        Its Trip Planner ID.

    Returns
    -------
    str
        The place's name, else the ID.
    """
    return stop_id if place is None else place.name


def row_text(*, row: SuggestionRow) -> str:
    """
    Describe a row: chips, times and where it changes.

    Parameters
    ----------
    row : SuggestionRow
        The row.

    Returns
    -------
    str
        Escaped HTML.
    """
    legs = row.journey.legs
    arrow = f' <span aria-hidden="true">{ARROW}</span> '
    chips = arrow.join(
        f'<span class="chip">{html.escape(leg.route)}</span>' for leg in legs
    )
    leaves = legs[0].departs.astimezone(SYDNEY).strftime('%H:%M')
    arrives = legs[-1].arrives.astimezone(SYDNEY).strftime('%H:%M')
    via = f'via {", ".join(row.boards)}' if row.boards else 'direct'
    return (
        f'{chips} <span class="times">{leaves} {ARROW} {arrives}</span>'
        f' <span class="via">{html.escape(via)}</span>'
    )


def row_html(*, row: SuggestionRow) -> str:
    """
    Draw one row: a pick button, or greyed with its tag.

    Parameters
    ----------
    row : SuggestionRow
        The row.

    Returns
    -------
    str
        A list item.
    """
    text = row_text(row=row)
    if row.greyed:
        return GREYED.format(text=text, tag=NOT_YET)
    if row.pick is None:
        return PLAIN.format(text=text)
    return PICK.format(pick=html.escape(row.pick), text=text)


def list_html(
    *, options: PinOptions, journeys: tuple[Journey, ...], arrive: bool,
) -> str:
    """
    Draw the suggestion list.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    journeys : tuple[Journey, ...]
        The journeys found.
    arrive : bool
        True when the search was arrive-by.

    Returns
    -------
    str
        The list with Earlier and Later, or the empty line.
    """
    if not journeys:
        return LIST.format(body=f'\n          <p>{NO_TRIPS}</p>')
    rows = ''.join(
        row_html(row=row_of(options=options, journey=journey))
        for journey in journeys
    )
    edges = [
        journey.legs[-1].arrives if arrive else journey.legs[0].departs
        for journey in journeys
    ]
    body = EDGES.format(
        first=min(edges).isoformat(), last=max(edges).isoformat(),
    )
    body += f'\n          <ol class="journeys">{rows}\n          </ol>'
    return LIST.format(body=body + PAGE_BUTTONS)


def failure_html() -> str:
    """
    Draw the line shown when the Trip Planner did not answer.

    Returns
    -------
    str
        The list holding only the failure line.
    """
    text = html.escape(FAILURE, quote=False)
    return LIST.format(body=f'\n          <p class="failure">{text}</p>')


class SuggestPages:
    """
    The suggestion list on the ways page.

    Parameters
    ----------
    ways : WaysPages
        The ways page, to draw around the list.
    planner : TripPlanner | None
        The Trip Planner, or None when there is no key.
    now : Callable[[], datetime]
        Gives the current time.
    """

    def __init__(
        self,
        *,
        ways: WaysPages,
        planner: TripPlanner | None,
        now: Callable[[], datetime],
    ) -> None:
        self._ways = ways
        self._planner = planner
        self._now = now

    def routes(self) -> list[web.RouteDef]:
        """
        List the routes.

        Returns
        -------
        list[web.RouteDef]
            The suggestions and their script.
        """
        return [
            web.get('/pins/ways/suggest', self._suggest),
            web.get('/suggest.js', self._script),
        ]

    def card(self, query: MultiMapping[str]) -> str:
        """
        Draw the controls and the Suggest button with an empty list.

        Parameters
        ----------
        query : MultiMapping[str]
            The page's query; its search fields are kept.

        Returns
        -------
        str
            The card's HTML.
        """
        search = search_of(query=query, now=self._now())
        return controls(search=search) + SUGGEST.format(
            suggestions=LIST.format(body=''),
        )

    async def _script(self, request: web.Request) -> web.Response:
        """
        Serve the suggestion script.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The script.
        """
        del request
        return web.Response(
            text=asset(name='suggest.js'), content_type='text/javascript',
        )

    async def _suggest(self, request: web.Request) -> web.Response:
        """
        Ask the Trip Planner once and list the journeys.

        Parameters
        ----------
        request : web.Request
            The incoming request: the ways page's fields, and
            `part=suggestions` for the list alone.

        Returns
        -------
        web.Response
            The list alone, or the ways page holding it.
        """
        query = request.query
        search = search_of(query=query, now=self._now())
        found = await self._listed(query=query, search=search)
        if query.get('part') == 'suggestions':
            return html_page(text=found)
        card = controls(search=search) + SUGGEST.format(suggestions=found)
        return self._ways.page(query=query, card=card)

    async def _listed(
        self, *, query: MultiMapping[str], search: TripSearch,
    ) -> str:
        """
        Make the one Trip Planner call and draw what it gives.

        Parameters
        ----------
        query : MultiMapping[str]
            Fields `from` and `to`.
        search : TripSearch
            When to travel.

        Returns
        -------
        str
            The list, or the failure line.
        """
        options = self._ways.options
        if self._planner is None or options is None:
            return failure_html()
        try:
            journeys = await self._planner.trips(
                origin_id=query.get('from', ''),
                destination_id=query.get('to', ''),
                search=search,
            )
        except PlannerUnavailable:
            return failure_html()
        return list_html(
            options=options, journeys=journeys, arrive=search.arrive,
        )
