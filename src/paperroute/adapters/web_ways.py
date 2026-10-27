"""Serve the ways page of a pin with a Destination."""

import html
from collections.abc import Callable
from dataclasses import replace
from http import HTTPStatus
from typing import Final
from urllib.parse import urlencode

from aiohttp import web
from multidict import MultiDict, MultiMapping

from paperroute.adapters.pin_windows import (
    DEFAULT_BUTTON,
    WindowRow,
    query_rows,
    rows_of,
    start_rows,
    windows_fieldset,
    windows_of,
)
from paperroute.adapters.ways_editor import (
    WaysDraft,
    draft_pin,
    encode_way,
    leg_routes,
    never_choices,
    ways_draft,
)
from paperroute.adapters.web_common import asset
from paperroute.adapters.web_pin_edit import (
    MISFIT,
    SEARCH_FORM,
    delete_form,
    edit_page,
)
from paperroute.adapters.web_pin_list import pin_label
from paperroute.adapters.web_pin_search import CONNECT_FIELD, STARTING
from paperroute.adapters.web_pins import PinsPages, html_page, notice_alert
from paperroute.core.connection_model import way_legs
from paperroute.core.model import Pin, StopPlace, Way
from paperroute.core.pin_choices import NICKNAME_MAX
from paperroute.observers import PinsSeen
from paperroute.ports import PinOptions

ADD_TITLE: Final[str] = 'Add a pin'
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
STOP: Final[str] = """
        <p>
          <label for="pin-{name}">{label}</label>
          <select id="pin-{name}" name="{name}" data-stop-search>
            <option value="{value}" selected>{text}</option>
          </select>
        </p>"""
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


def stop_field(*, name: str, label: str, place: StopPlace) -> str:
    """
    Draw a stop select that the stop search script can take over.

    Parameters
    ----------
    name : str
        Field name.
    label : str
        Its label.
    place : StopPlace
        The stop chosen.

    Returns
    -------
    str
        A paragraph with the select holding the chosen stop.
    """
    return STOP.format(
        name=name, label=label, value=html.escape(place.stop_id),
        text=html.escape(place.name),
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
        The editor's address; `back` holds every field but `hand`.
    """
    back = urlencode([
        (name, value) for name, value in query.items() if name != 'hand'
    ])
    fields = [('stop', query.get('from', '')), ('back', back)]
    return f'/pins/connect?{urlencode(fields)}'


def ways_form(
    *, options: PinOptions, pin: Pin, ref: str | None,
    rows: tuple[WindowRow, ...], card: str,
) -> str:
    """
    Draw the ways page's form.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin
        The pin, with a Destination.
    ref : str | None
        The saved pin's ref, or None.
    rows : tuple[WindowRow, ...]
        The window rows.
    card : str
        The Trip Planner controls and suggestions.

    Returns
    -------
    str
        Escaped HTML of the form.
    """
    destination = pin.destination or pin.origin
    return FORM.format(
        default=DEFAULT_BUTTON,
        ref=html.escape(ref or ''),
        origin=stop_field(name='from', label='From', place=pin.origin),
        destination=stop_field(name='to', label='To', place=destination),
        card=card,
        ways=ways_boxes(options=options, pin=pin),
        never_hint=html.escape(NEVER_HINT),
        never=never_boxes(options=options, pin=pin),
        nickname=html.escape(pin.nickname or ''),
        max=NICKNAME_MAX,
        windows=windows_fieldset(rows=rows),
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
    """

    def __init__(
        self,
        *,
        pages: PinsPages,
        pins: Callable[[], PinsSeen | None],
        options: PinOptions | None,
        card: CardOf = plain_card,
    ) -> None:
        self._pages = pages
        self._pins = pins
        self.options = options
        self.card = card

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
            text=asset(name='ways.js'), content_type='text/javascript',
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
            The page; the stop search when there is no pin to show.

        Raises
        ------
        web.HTTPSeeOther
            To the gone notice when the ref is not a pin.
        """
        draft = ways_draft(query=query)
        base = self._base(ref=draft.ref)
        pin = self._pin(draft=draft, base=base, started='from' in query)
        title = EDIT_TITLE if draft.ref else ADD_TITLE
        if self.options is None or pin is None:
            body = STARTING if self.options is None else SEARCH_FORM.format(
                text='', extra=CONNECT_FIELD,
            )
            return html_page(text=edit_page(
                title=title, notice=notice, body=body,
            ), status=status)
        rows = start_rows(rows=query_rows(fields=query), pin=base)
        body = ways_form(
            options=self.options, pin=pin, ref=draft.ref, rows=rows,
            card=card,
        )
        if base is not None and draft.ref:
            body += delete_form(ref=draft.ref, label=pin_label(pin=base))
        return html_page(
            text=edit_page(title=title, notice=notice, body=body),
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

    def _pin(
        self, *, draft: WaysDraft, base: Pin | None, started: bool,
    ) -> Pin | None:
        """
        Work out the pin the page shows.

        Parameters
        ----------
        draft : WaysDraft
            The choices made.
        base : Pin | None
            The saved pin, or None.
        started : bool
            True when the query holds the choices, not just the ref.

        Returns
        -------
        Pin | None
            The pin; the saved pin before any choice; None when the
            timetable is not ready or a stop is unknown.
        """
        if self.options is None:
            return None
        if not started and base is not None and base.destination:
            return base
        return draft_pin(options=self.options, draft=draft, base=base)

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
            text = MISFIT if pin is None else str(windows)
            return self.page(
                query=fields, card=self.card(fields),
                notice=notice_alert(text=text),
                status=HTTPStatus.BAD_REQUEST,
            )
        pin = replace(pin, windows=windows)
        raise self._pages.saved(seen=latest, ref=draft.ref, pin=pin)
