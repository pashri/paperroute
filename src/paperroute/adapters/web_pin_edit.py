"""Draw the pin search results and the pin editor."""

import html
from collections.abc import Mapping, Sequence
from typing import Final
from urllib.parse import urlencode

from paperroute.adapters.pin_windows import DEFAULT_BUTTON
from paperroute.adapters.web_common import asset
from paperroute.core.pin_choices import (
    KIND_NAMES,
    NICKNAME_MAX,
    PinDraft,
    PinEditor,
    StopMatch,
)
from paperroute_transit.core.model import STATION_FIRST, RouteKind, StopPlace

MIN_QUERY: Final[int] = 2
TOO_SHORT: Final[str] = 'Type at least 2 letters.'
NO_MATCH: Final[str] = 'No stop or station matches “{text}”.'
MISFIT: Final[str] = "Those choices don't fit together. Pick again."
NO_TO: Final[str] = 'None: every departure'
FILTER_HINT: Final[str] = 'Leave all unticked to show every route.'
STRIP_HINT: Final[str] = (
    'With no Destination the stop lists every route; this sets the Strip'
    ' and the Destinations offered.'
)
SEARCH_FORM: Final[str] = """<search>
    <form class="search" method="get" action="/pins/new">
      <label for="pin-search">Search a stop or station</label>
      <input id="pin-search" type="search" name="q" value="{text}">{extra}
      <button type="submit">Search</button>
    </form>
    </search>"""
EDITOR: Final[str] = """<form class="editor" method="get" action="/pins/edit">
      {default}
      <input type="hidden" name="ref" value="{ref}">
      <input type="hidden" name="stop" value="{stop}">
      <h3>{origin}</h3>{fields}
      <p>
        <label for="pin-nickname">Nickname</label>
        <input id="pin-nickname" type="text" name="nickname"
               maxlength="{max}" value="{nickname}">
      </p>{windows}
      <p class="buttons">
        <button type="submit" class="secondary">Show choices</button>{save}
      </p>
    </form>{nearby}"""
SAVE: Final[str] = """
        <button type="submit" formmethod="post"
                formaction="/pins/save">Save</button>"""

DANGER_CARD: Final[str] = """
    <section class="card danger-zone" aria-labelledby="delete-heading">
      <h2 id="delete-heading">Delete this pin</h2>
      <p>It disappears from the panel and this list. This can't be undone.</p>
      <form class="delete" method="post" action="/pins/delete">
        <input type="hidden" name="ref" value="{ref}">
        <button type="submit" class="danger"
                data-label="{label}">Delete pin</button>
      </form>
    </section>"""


def draft_of(
    *, query: Mapping[str, str], only: Sequence[str],
) -> PinDraft:
    """
    Read an editor's choices from form fields.

    Parameters
    ----------
    query : Mapping[str, str]
        Fields: `ref`, `stop`, `kind`, `route`, `to` and `nickname`.
    only : Sequence[str]
        Every `only` field given.

    Returns
    -------
    PinDraft
        The draft; an unknown kind becomes None.
    """
    kind = query.get('kind', '')
    return PinDraft(
        stop=query.get('stop', ''),
        kind=RouteKind(kind) if kind in RouteKind else None,
        route=query.get('route', ''),
        to=query.get('to', ''),
        only=tuple(only),
        nickname=query.get('nickname') or None,
        ref=query.get('ref') or None,
    )


def match_item(*, match: StopMatch, target: str = '/pins/edit') -> str:
    """
    Draw one search result.

    Parameters
    ----------
    match : StopMatch
        The stop found.
    target : str
        The editor the result opens.

    Returns
    -------
    str
        An escaped list item linking to the stop's editor.
    """
    kinds = ', '.join(KIND_NAMES[kind] for kind in match.kinds)
    routes = (
        f' <span class="routes">{html.escape(" ".join(match.routes))}</span>'
        if match.routes
        else ''
    )
    link = f'{target}?' + urlencode({'stop': match.place.stop_id})
    return (
        f'\n      <li><a href="{html.escape(link)}">'
        f'<b>{html.escape(match.place.name)}</b> '
        f'<small>{html.escape(kinds)}</small>{routes}</a></li>'
    )


def search_results(
    *,
    text: str,
    found: Sequence[StopMatch],
    target: str = '/pins/edit',
    extra: str = '',
) -> str:
    """
    Draw the search form and what it found.

    Parameters
    ----------
    text : str
        The text searched for.
    found : Sequence[StopMatch]
        The matches.
    target : str
        The editor each result opens.
    extra : str
        Markup added to the form, such as hidden inputs; not escaped.

    Returns
    -------
    str
        Escaped HTML.
    """
    form = SEARCH_FORM.format(text=html.escape(text), extra=extra)
    if len(text.strip()) < MIN_QUERY:
        return f'{form}\n    <p>{TOO_SHORT}</p>'
    if not found:
        line = NO_MATCH.format(text=html.escape(text.strip()))
        return f'{form}\n    <p>{line}</p>'
    items = ''.join(
        match_item(match=match, target=target) for match in found
    )
    return f'{form}\n    <ul class="results">{items}\n    </ul>'


def option(*, value: str, label: str, chosen: str) -> str:
    """
    Draw one option of a select.

    Parameters
    ----------
    value : str
        The option's value.
    label : str
        The option's text.
    chosen : str
        The value selected.

    Returns
    -------
    str
        An escaped option.
    """
    selected = ' selected' if value == chosen else ''
    return (
        f'\n          <option value="{html.escape(value)}"{selected}>'
        f'{html.escape(label)}</option>'
    )


def kind_fields(*, editor: PinEditor) -> str:
    """
    Draw the Mode radios.

    Parameters
    ----------
    editor : PinEditor
        The editor.

    Returns
    -------
    str
        A fieldset, or an empty string when no kind is offered.
    """
    if not editor.kinds:
        return ''
    radios = ''.join(
        f'\n        <label><input type="radio" name="kind" value="{kind}"'
        f'{" checked" if kind is editor.draft.kind else ""}> '
        f'{html.escape(KIND_NAMES[kind])}</label>'
        for kind in editor.kinds
    )
    return (
        '\n      <fieldset>\n        <legend>Mode</legend>'
        f'{radios}\n      </fieldset>'
    )


def select_field(
    *, name: str, label: str, options: str, hint: str | None = None,
) -> str:
    """
    Draw a labelled select.

    Parameters
    ----------
    name : str
        Field name.
    label : str
        Its label.
    options : str
        The option elements.
    hint : str | None
        Escaped text describing the select, shown after it; None for
        none.

    Returns
    -------
    str
        A paragraph holding the select, then the hint's paragraph.
    """
    hint_id = f'pin-{name}-hint'
    described = '' if hint is None else f' aria-describedby="{hint_id}"'
    after = '' if hint is None else (
        f'\n      <p id="{hint_id}" class="hint">{hint}</p>'
    )
    return (
        f'\n      <p>\n        <label for="pin-{name}">{label}</label>'
        f'\n        <select id="pin-{name}" name="{name}"{described}>'
        f'{options}\n        </select>\n      </p>{after}'
    )


def line_field(*, editor: PinEditor) -> str:
    """
    Draw the Line or Route select.

    With no To the stop lists every route, so the select is labelled as
    the Strip's and says so.

    Parameters
    ----------
    editor : PinEditor
        The editor.

    Returns
    -------
    str
        A select, or an empty string when no line is offered.
    """
    if not editor.lines:
        return ''
    label = 'Line' if editor.draft.kind in STATION_FIRST else 'Route'
    open_ended = not editor.draft.to
    chosen = editor.draft.route
    options = option(value='', label=f'Pick a {label.lower()}', chosen=chosen)
    options += ''.join(
        option(
            value=line.badge,
            label=(
                line.badge
                if line.label == line.badge
                else f'{line.badge} {line.label}'
            ),
            chosen=chosen,
        )
        for line in editor.lines
    )
    return select_field(
        name='route',
        label=f'Strip {label.lower()}' if open_ended else label,
        options=options,
        hint=STRIP_HINT if open_ended else None,
    )


def to_field(*, editor: PinEditor) -> str:
    """
    Draw the Destination select.

    Parameters
    ----------
    editor : PinEditor
        The editor.

    Returns
    -------
    str
        A select, or an empty string before a line is chosen.
    """
    if not editor.draft.route:
        return ''
    chosen = editor.draft.to
    options = option(value='', label=NO_TO, chosen=chosen) + ''.join(
        option(value=place.stop_id, label=place.name, chosen=chosen)
        for place in editor.destinations
    )
    return select_field(name='to', label='Destination', options=options)


def filter_fields(*, editor: PinEditor) -> str:
    """
    Draw the Routes checkboxes.

    Parameters
    ----------
    editor : PinEditor
        The editor.

    Returns
    -------
    str
        A fieldset, or an empty string when no route is offered.
    """
    if not editor.filters:
        return ''
    boxes = ''.join(
        '\n        <label><input type="checkbox" name="only" '
        f'value="{html.escape(route)}"'
        f'{" checked" if route in editor.draft.only else ""}> '
        f'{html.escape(route)}</label>'
        for route in editor.filters
    )
    return (
        '\n      <fieldset>\n        <legend>Routes</legend>'
        f'\n        <p>{FILTER_HINT}</p>{boxes}\n      </fieldset>'
    )


def nearby_item(*, ref: str, place: StopPlace) -> str:
    """
    Draw a link that restarts the editor from a nearby stop.

    Parameters
    ----------
    ref : str
        The missing pin's ref.
    place : StopPlace
        The nearby stop.

    Returns
    -------
    str
        An escaped list item.
    """
    link = '/pins/edit?' + urlencode({'ref': ref, 'stop': place.stop_id})
    return (
        f'\n        <li><a href="{html.escape(link)}">'
        f'{html.escape(place.name)}</a></li>'
    )


def nearby_list(*, editor: PinEditor) -> str:
    """
    Draw the stops near a missing pin.

    Parameters
    ----------
    editor : PinEditor
        The editor.

    Returns
    -------
    str
        A section of links, or an empty string when there are none.
    """
    if not editor.nearby:
        return ''
    items = ''.join(
        nearby_item(ref=editor.draft.ref or '', place=place)
        for place in editor.nearby
    )
    return (
        '\n    <section aria-labelledby="nearby">'
        '\n      <h3 id="nearby">Stops nearby</h3>'
        f'\n      <ul>{items}\n      </ul>\n    </section>'
    )


def editor_form(*, editor: PinEditor, origin: str, windows: str) -> str:
    """
    Draw the pin editor.

    Parameters
    ----------
    editor : PinEditor
        The editor.
    origin : str
        The From name for the heading; not escaped.
    windows : str
        The Show automatically fieldset's HTML.

    Returns
    -------
    str
        Escaped HTML of the form, and the stops nearby when there are
        any.
    """
    draft = editor.draft
    fields = (
        kind_fields(editor=editor) + line_field(editor=editor)
        + to_field(editor=editor) + filter_fields(editor=editor)
    )
    return EDITOR.format(
        default=DEFAULT_BUTTON,
        windows=windows,
        ref=html.escape(draft.ref or ''),
        stop=html.escape(draft.stop),
        origin=html.escape(origin),
        fields=fields,
        max=NICKNAME_MAX,
        nickname=html.escape(draft.nickname or ''),
        save=SAVE if editor.pin is not None else '',
        nearby=nearby_list(editor=editor),
    )


def delete_form(*, ref: str, label: str) -> str:
    """
    Draw the Delete card for a saved pin.

    Parameters
    ----------
    ref : str
        The pin's ref.
    label : str
        The pin's name; not escaped.

    Returns
    -------
    str
        A card whose form posts to the delete confirmation.
    """
    return DANGER_CARD.format(
        ref=html.escape(ref),
        label=html.escape(label),
    )


def edit_page(
    *, title: str, notice: str, body: str, danger: str = '',
) -> str:
    """
    Fill in the search and editor page.

    Parameters
    ----------
    title : str
        Page title; not escaped.
    notice : str
        The notice's HTML, or an empty string.
    body : str
        The page's HTML.
    danger : str
        The Delete card's HTML after the editor's card, or an empty
        string.

    Returns
    -------
    str
        The page.
    """
    return (
        asset(name='pin-edit.html')
        .replace('{{title}}', html.escape(title))
        .replace('{{notice}}', notice)
        .replace('{{body}}', body)
        .replace('{{danger}}', danger)
    )


def origin_name(*, editor: PinEditor, fallback: StopPlace | None) -> str:
    """
    Name the From for the editor's heading.

    Parameters
    ----------
    editor : PinEditor
        The editor.
    fallback : StopPlace | None
        A missing pin's old From, or None.

    Returns
    -------
    str
        The From's name, the old From's name, or a prompt.
    """
    place = editor.origin or fallback
    return 'Pick a stop' if place is None else place.name
