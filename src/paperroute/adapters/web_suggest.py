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
from paperroute.core.trip_search import (
    Journey,
    PlannerBusy,
    PlannerUnavailable,
    TripSearch,
    default_search,
)
from paperroute.planner_port import TripPlanner
from paperroute.ports import PinOptions
from paperroute_transit.core.model import MAX_CHANGES, SYDNEY, StopPlace

FAILURE: Final[str] = "Trip Planner didn't answer. Try again in a minute."
BUSY: Final[str] = 'Trip Planner is busy. Try again in a minute.'
PICK_FIRST: Final[str] = 'Pick From and To first.'
FROM_TO: Final[tuple[str, str]] = ('from', 'to')
NO_TRIPS: Final[str] = 'No trips found.'
NOT_YET: Final[str] = 'not supported yet'
SCHOOL_TAG: Final[str] = 'school bus'
DIRECT_NOTE: Final[str] = 'Direct trips always show on the panel.'
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
    school : bool
        True when it is greyed for riding a school bus.
    """

    journey: Journey
    boards: tuple[str, ...]
    pick: str | None
    greyed: bool
    school: bool = False


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
        Earlier arrives by one minute before `first`; Later leaves one
        minute after `last`; else the search unchanged. Each pages against
        the direction it moves, so it never asks for the same journeys.
    """
    page = query.get('page', '')
    if page not in PAGES:
        return search
    try:
        edge = datetime.fromisoformat(query.get(PAGES[page], ''))
    except ValueError:
        return search
    earlier = page == 'earlier'
    when = edge - STEP if earlier else edge + STEP
    return TripSearch(when=when.astimezone(SYDNEY), arrive=earlier)


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


def school_row(*, row: SuggestionRow, skip_school: bool) -> SuggestionRow:
    """
    Grey a row that rides a school bus when they are left out.

    Parameters
    ----------
    row : SuggestionRow
        The row.
    skip_school : bool
        Whether school buses are left out.

    Returns
    -------
    SuggestionRow
        The row greyed and unpickable when it is left out; else as given.
    """
    if not skip_school or not any(leg.school for leg in row.journey.legs):
        return row
    return replace(row, pick=None, greyed=True, school=True)


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

    A direct journey has no change to pick, so it is greyed with a note
    that direct trips always show.

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
        return GREYED.format(
            text=text, tag=SCHOOL_TAG if row.school else NOT_YET,
        )
    if row.pick is None:
        return GREYED.format(text=text, tag=DIRECT_NOTE)
    return PICK.format(pick=html.escape(row.pick), text=text)


def list_html(
    *,
    options: PinOptions,
    journeys: tuple[Journey, ...],
    skip_school: bool = True,
) -> str:
    """
    Draw the suggestion list.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    journeys : tuple[Journey, ...]
        The journeys found.
    skip_school : bool
        Whether journeys on a school bus are greyed.

    Returns
    -------
    str
        The list with Earlier and Later, or the empty line.
    """
    if not journeys:
        return LIST.format(body=f'\n          <p>{NO_TRIPS}</p>')
    rows = ''.join(
        row_html(row=school_row(
            row=row_of(options=options, journey=journey),
            skip_school=skip_school,
        ))
        for journey in journeys
    )
    first = min(journey.legs[-1].arrives for journey in journeys)
    last = max(journey.legs[0].departs for journey in journeys)
    body = EDGES.format(first=first.isoformat(), last=last.isoformat())
    body += f'\n          <ol class="journeys">{rows}\n          </ol>'
    return LIST.format(body=body + PAGE_BUTTONS)


def failure_html(*, line: str = FAILURE) -> str:
    """
    Draw the line shown when the Trip Planner did not answer.

    Parameters
    ----------
    line : str
        The line to show.

    Returns
    -------
    str
        The list holding only the failure line.
    """
    text = html.escape(line, quote=False)
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
    skip_school : Callable[[], bool]
        Says whether journeys on a school bus are greyed.
    """

    def __init__(
        self,
        *,
        ways: WaysPages,
        planner: TripPlanner | None,
        now: Callable[[], datetime],
        skip_school: Callable[[], bool] = lambda: True,
    ) -> None:
        self._ways = ways
        self._planner = planner
        self._now = now
        self._skip_school = skip_school

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
            The list, or the failure line; a line asking for From and To
            when either is not known.
        """
        options = self._ways.options
        if self._planner is None or options is None:
            return failure_html()
        stops = [
            options.place(stop_id=query.get(name, '')) for name in FROM_TO
        ]
        if None in stops:
            return failure_html(line=PICK_FIRST)
        try:
            journeys = await self._planner.trips(
                origin_id=query.get('from', ''),
                destination_id=query.get('to', ''),
                search=search,
            )
        except PlannerBusy:
            return failure_html(line=BUSY)
        except PlannerUnavailable:
            return failure_html()
        return list_html(
            options=options,
            journeys=journeys,
            skip_school=self._skip_school(),
        )
