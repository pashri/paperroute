"""Serve the hand way editor: build one way step by step."""

import html
from collections.abc import Callable
from http import HTTPStatus
from typing import Final
from urllib.parse import parse_qsl, urlencode

from aiohttp import web
from multidict import MultiDict

from paperroute.adapters.board_head import BoardHead
from paperroute.adapters.connection_editor import (
    ALLOW_RANGE,
    ChangeStep,
    ConnectionDraft,
    ConnectionEditor,
    connection_draft,
    connection_edited,
)
from paperroute.adapters.pin_windows import DEFAULT_BUTTON
from paperroute.adapters.ways_editor import (
    WaysDraft,
    added_way,
    draft_pin,
    ways_draft,
    ways_query,
)
from paperroute.adapters.web_pin_edit import (
    MISFIT,
    edit_page,
    option,
    select_field,
)
from paperroute.adapters.web_pin_search import STARTING
from paperroute.adapters.web_pins import html_page, notice_alert
from paperroute.adapters.web_ways import posted_fields
from paperroute.observers import PinsSeen
from paperroute_transit.core.model import MAX_CHANGES, Pin, StopPlace
from paperroute_transit.ports import PinOptions

TITLE: Final[str] = 'Add a way by hand'
PICK_STOP: Final[str] = 'Pick a stop'
WAY_FIELDS: Final[frozenset[str]] = frozenset({'ways', 'way'})
ARROW: Final[str] = '\u2192'
ON_HINT: Final[str] = 'Stops within 500 m of where you get off, nearest first.'
ALLOW_HINT: Final[str] = (
    'Worked out from the {metres} m walk. Change it if you need longer.'
)
FORM: Final[str] = """<form class="editor" method="get" action="/pins/connect"
          data-steps>
      {default}
      <input type="hidden" name="back" value="{back}">
      <input type="hidden" name="stop" value="{stop}">
      <input type="hidden" name="to" value="{to}">
      <input type="hidden" name="changes" value="{changes}">
      <h3>{origin} {arrow} {target}</h3>{steps}{buttons}
    </form>"""
STEPS: Final[str] = """
      <fieldset class="steps">
        <legend>Way</legend>{fields}
      </fieldset>"""
BUTTONS: Final[str] = """
      <p class="buttons">
        <button type="submit" class="secondary">Show choices</button>{more}{save}
      </p>"""
MORE: Final[str] = """
        <button type="submit" name="more" value="1"
                class="secondary">Add another change</button>"""
SAVE: Final[str] = """
        <button type="submit" formmethod="post"
                formaction="/pins/connect/save">Add this way</button>"""
LEG: Final[str] = """
      <fieldset>
        <legend>{legend}</legend>{fields}
      </fieldset>"""
ALLOW: Final[str] = """
      <input type="hidden" name="allow{n}_for" value="{allow_for}">
      <p>
        <label for="pin-allow{n}">Allow (minutes)</label>
        <input id="pin-allow{n}" type="number" min="1" max="60" step="1"
               name="allow{n}" value="{allow}">
      </p>"""
HINT: Final[str] = """
      <p class="hint">{text}</p>"""
LAST: Final[str] = """
      <p>Then on to {to}.</p>"""
UNREACHED: Final[str] = 'No ride from {on} reaches {to}. Pick another stop.'



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
    Draw leg 1: where to get off, on any route.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        A fieldset.
    """
    fields = place_select(
        name='off',
        label='Get off at',
        places=stop_choices(places=editor.firsts),
        chosen=editor.draft.first.off,
    )
    return LEG.format(legend='Leg 1', fields=fields)


def change_fieldset(
    *, step: ChangeStep, chosen: str, suffix: str, legend: str,
) -> str:
    """
    Draw a change: where to get on, and the minutes allowed.

    Parameters
    ----------
    step : ChangeStep
        The checked change.
    chosen : str
        The stop chosen to get on at.
    suffix : str
        '' for the first change, '2' for the second.
    legend : str
        The fieldset's legend.

    Returns
    -------
    str
        A fieldset.
    """
    fields = place_select(
        name=f'on{suffix}',
        label='Get on at',
        places=[
            (stop.place.stop_id, f'{stop.place.name} · {stop.metres} m')
            for stop in step.stops
        ],
        chosen=chosen,
    ) + HINT.format(text=html.escape(ON_HINT))
    if step.on is not None:
        fields += ALLOW.format(
            n=suffix, allow=html.escape(step.allow),
            allow_for=html.escape(step.on.place.stop_id),
        )
        fields += HINT.format(text=ALLOW_HINT.format(metres=step.on.metres))
    return LEG.format(legend=legend, fields=fields)


def stop_choices(*, places: tuple[StopPlace, ...]) -> list[tuple[str, str]]:
    """
    List places as select choices.

    Parameters
    ----------
    places : tuple[StopPlace, ...]
        Places.

    Returns
    -------
    list[tuple[str, str]]
        Each place's ID and name.
    """
    return [(place.stop_id, place.name) for place in places]


def second_change(*, editor: ConnectionEditor) -> str:
    """
    Draw leg 2 and the second change, when two changes are asked for.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        The fieldsets, or an empty string.
    """
    draft = editor.draft
    if draft.changes != 2 or editor.first.on is None:
        return ''
    fields = LEG.format(legend='Leg 2', fields=place_select(
        name='off2', label='Get off at',
        places=stop_choices(places=editor.offs), chosen=draft.second.off,
    ))
    if not draft.second.off:
        return fields
    return fields + change_fieldset(
        step=editor.second, chosen=draft.second.on, suffix='2',
        legend='Change 2',
    )


def last_fieldset(*, editor: ConnectionEditor) -> str:
    """
    Draw the last leg: on to the ways page's To.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        A fieldset, or an empty string before the last stop got on at.
    """
    step = editor.second if editor.draft.changes == 2 else editor.first
    if not editor.places or step.on is None or editor.target is None:
        return ''
    to = html.escape(editor.target.name)
    text = LAST.format(to=to) if editor.draft.to else HINT.format(
        text=UNREACHED.format(on=html.escape(step.on.place.name), to=to),
    )
    legend = f'Leg {editor.draft.changes + 1}'
    return LEG.format(legend=legend, fields=text)


def steps_fieldset(*, editor: ConnectionEditor) -> str:
    """
    Draw every step offered so far.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.

    Returns
    -------
    str
        The steps fieldset.
    """
    fields = first_fieldset(editor=editor)
    if editor.draft.first.off:
        fields += change_fieldset(
            step=editor.first, chosen=editor.draft.first.on, suffix='',
            legend='Change',
        )
    fields += second_change(editor=editor) + last_fieldset(editor=editor)
    return STEPS.format(fields=fields)


def connection_form(
    *, editor: ConnectionEditor, origin: StopPlace, target: StopPlace,
) -> str:
    """
    Draw the hand way editor.

    Parameters
    ----------
    editor : ConnectionEditor
        The editor.
    origin : StopPlace
        The From.
    target : StopPlace
        The ways page's To.

    Returns
    -------
    str
        Escaped HTML of the form.
    """
    draft = editor.draft
    more = draft.changes < MAX_CHANGES and editor.first.on is not None
    return FORM.format(
        default=DEFAULT_BUTTON,
        back=html.escape(draft.back),
        stop=html.escape(origin.stop_id),
        to=html.escape(target.stop_id),
        changes=draft.changes,
        origin=html.escape(origin.name),
        arrow=ARROW,
        target=html.escape(target.name),
        steps=steps_fieldset(editor=editor),
        buttons=BUTTONS.format(
            more=MORE if more else '',
            save=SAVE if editor.way is not None else '',
        ),
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
    pairs = [(draft.first, checked.first)]
    if draft.changes == 2:
        pairs.append((draft.second, checked.second))
    same = all(
        given.off == kept.off and given.on == kept.on
        and given.allow.strip() in ('', kept.allow)
        for given, kept in pairs
    )
    fields = ('stop', 'to')
    return same and all(
        getattr(draft, name) == getattr(checked, name) for name in fields
    )


def back_draft(*, draft: ConnectionDraft) -> WaysDraft:
    """
    Read the ways page a hand way goes back to.

    Parameters
    ----------
    draft : ConnectionDraft
        The choices made.

    Returns
    -------
    WaysDraft
        The ways page's choices; for a new pin, just its From and To.
    """
    if not draft.back:
        return WaysDraft(origin=draft.stop, destination=draft.to)
    return ways_draft(query=MultiDict(parse_qsl(draft.back)))


def returned(
    *, back: str, pin: Pin, ref: str | None,
) -> list[tuple[str, str]]:
    """
    Write the ways page's fields with a way added.

    Parameters
    ----------
    back : str
        The ways page's fields as it was left, URL-encoded; may be empty.
    pin : Pin
        The pin with the way added.
    ref : str | None
        The saved pin's ref, or None.

    Returns
    -------
    list[tuple[str, str]]
        The page's own fields, unsaved edits and windows among them, with
        its ways and ticks replaced by the pin's; the pin's fields alone
        without `back`.
    """
    fresh = ways_query(pin=pin, ref=ref)
    if not back:
        return fresh
    kept = [
        (name, value)
        for name, value in parse_qsl(back)
        if name not in WAY_FIELDS
    ]
    return kept + [
        (name, value) for name, value in fresh if name in WAY_FIELDS
    ]


class ConnectionPages:
    """
    The hand way editor and its save.

    Parameters
    ----------
    pins : Callable[[], PinsSeen | None]
        Gives the pins the app last reported, or None before the first.
    options : PinOptions | None
        The timetable's choices for the editor, or None.
    head : BoardHead
        The extra stylesheets and scripts each page links, and its nav.
    """

    def __init__(
        self,
        *,
        pins: Callable[[], PinsSeen | None],
        options: PinOptions | None,
        head: BoardHead = BoardHead(),
    ) -> None:
        self._pins = pins
        self._options = options
        self._head = head

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
        Serve the editor, or only its steps for `part=steps`.

        Parameters
        ----------
        request : web.Request
            The incoming request; the choices in the query.

        Returns
        -------
        web.Response
            The page; 400 with a notice past `MAX_CHANGES` changes.
        """
        draft = connection_draft(query=request.query)
        if draft.changes > MAX_CHANGES:
            return self._refused(draft=draft, text=MISFIT)
        if request.query.get('part') == 'steps' and self._options:
            editor = connection_edited(options=self._options, draft=draft)
            return html_page(text=steps_fieldset(editor=editor))
        return self._page(draft=draft)

    def _page(
        self, *, draft: ConnectionDraft, notice: str = '',
        status: int = HTTPStatus.OK,
    ) -> web.Response:
        """
        Draw the editor page for some choices.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices made.
        notice : str
            The notice's HTML, or an empty string.
        status : int
            HTTP status.

        Returns
        -------
        web.Response
            The page: the form, or a line saying the timetable is not
            ready.
        """
        if self._options is None:
            body = STARTING
        else:
            body = self._form(draft=draft, options=self._options)
        return html_page(
            text=edit_page(
                title=TITLE, notice=notice, body=body, head=self._head,
            ),
            status=status,
        )

    @staticmethod
    def _form(*, draft: ConnectionDraft, options: PinOptions) -> str:
        """
        Draw the editor's form.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices made.
        options : PinOptions
            What the timetable offers.

        Returns
        -------
        str
            The form.

        Raises
        ------
        web.HTTPSeeOther
            To the ways page it came from when the From or To is not
            known.
        """
        editor = connection_edited(options=options, draft=draft)
        if editor.origin is None or editor.target is None:
            back = f'?{draft.back}' if draft.back else ''
            raise web.HTTPSeeOther(location=f'/pins/ways{back}')
        return connection_form(
            editor=editor, origin=editor.origin, target=editor.target,
        )

    async def _save(self, request: web.Request) -> web.Response:
        """
        Add the way built to the ways page it came from.

        Parameters
        ----------
        request : web.Request
            The incoming form post; the choices.

        Returns
        -------
        web.Response
            400 with the editor and a notice when the choices or an
            allowance are refused.

        Raises
        ------
        web.HTTPSeeOther
            To the ways page with the way added and ticked; to the list
            before the app or timetable is ready.
        """
        fields = await posted_fields(request=request)
        draft = connection_draft(query=fields)
        if self._pins() is None or self._options is None:
            raise web.HTTPSeeOther(location='/pins')
        location = self._added(draft=draft, options=self._options)
        if location in (MISFIT, ALLOW_RANGE):
            return self._refused(draft=draft, text=location)
        raise web.HTTPSeeOther(location=location)

    @staticmethod
    def _added(*, draft: ConnectionDraft, options: PinOptions) -> str:
        """
        Work out where a checked way goes back to.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices submitted.
        options : PinOptions
            What the timetable offers.

        Returns
        -------
        str
            The ways page's address with the way added; else `MISFIT`
            or `ALLOW_RANGE`.
        """
        editor = connection_edited(options=options, draft=draft)
        if draft.changes > MAX_CHANGES or not survived(
            draft=draft, editor=editor,
        ):
            return MISFIT
        if editor.way is None:
            return ALLOW_RANGE
        back = back_draft(draft=draft)
        pin = draft_pin(options=options, draft=back, base=None)
        if pin is None or (back.origin, back.destination) != (
            draft.stop, draft.to,
        ):
            return MISFIT
        added = added_way(pin=pin, way=editor.way)
        fields = returned(back=draft.back, pin=added, ref=back.ref)
        return f'/pins/ways?{urlencode(fields)}'

    def _refused(self, *, draft: ConnectionDraft, text: str) -> web.Response:
        """
        Answer refused choices with the editor and a notice.

        Parameters
        ----------
        draft : ConnectionDraft
            The choices submitted.
        text : str
            The notice; not escaped.

        Returns
        -------
        web.Response
            400 with the editor.
        """
        return self._page(
            draft=draft, notice=notice_alert(text=text),
            status=HTTPStatus.BAD_REQUEST,
        )
