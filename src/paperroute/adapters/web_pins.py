"""Serve the Pins page: list, move, reorder and delete pins."""

import asyncio
import html
from collections.abc import Awaitable, Callable
from dataclasses import replace
from http import HTTPStatus
from typing import Final

from aiohttp import web
from multidict import MultiDict

from paperroute.adapters.pin_editor import edited
from paperroute.adapters.pin_windows import (
    WindowRow,
    query_rows,
    rows_of,
    start_rows,
    windows_fieldset,
    windows_of,
)
from paperroute.adapters.web_common import asset, form_fields
from paperroute.adapters.web_pin_edit import (
    MISFIT,
    draft_of,
    edit_page,
    editor_form,
    origin_name,
    search_results,
)
from paperroute.core.messages import (
    Event,
    PinDeleted,
    PinMoved,
    PinSaved,
    PinsOrdered,
)
from paperroute.core.model import Pin, pin_ref, same_place
from paperroute.core.pin_choices import PinDraft, PinEditor
from paperroute.observers import PinsSeen
from paperroute.ports import PinOptions

__all__ = [
    'PinsPages',
    'draft_of',
    'editor_form',
    'pin_label',
    'pin_line',
    'search_results',
]

SEEN_WAIT_S: Final[float] = 1.0  # longest a page waits for a new report
SEEN_POLL_S: Final[float] = 0.05  # gap between looks for a new report
NOTICES: Final[dict[str, str]] = {
    'saved': 'Pin saved.',
    'deleted': 'Pin deleted.',
    'moved': 'Pins reordered.',
    'gone': 'That pin changed on the device. Here are the pins now.',
    'duplicate': 'That pin already exists.',
}
EMPTY: Final[str] = (
    '<p>No pins yet. Search for a stop or station to add one.</p>'
)
STARTING: Final[str] = '<p>Starting up. Reload in a moment.</p>'
NOW: Final[str] = '<span class="now">On the screen now</span>'
MISSING: Final[str] = (
    '<span class="missing">Stop not in the new timetable</span>'
)
ITEM: Final[str] = """
      <li data-ref="{ref}">
        <span class="grip" aria-hidden="true">⋮⋮</span>
        <div class="pin">
          <b>{label}</b>
          <small>{line}</small>{flags}
        </div>
        <div class="actions">
{up}
{down}
          <a href="/pins/edit?ref={ref}" aria-label="Edit {label}">Edit</a>
          <form method="post" action="/pins/delete">
            <input type="hidden" name="ref" value="{ref}">
            <button type="submit" aria-label="Delete {label}"
                    data-label="{label}">Delete</button>
          </form>
        </div>
      </li>"""
MOVE: Final[str] = """          <form method="post" action="/pins/move">
            <input type="hidden" name="ref" value="{ref}">
            <input type="hidden" name="steps" value="{steps}">
            <button type="submit"
                    aria-label="Move {label} {name}">{name_cap}</button>
          </form>"""
CONFIRM: Final[str] = """<form method="post" action="/pins/delete">
      <p>Delete “{label}”?</p>
      <input type="hidden" name="ref" value="{ref}">
      <input type="hidden" name="confirm" value="yes">
      <a href="/pins">Cancel</a>
      <button type="submit">Delete</button>
    </form>"""


def pin_label(*, pin: Pin) -> str:
    """
    Name a pin for the list.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    str
        The nickname, else the From name; not escaped.
    """
    return pin.nickname or pin.origin.name


def pin_line(*, pin: Pin) -> str:
    """
    Describe where a pin goes.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    str
        The From, then the To after an arrow when there is one, then
        the routes after a dot; not escaped.
    """
    line = pin.origin.name
    if pin.destination is not None:
        line += f' → {pin.destination.name}'
    routes = pin.only or pin.routes
    return f'{line} · {" ".join(routes)}' if routes else line


def whole_number(*, text: str) -> int | None:
    """
    Read a whole number.

    Parameters
    ----------
    text : str
        The text.

    Returns
    -------
    int | None
        The number, or None if the text is not one.
    """
    try:
        return int(text)
    except ValueError:
        return None


def move_form(*, pin: Pin, steps: int) -> str:
    """
    Draw the Up or Down button of a pin.

    Parameters
    ----------
    pin : Pin
        The pin.
    steps : int
        -1 for Up, 1 for Down.

    Returns
    -------
    str
        A form that posts the move.
    """
    name = 'up' if steps < 0 else 'down'
    return MOVE.format(
        ref=pin_ref(pin=pin),
        steps=steps,
        label=html.escape(pin_label(pin=pin)),
        name=name,
        name_cap=name.capitalize(),
    )


def pin_item(*, pin: Pin, shown: bool) -> str:
    """
    Draw one pin of the list.

    Parameters
    ----------
    pin : Pin
        The pin.
    shown : bool
        Whether the pin is on the screen now.

    Returns
    -------
    str
        An escaped list item.
    """
    flags = ''.join(
        f'\n          {flag}'
        for flag, on in ((NOW, shown), (MISSING, pin.missing))
        if on
    )
    return ITEM.format(
        ref=pin_ref(pin=pin),
        label=html.escape(pin_label(pin=pin)),
        line=html.escape(pin_line(pin=pin)),
        flags=flags,
        up=move_form(pin=pin, steps=-1),
        down=move_form(pin=pin, steps=1),
    )


def pins_list(*, seen: PinsSeen) -> str:
    """
    Draw the pins in knob order.

    Parameters
    ----------
    seen : PinsSeen
        The pins the app reported.

    Returns
    -------
    str
        An escaped ordered list, or a line saying there are none.
    """
    if not seen.pins:
        return EMPTY
    items = ''.join(
        pin_item(pin=pin, shown=index == seen.shown)
        for index, pin in enumerate(seen.pins)
    )
    return f'<ol class="pins">{items}\n    </ol>'


def page_html(*, notice: str, body: str) -> str:
    """
    Fill in the Pins page.

    Parameters
    ----------
    notice : str
        The notice's HTML, or an empty string.
    body : str
        The HTML of the list, or of a confirmation.

    Returns
    -------
    str
        The page.
    """
    return (
        asset(name='pins.html')
        .replace('{{notice}}', notice)
        .replace('{{pins}}', body)
    )


def pin_draft(*, pin: Pin, ref: str) -> PinDraft:
    """
    Start an editor's choices from a saved pin.

    Parameters
    ----------
    pin : Pin
        The pin.
    ref : str
        Its ref.

    Returns
    -------
    PinDraft
        The pin's choices.
    """
    return PinDraft(
        stop=pin.origin.stop_id,
        kind=pin.kind,
        route=pin.routes[0] if pin.routes else '',
        to='' if pin.destination is None else pin.destination.stop_id,
        only=pin.only,
        nickname=pin.nickname,
        ref=ref,
    )


def survived(*, draft: PinDraft, editor: PinEditor) -> bool:
    """
    Say whether every choice made makes a pin unchanged.

    Parameters
    ----------
    draft : PinDraft
        The choices submitted.
    editor : PinEditor
        The editor after checking them.

    Returns
    -------
    bool
        True when there is a pin and no choice was dropped or changed.
    """
    checked = editor.draft
    fields = (draft.stop, draft.kind, draft.route, draft.to)
    return (
        editor.pin is not None
        and fields == (checked.stop, checked.kind, checked.route, checked.to)
        and set(draft.only) == set(checked.only)
    )


def duplicate(*, seen: PinsSeen, pin: Pin, ref: str | None) -> bool:
    """
    Say whether a pin is the same place as another pin.

    Parameters
    ----------
    seen : PinsSeen
        The pins the app reported.
    pin : Pin
        The pin to save.
    ref : str | None
        The ref of the pin it replaces, or None for a new pin.

    Returns
    -------
    bool
        True when another pin shows the same services.
    """
    return any(
        same_place(first=pin, second=other)
        for other in seen.pins
        if pin_ref(pin=other) != ref
    )


def notice_alert(*, text: str) -> str:
    """
    Draw a refusal notice.

    Parameters
    ----------
    text : str
        The message; not escaped.

    Returns
    -------
    str
        A paragraph read out as an alert.
    """
    return f'<p class="notice" role="alert">{text}</p>'


def html_page(*, text: str, status: int = HTTPStatus.OK) -> web.Response:
    """
    Answer with a page.

    Parameters
    ----------
    text : str
        The page.
    status : int
        HTTP status.

    Returns
    -------
    web.Response
        The response.
    """
    return web.Response(text=text, status=status, content_type='text/html')


def notice_html(*, done: str | None) -> str:
    """
    Draw the notice for an outcome.

    Parameters
    ----------
    done : str | None
        The `done` query value.

    Returns
    -------
    str
        A paragraph, or an empty string for an unknown outcome.
    """
    text = NOTICES.get(done or '')
    if text is None:
        return ''
    return f'<p class="notice" role="status">{html.escape(text)}</p>'


class PinsPages:
    """
    The Pins page and its form posts.

    Parameters
    ----------
    post : Callable[[Event], None]
        Sends an event to the app.
    pins : Callable[[], PinsSeen | None]
        Gives the pins the app last reported, or None before the first.
    options : PinOptions | None
        The timetable's choices for the editor, or None.
    sleep : Callable[[float], Awaitable[None]]
        Waits for some seconds.
    """

    def __init__(
        self,
        *,
        post: Callable[[Event], None],
        pins: Callable[[], PinsSeen | None],
        options: PinOptions | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._post = post
        self._pins = pins
        self.options = options
        self._sleep = sleep

    def routes(self) -> list[web.RouteDef]:
        """
        List the routes.

        Returns
        -------
        list[web.RouteDef]
            The page, its script and the form posts.
        """
        return [
            web.get('/pins', self._page),
            web.get('/pins.js', self._script),
            web.post('/pins/move', self._move),
            web.post('/pins/order', self._order),
            web.post('/pins/delete', self._delete),
            web.get('/pins/new', self._search),
            web.get('/pins/edit', self._edit),
            web.post('/pins/save', self._save),
        ]

    async def _wait(self, *, seen: str | None) -> None:
        """
        Wait for a report newer than a revision.

        Parameters
        ----------
        seen : str | None
            The `seen` query value; ignored unless it is a number.
        """
        if seen is None or not seen.isdecimal():
            return
        for _ in range(round(SEEN_WAIT_S / SEEN_POLL_S)):
            latest = self._pins()
            if latest is None or latest.revision > int(seen):
                return
            await self._sleep(SEEN_POLL_S)

    async def _page(self, request: web.Request) -> web.Response:
        """
        Serve the list.

        Parameters
        ----------
        request : web.Request
            The incoming request; `seen` and `done` in the query.

        Returns
        -------
        web.Response
            The page.
        """
        await self._wait(seen=request.query.get('seen'))
        latest = self._pins()
        body = STARTING if latest is None else pins_list(seen=latest)
        notice = notice_html(done=request.query.get('done'))
        return web.Response(
            text=page_html(notice=notice, body=body),
            content_type='text/html',
        )

    async def _script(self, request: web.Request) -> web.Response:
        """
        Serve the page's script.

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
            text=asset(name='pins.js'),
            content_type='text/javascript',
        )

    def _find(self, *, ref: str) -> tuple[PinsSeen, Pin] | None:
        """
        Find a pin by ref in the latest report.

        Parameters
        ----------
        ref : str
            The pin's ref.

        Returns
        -------
        tuple[PinsSeen, Pin] | None
            The report and the pin, or None if there is no such pin.
        """
        latest = self._pins()
        if latest is None:
            return None
        for pin in latest.pins:
            if pin_ref(pin=pin) == ref:
                return latest, pin
        return None

    def _finish(
        self, *, event: Event | None, revision: int, done: str
    ) -> web.HTTPSeeOther:
        """
        Post an event and point the browser back at the list.

        Parameters
        ----------
        event : Event | None
            The event, or None when the pin has gone.
        revision : int
            The revision read before posting.
        done : str
            The outcome for the notice.

        Returns
        -------
        web.HTTPSeeOther
            A redirect to wait for the app's report, or to the gone
            notice.
        """
        if event is None:
            return web.HTTPSeeOther(location='/pins?done=gone')
        self._post(event)
        return web.HTTPSeeOther(
            location=f'/pins?seen={revision}&done={done}',
        )

    async def _move(self, request: web.Request) -> web.Response:
        """
        Move a pin one place along the knob order.

        Parameters
        ----------
        request : web.Request
            The incoming form post; `ref` and `steps`.

        Returns
        -------
        web.Response
            400 for bad fields; otherwise a 303 back to the list.
        """
        fields = await form_fields(request=request)
        ref = fields.get('ref')
        steps = whole_number(text=fields.get('steps', ''))
        if not ref or steps is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        latest = self._pins()
        if latest is None:
            raise web.HTTPSeeOther(location='/pins')
        gone = self._find(ref=ref) is None
        event = None if gone else PinMoved(ref=ref, steps=steps)
        raise self._finish(
            event=event, revision=latest.revision, done='moved',
        )

    async def _order(self, request: web.Request) -> web.Response:
        """
        Put the pins in a new order.

        Parameters
        ----------
        request : web.Request
            The incoming form post; `refs`, comma separated.

        Returns
        -------
        web.Response
            400 for a missing field; otherwise a 303 back to the list.
        """
        refs = (await form_fields(request=request)).get('refs')
        if refs is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        latest = self._pins()
        if latest is None:
            raise web.HTTPSeeOther(location='/pins')
        wanted = tuple(ref for ref in refs.split(',') if ref)
        current = {pin_ref(pin=pin) for pin in latest.pins}
        same = len(wanted) == len(current) and set(wanted) == current
        event = PinsOrdered(refs=wanted) if same else None
        raise self._finish(
            event=event, revision=latest.revision, done='moved',
        )

    async def _delete(self, request: web.Request) -> web.Response:
        """
        Ask before deleting a pin, then delete it.

        Parameters
        ----------
        request : web.Request
            The incoming form post; `ref`, and `confirm` once asked.

        Returns
        -------
        web.Response
            400 for a missing ref; the confirmation page without
            `confirm=yes`; otherwise a 303 back to the list.
        """
        fields = await form_fields(request=request)
        ref = fields.get('ref')
        if not ref:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        found = self._find(ref=ref)
        if found is None:
            raise web.HTTPSeeOther(location='/pins?done=gone')
        latest, pin = found
        if fields.get('confirm') != 'yes':
            body = CONFIRM.format(
                label=html.escape(pin_label(pin=pin)), ref=ref,
            )
            return web.Response(
                text=page_html(notice='', body=body),
                content_type='text/html',
            )
        raise self._finish(
            event=PinDeleted(ref=ref),
            revision=latest.revision,
            done='deleted',
        )

    async def _search(self, request: web.Request) -> web.Response:
        """
        Search for a stop or station.

        Parameters
        ----------
        request : web.Request
            The incoming request; `q` in the query.

        Returns
        -------
        web.Response
            The results page.
        """
        text = request.query.get('q', '')
        if self.options is None:
            return html_page(text=edit_page(
                title='Add a pin', notice='', body=STARTING,
            ))
        found = self.options.search(text=text)
        return html_page(text=edit_page(
            title='Add a pin',
            notice='',
            body=search_results(text=text, found=found),
        ))

    def _editor(
        self, *, draft: PinDraft, rows: tuple[WindowRow, ...] | None,
        notice: str = '', status: int = HTTPStatus.OK,
    ) -> web.Response:
        """
        Draw the editor for some choices.

        Parameters
        ----------
        draft : PinDraft
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
            The editor page; a 303 to the gone notice when the ref is
            not a pin.
        """
        found = None if draft.ref is None else self._find(ref=draft.ref)
        if draft.ref is not None and found is None:
            raise web.HTTPSeeOther(location='/pins?done=gone')
        pin = None if found is None else found[1]
        if pin is not None and draft.ref and not draft.stop:
            draft = pin_draft(pin=pin, ref=draft.ref)
        body = self._editor_body(
            draft=draft, pin=pin, rows=start_rows(rows=rows, pin=pin),
        )
        title = 'Add a pin' if draft.ref is None else 'Edit pin'
        return html_page(
            text=edit_page(title=title, notice=notice, body=body),
            status=status,
        )

    def _editor_body(
        self, *, draft: PinDraft, pin: Pin | None,
        rows: tuple[WindowRow, ...],
    ) -> str:
        """
        Draw the editor form for some choices.

        Parameters
        ----------
        draft : PinDraft
            The choices made.
        pin : Pin | None
            The pin being edited, or None for a new pin.
        rows : tuple[WindowRow, ...]
            The window rows.

        Returns
        -------
        str
            The form's HTML, or a line saying the timetable is not
            ready.
        """
        if self.options is None:
            return STARTING
        missing = pin if pin is not None and pin.missing else None
        editor = edited(options=self.options, draft=draft, missing=missing)
        return editor_form(
            editor=editor,
            origin=origin_name(
                editor=editor,
                fallback=None if pin is None else pin.origin,
            ),
            windows=windows_fieldset(rows=rows),
        )

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
        draft = draft_of(
            query=request.query, only=request.query.getall('only', []),
        )
        return self._editor(
            draft=draft, rows=query_rows(fields=request.query),
        )

    async def _save(self, request: web.Request) -> web.Response:
        """
        Save a pin.

        Parameters
        ----------
        request : web.Request
            The incoming form post; the choices.

        Returns
        -------
        web.Response
            400 with the editor when a choice is not offered; otherwise
            a 303 back to the list.
        """
        body = await request.post()
        fields = MultiDict(
            (name, value)
            for name, value in body.items()
            if isinstance(value, str)
        )
        draft = draft_of(query=fields, only=fields.getall('only', []))
        return self._checked(draft=draft, rows=rows_of(fields=fields))

    def _checked(
        self, *, draft: PinDraft, rows: tuple[WindowRow, ...],
    ) -> web.Response:
        """
        Check a pin's choices and windows, then save it.

        Parameters
        ----------
        draft : PinDraft
            The choices submitted.
        rows : tuple[WindowRow, ...]
            The window rows submitted.

        Returns
        -------
        web.Response
            400 with the editor when a choice is not offered or a window
            is refused.

        Raises
        ------
        web.HTTPSeeOther
            Back to the list once saved, or when nothing is known yet.
        """
        latest = self._pins()
        if latest is None or self.options is None:
            raise web.HTTPSeeOther(location='/pins')
        editor = edited(
            options=self.options, draft=draft,
            missing=self._missing(ref=draft.ref),
        )
        if not survived(draft=draft, editor=editor) or editor.pin is None:
            return self._refused(draft=draft, editor=editor, rows=rows)
        windows = windows_of(rows=rows)
        if isinstance(windows, str):
            return self._editor(
                draft=draft, rows=rows, notice=notice_alert(text=windows),
                status=HTTPStatus.BAD_REQUEST,
            )
        pin = replace(editor.pin, windows=windows)
        raise self._saved(seen=latest, ref=draft.ref, pin=pin)

    def _missing(self, *, ref: str | None) -> Pin | None:
        """
        Find a pin the timetable no longer knows.

        Parameters
        ----------
        ref : str | None
            A pin ref, or None.

        Returns
        -------
        Pin | None
            The pin when it has the ref and is missing; otherwise None.
        """
        found = None if ref is None else self._find(ref=ref)
        return found[1] if found is not None and found[1].missing else None

    def _refused(
        self, *, draft: PinDraft, editor: PinEditor,
        rows: tuple[WindowRow, ...],
    ) -> web.Response:
        """
        Answer a save whose choices do not make a pin.

        Parameters
        ----------
        draft : PinDraft
            The choices submitted.
        editor : PinEditor
            The editor after checking them.
        rows : tuple[WindowRow, ...]
            The window rows submitted.

        Returns
        -------
        web.Response
            The editor with its stops nearby when a missing pin has
            some; otherwise 400 with the editor and a notice.
        """
        if editor.nearby and editor.pin is None:
            return self._editor(draft=draft, rows=rows)
        return self._editor(
            draft=draft, rows=rows, notice=notice_alert(text=MISFIT),
            status=HTTPStatus.BAD_REQUEST,
        )

    def _saved(
        self, *, seen: PinsSeen, ref: str | None, pin: Pin,
    ) -> web.HTTPSeeOther:
        """
        Post a checked pin unless it is gone or a duplicate.

        Parameters
        ----------
        seen : PinsSeen
            The pins the app last reported.
        ref : str | None
            The ref of the pin it replaces, or None for a new pin.
        pin : Pin
            The pin.

        Returns
        -------
        web.HTTPSeeOther
            A redirect to the list with the outcome.
        """
        gone = ref is not None and self._find(ref=ref) is None
        if not gone and duplicate(seen=seen, pin=pin, ref=ref):
            return web.HTTPSeeOther(location='/pins?done=duplicate')
        event = None if gone else PinSaved(ref=ref, pin=pin)
        return self._finish(
            event=event, revision=seen.revision, done='saved',
        )
