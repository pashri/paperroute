"""Serve the ways page of a pin with a Destination."""

import html
from collections.abc import Callable
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Final
from urllib.parse import urlencode

from aiohttp import web
from multidict import MultiDict, MultiMapping

from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_web import board_text
from paperroute_transit.adapters.pin_windows import (
    DEFAULT_BUTTON,
    WindowRow,
    query_rows,
    rows_of,
    start_rows,
    windows_fieldset,
    windows_of,
)
from paperroute_transit.adapters.ways_editor import (
    WaysDraft,
    draft_pin,
    encode_way,
    leg_routes,
    never_choices,
    ways_draft,
)
from paperroute_transit.adapters.web_pin_edit import (
    MIN_QUERY,
    MISFIT,
    NO_MATCH,
    delete_form,
    edit_page,
    option,
)
from paperroute_transit.adapters.web_pin_list import pin_label
from paperroute_transit.adapters.web_pin_search import STARTING, STOPS_SHOWN
from paperroute_transit.adapters.web_pins import (
    PinsPages,
    html_page,
    notice_alert,
)
from paperroute_transit.core.connection_model import way_legs
from paperroute_transit.core.model import Pin, StopPlace, Way
from paperroute_transit.core.pin_choices import NICKNAME_MAX
from paperroute_transit.observers import PinsSeen
from paperroute_transit.ports import PinOptions

ADD_TITLE: Final[str] = 'Add a connection'
EDIT_TITLE: Final[str] = 'Edit pin'
NEVER_HINT: Final[str] = 'Routes ticked here are never used on any leg.'
ARROW: Final[str] = '\u2192'
FORM: Final[str] = """<form class="editor ways" method="get"
          action="/pins/ways">
      {default}
      <input type="hidden" name="ref" value="{ref}">
      <fieldset class="from-to">
        <legend>From and to</legend>{origin}{destination}{card}
      </fieldset>
      <fieldset>
        <legend>Ways to go</legend>{ways}
      </fieldset>
      <p><button type="submit" name="hand" class="secondary"
                 formaction="/pins/ways/hand">Add a way by hand</button></p>
      <fieldset>
        <legend>Never use</legend>
        <p class="hint">{never_hint}</p>{never}
      </fieldset>
      <p>
        <label for="pin-nickname">Nickname</label>
        <input id="pin-nickname" type="text" name="nickname"
               value="{nickname}" maxlength="{max}">
      </p>{windows}
      <p class="buttons">
        <button type="submit" class="secondary">Show choices</button>
        <button type="submit" formmethod="post"
                formaction="/pins/ways/save">Save</button>
      </p>
    </form>"""
PICK_STOP: Final[str] = 'Pick a stop'
NO_WAYS: Final[str] = (
    'Pick From and To, then suggest with Trip Planner or add a way by hand.'
)
STOP: Final[str] = """
        <p>
          <label for="pin-{name}">{label}</label>
          <select id="pin-{name}" name="{name}" data-stop-search
                  placeholder="{prompt}">{options}
          </select>
        </p>
        <noscript>
          <p>
            <label for="pin-{name}-q">Search {label}</label>
            <input id="pin-{name}-q" type="search" name="{name}_q"
                   value="{text}">
          </p>{note}
        </noscript>"""
NOTE: Final[str] = """
          <p class="hint">{text}</p>"""
WAY: Final[str] = """
        <label class="way"><input type="checkbox" name="way"
               value="{index}"{checked}> {label}</label>
        <input type="hidden" name="ways" value="{value}">"""
NEVER: Final[str] = """
        <label><input type="checkbox" name="never"
               value="{route}"{checked}> {route}</label>"""
SUGGEST: Final[str] = """
        <p>
          <button type="submit" class="secondary"
                  formaction="/pins/ways/suggest"
                  data-suggest>Suggest with Trip Planner</button>
        </p>{suggestions}"""
EMPTY_LIST: Final[str] = """
        <div class="suggestions" aria-live="polite" data-suggestions></div>"""

type CardOf = Callable[[MultiMapping[str]], str]


async def posted_fields(*, request: web.Request) -> MultiMapping[str]:
    """
    Read the text fields of a form post, keeping repeated names.

    Parameters
    ----------
    request : web.Request
        The incoming form post.

    Returns
    -------
    MultiMapping[str]
        Every text field, in order.
    """
    posted = await request.post()
    return MultiDict([
        (name, value)
        for name, value in posted.items()
        if isinstance(value, str)
    ])


@dataclass(frozen=True)
class StopPick:
    """
    One stop field: what is chosen and what a typed search found.

    Attributes
    ----------
    name : str
        Field name, `from` or `to`.
    label : str
        Its label.
    place : StopPlace | None
        The stop chosen, or None.
    text : str
        The search typed without scripts; '' for none.
    found : tuple[StopPlace, ...]
        What the search found.
    """

    name: str
    label: str
    place: StopPlace | None
    text: str = ''
    found: tuple[StopPlace, ...] = ()


def stop_options(*, pick: StopPick) -> str:
    """
    Draw a stop select's options.

    Parameters
    ----------
    pick : StopPick
        The field.

    Returns
    -------
    str
        A prompt when nothing is chosen, then the chosen stop and the
        stops found.
    """
    chosen = '' if pick.place is None else pick.place.stop_id
    places = [] if pick.place is None else [pick.place]
    places += [place for place in pick.found if place.stop_id != chosen]
    prompt = option(value='', label=PICK_STOP, chosen=chosen)
    return (prompt if pick.place is None else '') + ''.join(
        option(value=place.stop_id, label=place.name, chosen=chosen)
        for place in places
    )


def search_note(*, pick: StopPick) -> str:
    """
    Say when a typed search found nothing.

    Parameters
    ----------
    pick : StopPick
        The field.

    Returns
    -------
    str
        A hint, or an empty string.
    """
    if len(pick.text.strip()) < MIN_QUERY or pick.found:
        return ''
    return NOTE.format(
        text=NO_MATCH.format(text=html.escape(pick.text.strip())),
    )


def stop_field(*, pick: StopPick) -> str:
    """
    Draw a stop select that the stop search script can take over.

    Without scripts, a search box under it fills the select on the next
    Show choices.

    Parameters
    ----------
    pick : StopPick
        The field.

    Returns
    -------
    str
        The select and its search box.
    """
    return STOP.format(
        name=pick.name, label=pick.label, prompt=PICK_STOP,
        options=stop_options(pick=pick), text=html.escape(pick.text),
        note=search_note(pick=pick),
    )


def stop_pick(
    *, options: PinOptions, query: MultiMapping[str], name: str,
    place: StopPlace | None,
) -> StopPick:
    """
    Read one stop field from the page's query.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    query : MultiMapping[str]
        The page's fields.
    name : str
        Field name, `from` or `to`.
    place : StopPlace | None
        The stop chosen, or None.

    Returns
    -------
    StopPick
        The field, with the stops its `<name>_q` search found.
    """
    text = query.get(f'{name}_q', '')
    matches = () if len(text.strip()) < MIN_QUERY else options.search(
        text=text,
    )
    return StopPick(
        name=name, label='From' if name == 'from' else 'To', place=place,
        text=text,
        found=tuple(match.place for match in matches[:STOPS_SHOWN]),
    )


def chips(*, routes: tuple[str, ...]) -> str:
    """
    Draw a leg's route chips.

    Parameters
    ----------
    routes : tuple[str, ...]
        Route short names.

    Returns
    -------
    str
        One chip per route; a question mark when there are none.
    """
    return ''.join(
        f'<span class="chip">{html.escape(route)}</span>'
        for route in routes or ('?',)
    )


def way_label(*, options: PinOptions, pin: Pin, way: Way) -> str:
    """
    Describe a way: its chips per leg, where it changes, its allows.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin
        The pin.
    way : Way
        One of its ways.

    Returns
    -------
    str
        Escaped HTML, such as chips, `via Hammers` and `7 min`.
    """
    arrow = f' <span aria-hidden="true">{ARROW}</span> '
    legs = arrow.join(
        chips(routes=leg_routes(options=options, leg=leg))
        for leg in way_legs(pin=pin, way=way)
    )
    via = ', '.join(change.board.name for change in way.changes)
    allows = ', '.join(f'{change.allow} min' for change in way.changes)
    return f'{legs} \u00b7 via {html.escape(via)} \u00b7 {allows}'


def ways_boxes(*, options: PinOptions, pin: Pin) -> str:
    """
    Draw a checkbox per way, with the way itself in a hidden field.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin
        The pin.

    Returns
    -------
    str
        The checkboxes, ticked for ticked ways.
    """
    return ''.join(
        WAY.format(
            index=index,
            checked=' checked' if way.ticked else '',
            label=way_label(options=options, pin=pin, way=way),
            value=html.escape(encode_way(way=way)),
        )
        for index, way in enumerate(pin.ways)
    )


def never_boxes(*, options: PinOptions, pin: Pin) -> str:
    """
    Draw a checkbox per route that can be never used.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin
        The pin.

    Returns
    -------
    str
        The checkboxes, ticked for routes in the never list.
    """
    return ''.join(
        NEVER.format(
            route=html.escape(route),
            checked=' checked' if route in pin.never else '',
        )
        for route in never_choices(options=options, pin=pin)
    )


def hand_location(*, query: MultiMapping[str]) -> str:
    """
    Address the hand way editor, coming back to the page as edited.

    Parameters
    ----------
    query : MultiMapping[str]
        The ways page's fields as submitted, with `hand`.

    Returns
    -------
    str
        The editor's address with the From and To; `back` holds every
        field but `hand`.
    """
    back = urlencode([
        (name, value) for name, value in query.items() if name != 'hand'
    ])
    fields = [
        ('stop', query.get('from', '')), ('to', query.get('to', '')),
        ('back', back),
    ]
    return f'/pins/connect?{urlencode(fields)}'


def ways_form(
    *, options: PinOptions, pin: Pin | None, query: MultiMapping[str],
    rows: tuple[WindowRow, ...], card: str,
) -> str:
    """
    Draw the ways page's form.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin | None
        The pin, with a Destination; None until From and To are known.
    query : MultiMapping[str]
        The page's fields.
    rows : tuple[WindowRow, ...]
        The window rows.
    card : str
        The Trip Planner controls and suggestions.

    Returns
    -------
    str
        Escaped HTML of the form.
    """
    draft = ways_draft(query=query)
    origin, destination = stop_places(options=options, pin=pin, draft=draft)
    ways = '' if pin is None else ways_boxes(options=options, pin=pin)
    return FORM.format(
        default=DEFAULT_BUTTON,
        ref=html.escape(draft.ref or ''),
        origin=stop_field(pick=stop_pick(
            options=options, query=query, name='from', place=origin,
        )),
        destination=stop_field(pick=stop_pick(
            options=options, query=query, name='to', place=destination,
        )),
        card=card,
        ways=ways or NOTE.format(text=NO_WAYS),
        never_hint=html.escape(NEVER_HINT),
        never='' if pin is None else never_boxes(options=options, pin=pin),
        nickname=html.escape(
            (draft.nickname if pin is None else pin.nickname) or '',
        ),
        max=NICKNAME_MAX,
        windows=windows_fieldset(rows=rows),
    )


def stop_places(
    *, options: PinOptions, pin: Pin | None, draft: WaysDraft,
) -> tuple[StopPlace | None, StopPlace | None]:
    """
    Find the From and To the page shows.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin | None
        The pin, or None.
    draft : WaysDraft
        The choices made.

    Returns
    -------
    tuple[StopPlace | None, StopPlace | None]
        The pin's From and To; else the draft's, each None when not
        known.
    """
    if pin is not None:
        return pin.origin, pin.destination
    return (
        options.place(stop_id=draft.origin),
        options.place(stop_id=draft.destination),
    )


def plain_card(query: MultiMapping[str]) -> str:
    """
    Draw the Suggest button with an empty suggestion list.

    Parameters
    ----------
    query : MultiMapping[str]
        The page's query; not used.

    Returns
    -------
    str
        The button and the list.
    """
    del query
    return SUGGEST.format(suggestions=EMPTY_LIST)


class WaysPages:
    """
    The ways page and its save.

    Parameters
    ----------
    pages : PinsPages
        The Pins pages, for finding and saving pins.
    pins : Callable[[], PinsSeen | None]
        Gives the pins the app last reported, or None before the first.
    options : PinOptions | None
        The timetable's choices, or None.
    card : CardOf
        Draws the Trip Planner controls for a query.
    head : BoardHead
        The extra stylesheets and scripts each page links, and its nav.
    """

    def __init__(
        self,
        *,
        pages: PinsPages,
        pins: Callable[[], PinsSeen | None],
        options: PinOptions | None,
        card: CardOf = plain_card,
        head: BoardHead = BoardHead(),
    ) -> None:
        self._pages = pages
        self._pins = pins
        self.options = options
        self.card = card
        self.head = head

    def routes(self) -> list[web.RouteDef]:
        """
        List the routes.

        Returns
        -------
        list[web.RouteDef]
            The page, its save and its script.
        """
        return [
            web.get('/pins/ways', self._edit),
            web.post('/pins/ways/save', self._save),
            web.get('/pins/ways/hand', self._hand),
            web.get('/ways.js', self._script),
        ]

    async def _script(self, request: web.Request) -> web.Response:
        """
        Serve the step script of the hand way editor.

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
            text=board_text(name='ways.js'), content_type='text/javascript',
        )

    async def _hand(self, request: web.Request) -> web.Response:
        """
        Go to the hand way editor with the page's fields as edited.

        Parameters
        ----------
        request : web.Request
            The incoming request; the ways page's fields in the query.

        Raises
        ------
        web.HTTPSeeOther
            Always, to the hand way editor.
        """
        raise web.HTTPSeeOther(location=hand_location(query=request.query))

    async def _edit(self, request: web.Request) -> web.Response:
        """
        Serve the ways page.

        Parameters
        ----------
        request : web.Request
            The incoming request; `ref`, or the choices, in the query.

        Returns
        -------
        web.Response
            The page.
        """
        return self.page(query=request.query, card=self.card(request.query))

    def page(
        self, *, query: MultiMapping[str], card: str, notice: str = '',
        status: int = HTTPStatus.OK,
    ) -> web.Response:
        """
        Draw the ways page for some choices.

        Parameters
        ----------
        query : MultiMapping[str]
            The choices, or just `ref` to start from a saved pin.
        card : str
            The Trip Planner controls and suggestions.
        notice : str
            The notice's HTML, or an empty string.
        status : int
            HTTP status.

        Returns
        -------
        web.Response
            The page; From and To left to pick when they are not known.

        Raises
        ------
        web.HTTPSeeOther
            To the gone notice when the ref is not a pin.
        """
        draft = ways_draft(query=query)
        base = self._base(ref=draft.ref)
        title = EDIT_TITLE if draft.ref else ADD_TITLE
        if self.options is None:
            return html_page(text=edit_page(
                title=title, notice=notice, body=STARTING, head=self.head,
            ), status=status)
        pin = self._pin(
            options=self.options, draft=draft, base=base,
            started='from' in query,
        )
        rows = start_rows(rows=query_rows(fields=query), pin=base)
        body = ways_form(
            options=self.options, pin=pin, query=query, rows=rows, card=card,
        )
        danger = ''
        if base is not None and draft.ref:
            danger = delete_form(ref=draft.ref, label=pin_label(pin=base))
        return html_page(
            text=edit_page(
                title=title, notice=notice, body=body, danger=danger,
                head=self.head,
            ),
            status=status,
        )

    def _base(self, *, ref: str | None) -> Pin | None:
        """
        Find the saved pin being edited.

        Parameters
        ----------
        ref : str | None
            Its ref, or None for a new pin.

        Returns
        -------
        Pin | None
            The pin, or None for a new pin.

        Raises
        ------
        web.HTTPSeeOther
            To the gone notice when the ref is not a pin.
        """
        if ref is None:
            return None
        found = self._pages.find(ref=ref)
        if found is None:
            raise web.HTTPSeeOther(location='/pins?done=gone')
        return found[1]

    @staticmethod
    def _pin(
        *, options: PinOptions, draft: WaysDraft, base: Pin | None,
        started: bool,
    ) -> Pin | None:
        """
        Work out the pin the page shows.

        Parameters
        ----------
        options : PinOptions
            What the timetable offers.
        draft : WaysDraft
            The choices made.
        base : Pin | None
            The saved pin, or None.
        started : bool
            True when the query holds the choices, not just the ref.

        Returns
        -------
        Pin | None
            The pin; the saved pin before any choice; None when a stop
            is unknown.
        """
        if not started and base is not None and base.destination:
            return base
        return draft_pin(options=options, draft=draft, base=base)

    async def _save(self, request: web.Request) -> web.Response:
        """
        Save the pin with its ways and never list.

        Parameters
        ----------
        request : web.Request
            The incoming form post; the choices.

        Returns
        -------
        web.Response
            400 with the page and a notice when a stop or a window is
            refused.

        Raises
        ------
        web.HTTPSeeOther
            To the list: once posted, or before the app or timetable is
            ready.
        """
        fields = await posted_fields(request=request)
        latest = self._pins()
        if latest is None or self.options is None:
            raise web.HTTPSeeOther(location='/pins')
        draft = ways_draft(query=fields)
        pin = draft_pin(
            options=self.options, draft=draft,
            base=self._base(ref=draft.ref),
        )
        windows = windows_of(rows=rows_of(fields=fields))
        if pin is None or isinstance(windows, str):
            return self.page(
                query=fields, card=self.card(fields),
                notice=notice_alert(
                    text=MISFIT if pin is None else str(windows),
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
        raise self._pages.saved(
            seen=latest, ref=draft.ref, pin=replace(pin, windows=windows),
        )
