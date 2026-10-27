"""Check the choices made while building a way by hand."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from multidict import MultiMapping

from paperroute.adapters.state_change import ALLOW_MAX, ALLOW_MIN
from paperroute.core.connection_model import default_allow
from paperroute.core.model import Change, StopPlace, Way
from paperroute.core.pin_choices import ChangeStop
from paperroute.ports import PinOptions

ALLOW_RANGE: Final[str] = 'Allow between 1 and 60 minutes.'


@dataclass(frozen=True)
class ChangeDraft:
    """
    The choices made for one change.

    Attributes
    ----------
    off : str
        ID of the stop left; '' for none.
    on : str
        ID of the stop joined; '' for none.
    allow : str
        Minutes allowed, as typed; '' for none.
    allow_for : str
        The `on` that `allow` was set for.
    """

    off: str = ''
    on: str = ''
    allow: str = ''
    allow_for: str = ''


@dataclass(frozen=True)
class ConnectionDraft:
    """
    The choices made so far while building a way.

    Attributes
    ----------
    stop : str
        From stop or station ID; '' for none.
    first : ChangeDraft
        The first change.
    second : ChangeDraft
        The second change; used when `changes` is 2.
    changes : int
        Changes asked for.
    to : str
        ID of the ways page's To; '' for none.
    back : str
        The ways page's query to return to; '' for a new pin.
    """

    stop: str = ''
    first: ChangeDraft = ChangeDraft()
    second: ChangeDraft = ChangeDraft()
    changes: int = 1
    to: str = ''
    back: str = ''


@dataclass(frozen=True)
class ChangeStep:
    """
    One change, checked.

    Attributes
    ----------
    stops : tuple[ChangeStop, ...]
        Stops offered to get on at.
    on : ChangeStop | None
        The stop chosen to get on at, or None.
    allow : str
        The allowance text.
    """

    stops: tuple[ChangeStop, ...] = ()
    on: ChangeStop | None = None
    allow: str = ''


@dataclass(frozen=True)
class ConnectionEditor:
    """
    What the hand way editor shows.

    Attributes
    ----------
    draft : ConnectionDraft
        The choices that are offered; `to` is '' while the last ride does
        not reach it.
    origin : StopPlace | None
        The From, or None when it is not known.
    target : StopPlace | None
        The ways page's To, or None when it is not known.
    firsts : tuple[StopPlace, ...]
        Places offered to get off at after leg 1, on any route.
    first : ChangeStep
        The first change.
    offs : tuple[StopPlace, ...]
        Places offered to get off at for the second change.
    second : ChangeStep
        The second change.
    places : tuple[StopPlace, ...]
        Places the last leg can reach.
    way : Way | None
        The way the choices make; None until every step has a value and
        each allowance is 1 to 60 minutes.
    """

    draft: ConnectionDraft
    origin: StopPlace | None = None
    target: StopPlace | None = None
    firsts: tuple[StopPlace, ...] = ()
    first: ChangeStep = ChangeStep()
    offs: tuple[StopPlace, ...] = ()
    second: ChangeStep = ChangeStep()
    places: tuple[StopPlace, ...] = ()
    way: Way | None = None


def change_draft(*, query: MultiMapping[str], suffix: str) -> ChangeDraft:
    """
    Read one change's fields.

    Parameters
    ----------
    query : MultiMapping[str]
        Fields.
    suffix : str
        '' for the first change, '2' for the second.

    Returns
    -------
    ChangeDraft
        The change's choices.
    """
    return ChangeDraft(
        off=query.get(f'off{suffix}', ''),
        on=query.get(f'on{suffix}', ''),
        allow=query.get(f'allow{suffix}', ''),
        allow_for=query.get(f'allow{suffix}_for', ''),
    )


def connection_draft(*, query: MultiMapping[str]) -> ConnectionDraft:
    """
    Read the hand way editor's choices from form fields.

    Parameters
    ----------
    query : MultiMapping[str]
        Fields: `stop`, `off`, `on`, `allow`,
        `allow_for`, the same with `2` for the second change, `changes`,
        `more` (one change more), `to` and `back`.

    Returns
    -------
    ConnectionDraft
        The draft; an unreadable count becomes 1.
    """
    changes = query.get('changes', '1')
    more = 1 if query.get('more') else 0
    return ConnectionDraft(
        stop=query.get('stop', ''),
        first=change_draft(query=query, suffix=''),
        second=change_draft(query=query, suffix='2'),
        changes=(int(changes) if changes.isdecimal() else 1) + more,
        to=query.get('to', ''),
        back=query.get('back', ''),
    )


def allow_minutes(*, text: str) -> int | None:
    """
    Read an allowance.

    Parameters
    ----------
    text : str
        Minutes as typed.

    Returns
    -------
    int | None
        Whole minutes from 1 to 60, else None.
    """
    minutes = text.strip()
    if not minutes.isdecimal() or not ALLOW_MIN <= int(minutes) <= ALLOW_MAX:
        return None
    return int(minutes)


def allow_text(*, draft: ChangeDraft, on: ChangeStop) -> str:
    """
    Choose the allowance shown for a stop to get on at.

    Parameters
    ----------
    draft : ChangeDraft
        The change's choices.
    on : ChangeStop
        The stop joined.

    Returns
    -------
    str
        The typed allowance when it was set for this stop, else the
        walk's default.
    """
    if draft.allow.strip() and draft.allow_for == on.place.stop_id:
        return draft.allow.strip()
    return str(default_allow(metres=on.metres))


def chosen(
    *, places: Sequence[StopPlace], given: str,
) -> StopPlace | None:
    """
    Find the offered place with an ID.

    Parameters
    ----------
    places : Sequence[StopPlace]
        Places offered.
    given : str
        The ID given; '' for none.

    Returns
    -------
    StopPlace | None
        The place, or None when it is not offered.
    """
    return next((place for place in places if place.stop_id == given), None)


def change_step(
    *, options: PinOptions, draft: ChangeDraft, alight: StopPlace,
) -> ChangeStep:
    """
    Check one change against what the timetable offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : ChangeDraft
        The change's choices.
    alight : StopPlace
        The stop left.

    Returns
    -------
    ChangeStep
        The stops offered and, when it is offered, the stop chosen.
    """
    stops = options.change_stops(place=alight)
    on = next(
        (stop for stop in stops if stop.place.stop_id == draft.on), None,
    )
    if on is None:
        return ChangeStep(stops=stops)
    return ChangeStep(
        stops=stops, on=on, allow=allow_text(draft=draft, on=on),
    )


def first_leg(
    *, options: PinOptions, draft: ConnectionDraft, origin: StopPlace,
) -> tuple[tuple[StopPlace, ...], StopPlace | None]:
    """
    Check leg 1: any route from the From to the stop got off at.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : ConnectionDraft
        The choices made.
    origin : StopPlace
        The From.

    Returns
    -------
    tuple[tuple[StopPlace, ...], StopPlace | None]
        The places offered to get off at, and the one chosen when it is
        offered.
    """
    firsts = options.reached_from(place=origin)
    return firsts, chosen(places=firsts, given=draft.first.off)


def made_change(*, alight: StopPlace | None, step: ChangeStep) -> Change:
    """
    Make a change from checked choices.

    Parameters
    ----------
    alight : StopPlace | None
        The stop left.
    step : ChangeStep
        The checked change; its stop and allowance must be set.

    Returns
    -------
    Change
        The change.

    Raises
    ------
    ValueError
        If a stop or the allowance is missing.
    """
    allow = allow_minutes(text=step.allow)
    if alight is None or step.on is None or allow is None:
        raise ValueError('change not complete')
    return Change(alight=alight, board=step.on.place, allow=allow)


def editor_way(
    *, editor: ConnectionEditor, alight: StopPlace | None,
) -> Way | None:
    """
    Make the way that checked choices stand for.

    Parameters
    ----------
    editor : ConnectionEditor
        The checked editor, without its way.
    alight : StopPlace | None
        The stop leg 1 is left at.

    Returns
    -------
    Way | None
        The way; None until every step has a value and each allowance
        is 1 to 60 minutes.
    """
    two = editor.draft.changes == 2
    steps = [(alight, editor.first)]
    if two:
        steps.append((chosen(
            places=editor.offs, given=editor.draft.second.off,
        ), editor.second))
    if not editor.draft.to:
        return None
    try:
        changes = tuple(
            made_change(alight=place, step=step) for place, step in steps
        )
    except ValueError:
        return None
    return Way(changes=changes)


def change_kept(*, step: ChangeStep, off: str) -> ChangeDraft:
    """
    Keep a change's offered choices.

    Parameters
    ----------
    step : ChangeStep
        The checked change.
    off : str
        The checked stop left.

    Returns
    -------
    ChangeDraft
        The change's choices that are offered.
    """
    on = '' if step.on is None else step.on.place.stop_id
    return ChangeDraft(off=off, on=on, allow=step.allow, allow_for=on)


def later_steps(
    *, options: PinOptions, draft: ConnectionDraft, editor: ConnectionEditor,
) -> ConnectionEditor:
    """
    Check the second change and the To after the first change.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : ConnectionDraft
        The choices made.
    editor : ConnectionEditor
        The editor checked up to the first change.

    Returns
    -------
    ConnectionEditor
        The editor with the second change, the places and the checked
        draft.
    """
    on = editor.first.on
    if on is None:
        return editor
    offs = options.reached_from(place=on.place) if draft.changes == 2 else ()
    off2 = chosen(places=offs, given=draft.second.off)
    second = ChangeStep() if off2 is None else change_step(
        options=options, draft=draft.second, alight=off2,
    )
    last = on if draft.changes != 2 else second.on
    places = () if last is None else options.reached_from(place=last.place)
    to = chosen(places=places, given=draft.to)
    kept = ConnectionDraft(
        stop=editor.draft.stop, first=editor.draft.first,
        second=change_kept(
            step=second, off='' if off2 is None else off2.stop_id,
        ),
        changes=draft.changes, to='' if to is None else to.stop_id,
        back=draft.back,
    )
    return replace(
        editor, draft=kept, offs=offs, second=second, places=places,
    )


def connection_edited(
    *, options: PinOptions, draft: ConnectionDraft,
) -> ConnectionEditor:
    """
    Check a hand way's choices and work out what is offered next.

    Leg 1 runs on any route from the From to `off`. Then the first
    change, the second change when two are asked for, and the To. A
    choice that is not offered drops itself and everything after it.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : ConnectionDraft
        The choices made so far.

    Returns
    -------
    ConnectionEditor
        The checked draft, what is offered after it and, when every step
        has a value, the way.
    """
    origin = options.place(stop_id=draft.stop)
    target = options.place(stop_id=draft.to)
    if origin is None:
        empty = ConnectionDraft(
            back=draft.back, changes=draft.changes, to=draft.to,
        )
        return ConnectionEditor(draft=empty, target=target)
    firsts, alight = first_leg(options=options, draft=draft, origin=origin)
    first = ChangeStep() if alight is None else change_step(
        options=options, draft=draft.first, alight=alight,
    )
    off = '' if alight is None else alight.stop_id
    start = ConnectionEditor(
        draft=ConnectionDraft(
            stop=draft.stop, first=change_kept(step=first, off=off),
            changes=draft.changes, back=draft.back,
        ),
        origin=origin, target=target, firsts=firsts, first=first,
    )
    later = later_steps(options=options, draft=draft, editor=start)
    return replace(later, way=editor_way(editor=later, alight=alight))
