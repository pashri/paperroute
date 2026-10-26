"""Serve the connection editor: build a two-leg pin step by step."""

import html
from collections.abc import Callable
from dataclasses import replace
from http import HTTPStatus
from typing import Final

from aiohttp import web
from multidict import MultiDict

from paperroute.adapters.connection_editor import (
    ALLOW_RANGE,
    ConnectionDraft,
    ConnectionEditor,
    connection_draft,
    connection_draft_of,
    connection_edited,
)
from paperroute.adapters.pin_windows import (
    DEFAULT_BUTTON,
    WindowRow,
    query_rows,
    rows_of,
    start_rows,
    windows_fieldset,
    windows_of,
)
from paperroute.adapters.web_pin_edit import (
    MISFIT,
    delete_form,
    edit_page,
    kind_fields,
    line_field,
    option,
    select_field,
)
from paperroute.adapters.web_pins import (
    STARTING,
    PinsPages,
    html_page,
    notice_alert,
    pin_label,
)
from paperroute.core.model import Pin
from paperroute.core.pin_choices import NICKNAME_MAX
from paperroute.observers import PinsSeen
from paperroute.ports import PinOptions

ADD_TITLE: Final[str] = 'Add a connection'
EDIT_TITLE: Final[str] = 'Edit connection'
PICK_STOP: Final[str] = 'Pick a stop'
ON_HINT: Final[str] = 'Stops within 500 m of where you get off, nearest first.'
ALLOW_HINT: Final[str] = (
    'Worked out from the {metres} m walk. Change it if you need longer.'
)
ROUTES_HINT: Final[str] = (
    'Any route that gets there: {routes}. Tick some to keep only those.'
)
FORM: Final[str] = """<form class="editor" method="get" action="/pins/connect">
      {default}
      <input type="hidden" name="ref" value="{ref}">
      <input type="hidden" name="stop" value="{stop}">
      <input type="hidden" name="allow_for" value="{allow_for}">
      <h3>{origin}</h3>{legs}
      <p>
        <label for="pin-nickname">Nickname</label>
        <input id="pin-nickname" type="text" name="nickname"
               value="{nickname}" maxlength="{max}">
      </p>{windows}{buttons}
    </form>"""
BUTTONS: Final[str] = """
      <p class="buttons">
        <button type="submit" class="secondary">Show choices</button>{save}
      </p>"""
SAVE: Final[str] = """
        <button type="submit" formmethod="post"
                formaction="/pins/connect/save">Save connection</button>"""
LEG: Final[str] = """
      <fieldset>
        <legend>{legend}</legend>{fields}
      </fieldset>"""
ALLOW: Final[str] = """
      <p>
        <label for="pin-allow">Allow (minutes)</label>
        <input id="pin-allow" type="number" min="1" max="60" step="1"
               name="allow" value="{allow}">
      </p>"""
HINT: Final[str] = """
      <p class="hint">{text}</p>"""
ROUTES: Final[str] = """
      <fieldset>
        <legend>Routes</legend>
        <p class="hint">{hint}</p>{boxes}
      </fieldset>"""


def place_select(
    *, name: str, label: str, places: list[tuple[str, str]], chosen: str,
) -> str:
    """
    Draw a select of places with a prompt first.

    Parameters
    ----------
    name : str
        Field name.
    label : str
        Its label.
    places : list[tuple[str, str]]
        Each place's ID and label.
    chosen : str
        The ID selected.

    Returns
    -------
    str
        A paragraph holding the select.
    """
    options = option(value='', label=PICK_STOP, chosen=chosen) + ''.join(
        option(value=value, label=text, chosen=chosen)
        for value, text in places
    )
    return select_field(name=name, label=label, options=options)


def first_fieldset(*, editor: ConnectionEditor) -> str:
    """
    Draw leg 1: the mode, the route and where to get off.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        A fieldset.
    """
    leg = editor.leg
    fields = kind_fields(editor=leg) + line_field(editor=leg)
    if leg.draft.route:
        fields += place_select(
            name='off',
            label='Get off at',
            places=[(p.stop_id, p.name) for p in leg.destinations],
            chosen=editor.draft.off,
        )
    return LEG.format(legend='Leg 1', fields=fields)


def change_fieldset(*, editor: ConnectionEditor) -> str:
    """
    Draw the change: where to get on, and the minutes allowed.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        A fieldset, or an empty string before the stop to get off at.
    """
    draft = editor.draft
    if not draft.off:
        return ''
    fields = place_select(
        name='on',
        label='Get on at',
        places=[
            (stop.place.stop_id, f'{stop.place.name} · {stop.metres} m')
            for stop in editor.changes
        ],
        chosen=draft.on,
    ) + HINT.format(text=html.escape(ON_HINT))
    if editor.walk is not None:
        fields += ALLOW.format(allow=html.escape(draft.allow))
        fields += HINT.format(text=ALLOW_HINT.format(metres=editor.walk))
    return LEG.format(legend='Change', fields=fields)


def routes_fieldset(*, editor: ConnectionEditor) -> str:
    """
    Draw the leg 2 route checkboxes.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        A fieldset, or an empty string when no route is offered.
    """
    if not editor.routes:
        return ''
    boxes = ''.join(
        '\n        <label><input type="checkbox" name="only2" '
        f'value="{html.escape(route)}"'
        f'{" checked" if route in editor.draft.only else ""}> '
        f'{html.escape(route)}</label>'
        for route in editor.routes
    )
    hint = ROUTES_HINT.format(routes=', '.join(editor.routes))
    return ROUTES.format(hint=html.escape(hint), boxes=boxes)


def second_fieldset(*, editor: ConnectionEditor) -> str:
    """
    Draw leg 2: the To and its routes.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        A fieldset, or an empty string before the stop to get on at.
    """
    if not editor.draft.on:
        return ''
    fields = place_select(
        name='to',
        label='To',
        places=[(place.stop_id, place.name) for place in editor.places],
        chosen=editor.draft.to,
    ) + routes_fieldset(editor=editor)
    return LEG.format(legend='Leg 2', fields=fields)


def connection_form(*, editor: ConnectionEditor, windows: str) -> str:
    """
    Draw the connection editor.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.
    windows : str
        The Show automatically fieldset's HTML.

    Returns
    -------
    str
        Escaped HTML of the form.
    """
    draft = editor.draft
    origin = editor.leg.origin
    legs = (
        first_fieldset(editor=editor) + change_fieldset(editor=editor)
        + second_fieldset(editor=editor)
    )
    return FORM.format(
        default=DEFAULT_BUTTON,
        ref=html.escape(draft.ref or ''),
        stop=html.escape(draft.stop),
        allow_for=html.escape(draft.allow_for),
        origin=html.escape(PICK_STOP if origin is None else origin.name),
        legs=legs if origin is not None else '',
        max=NICKNAME_MAX,
        nickname=html.escape(draft.nickname or ''),
        windows=windows,
        buttons=BUTTONS.format(save=SAVE if editor.pin is not None else ''),
    )


def survived(*, draft: ConnectionDraft, editor: ConnectionEditor) -> bool:
    """
    Say whether every choice submitted is still offered.

    Parameters
    ----------
    draft : ConnectionDraft
        The choices submitted.
    editor : ConnectionEditor
        The editor after checking them.

    Returns
    -------
    bool
        True when no choice was dropped or changed; an allowance left
        empty may take the default.
    """
    checked = editor.draft
    fields = ('stop', 'kind', 'route', 'off', 'on', 'to')
    allow = draft.allow.strip()
    return (
        all(getattr(draft, name) == getattr(checked, name) for name in fields)
        and set(draft.only) == set(checked.only)
        and (not allow or allow == checked.allow)
    )


class ConnectionPages:
    """
    The connection editor and its save.

    Parameters
    ----------
    pages : PinsPages
        The Pins pages, for finding and saving pins.
    pins : Callable[[], PinsSeen | None]
        Gives the pins the app last reported, or None before the first.
    options : PinOptions | None
        The timetable's choices for the editor, or None.
    """

    def __init__(
        self,
        *,
        pages: PinsPages,
        pins: Callable[[], PinsSeen | None],
        options: PinOptions | None,
    ) -> None:
        self._pages = pages
        self._pins = pins
        self._options = options

    def routes(self) -> list[web.RouteDef]:
        """
        List the routes.

        Returns
        -------
        list[web.RouteDef]
            The editor and its save.
        """
        return [
            web.get('/pins/connect', self._edit),
            web.post('/pins/connect/save', self._save),
        ]

    async def _edit(self, request: web.Request) -> web.Response:
        """
        Serve the editor.

        Parameters
        ----------
        request : web.Request
            The incoming request; the choices in the query.

        Returns
        -------
        web.Response
            The editor page, or a 303 when the pin has gone.
        """
        return self._page(
            draft=connection_draft(query=request.query),
            rows=query_rows(fields=request.query),
        )

    def _page(
        self, *, draft: ConnectionDraft, rows: tuple[WindowRow, ...] | None,
        notice: str = '', status: int = HTTPStatus.OK,
    ) -> web.Response:
        """
        Draw the editor page for some choices.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices made.
        rows : tuple[WindowRow, ...] | None
            The window rows, or None to start from the pin's windows.
        notice : str
            The notice's HTML, or an empty string.
        status : int
            HTTP status.

        Returns
        -------
        web.Response
            The page; a 303 to the gone notice when the ref is not a pin.
        """
        found = None if draft.ref is None else self._pages.find(ref=draft.ref)
        if draft.ref is not None and found is None:
            raise web.HTTPSeeOther(location='/pins?done=gone')
        pin = None if found is None else found[1]
        if pin is not None and draft.ref and not draft.stop:
            draft = connection_draft_of(pin=pin, ref=draft.ref)
        body = self._body(draft=draft, rows=start_rows(rows=rows, pin=pin))
        if pin is not None and draft.ref:
            body += delete_form(ref=draft.ref, label=pin_label(pin=pin))
        title = ADD_TITLE if draft.ref is None else EDIT_TITLE
        return html_page(
            text=edit_page(title=title, notice=notice, body=body),
            status=status,
        )

    def _body(
        self, *, draft: ConnectionDraft, rows: tuple[WindowRow, ...],
    ) -> str:
        """
        Draw the editor form for some choices.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices made.
        rows : tuple[WindowRow, ...]
            The window rows.

        Returns
        -------
        str
            The form, or a line saying the timetable is not ready.
        """
        if self._options is None:
            return STARTING
        return connection_form(
            editor=connection_edited(options=self._options, draft=draft),
            windows=windows_fieldset(rows=rows),
        )

    async def _save(self, request: web.Request) -> web.Response:
        """
        Save a connection.

        Parameters
        ----------
        request : web.Request
            The incoming form post; the choices.

        Returns
        -------
        web.Response
            400 with the editor and a notice when the choices, the
            allowance or a window are refused.

        Raises
        ------
        web.HTTPSeeOther
            To the list: once posted, or before the app or timetable is
            ready.
        """
        posted = await request.post()
        fields = MultiDict([
            (name, value)
            for name, value in posted.items()
            if isinstance(value, str)
        ])
        draft = connection_draft(query=fields)
        rows = rows_of(fields=fields)
        latest = self._pins()
        if latest is None or self._options is None:
            raise web.HTTPSeeOther(location='/pins')
        editor = connection_edited(options=self._options, draft=draft)
        checked = self._checked(draft=draft, editor=editor, rows=rows)
        if isinstance(checked, web.Response):
            return checked
        windows = windows_of(rows=rows)
        if isinstance(windows, str):
            return self._refused(draft=draft, rows=rows, text=windows)
        pin = replace(checked, windows=windows)
        raise self._pages.saved(seen=latest, ref=draft.ref, pin=pin)

    def _checked(
        self, *, draft: ConnectionDraft, editor: ConnectionEditor,
        rows: tuple[WindowRow, ...],
    ) -> Pin | web.Response:
        """
        Check that submitted choices make a connection.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices submitted.
        editor : ConnectionEditor
            The editor after checking them.
        rows : tuple[WindowRow, ...]
            The window rows submitted.

        Returns
        -------
        Pin | web.Response
            The pin; else 400 with `MISFIT` when a choice was dropped, or
            with `ALLOW_RANGE` when only the allowance is wrong.
        """
        if not survived(draft=draft, editor=editor):
            return self._refused(draft=draft, rows=rows, text=MISFIT)
        if editor.pin is None:
            return self._refused(draft=draft, rows=rows, text=ALLOW_RANGE)
        return editor.pin

    def _refused(
        self, *, draft: ConnectionDraft, rows: tuple[WindowRow, ...],
        text: str,
    ) -> web.Response:
        """
        Answer a refused save with the editor and a notice.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices submitted.
        rows : tuple[WindowRow, ...]
            The window rows submitted.
        text : str
            The notice; not escaped.

        Returns
        -------
        web.Response
            400 with the editor.
        """
        return self._page(
            draft=draft, rows=rows, notice=notice_alert(text=text),
            status=HTTPStatus.BAD_REQUEST,
        )
