"""Check the choices made in a connection pin's editor."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from multidict import MultiMapping

from paperroute.adapters.pin_editor import edited, with_origin
from paperroute.core.connection_model import default_allow
from paperroute.core.model import Change, Pin, RouteKind, StopPlace
from paperroute.core.pin_choices import (
    ChangeStop,
    PinDraft,
    PinEditor,
    clean_nickname,
)
from paperroute.ports import PinOptions

ALLOW_RANGE: Final[str] = 'Allow between 1 and 60 minutes.'
ALLOW_MIN: Final[int] = 1
ALLOW_MAX: Final[int] = 60


@dataclass(frozen=True)
class ConnectionDraft:
    """
    The choices made so far in a connection's editor.

    Attributes
    ----------
    stop : str
        From stop or station ID; '' for none.
    kind : RouteKind | None
        Kind of leg 1's route, or None for none yet.
    route : str
        Leg 1's route number; '' for none.
    off : str
        ID of the stop leg 1 is left at; '' for none.
    on : str
        ID of the stop leg 2 is joined at; '' for none.
    allow : str
        Minutes allowed for the change, as typed; '' for none.
    allow_for : str
        The `on` that `allow` was set for.
    to : str
        ID of the final To; '' for none.
    only : tuple[str, ...]
        Routes leg 2 is limited to.
    ref : str | None
        Ref of the pin being edited, or None for a new pin.
    nickname : str | None
        Name typed for the pin, or None.
    """

    stop: str = ''
    kind: RouteKind | None = None
    route: str = ''
    off: str = ''
    on: str = ''
    allow: str = ''
    allow_for: str = ''
    to: str = ''
    only: tuple[str, ...] = ()
    ref: str | None = None
    nickname: str | None = None


@dataclass(frozen=True)
class ConnectionEditor:
    """
    What a connection's editor shows.

    Attributes
    ----------
    draft : ConnectionDraft
        The choices that are offered, with the nickname and ref.
    leg : PinEditor
        Leg 1's editor, whose To is the stop got off at.
    changes : tuple[ChangeStop, ...]
        Stops leg 2 can be joined at; empty until `off` is chosen.
    walk : int | None
        Metres to the chosen `on`, or None.
    places : tuple[StopPlace, ...]
        Places leg 2 can reach; empty until `on` is chosen.
    routes : tuple[str, ...]
        Routes from `on` to the To; empty until the To is chosen.
    pin : Pin | None
        The pin the choices make; None until every step has a value and
        the allowance is 1 to 60 minutes.
    """

    draft: ConnectionDraft
    leg: PinEditor
    changes: tuple[ChangeStop, ...]
    walk: int | None
    places: tuple[StopPlace, ...]
    routes: tuple[str, ...]
    pin: Pin | None


@dataclass(frozen=True)
class SecondLeg:
    """
    The checked choices after leg 1.

    Attributes
    ----------
    on : ChangeStop | None
        The stop leg 2 is joined at, or None.
    allow : str
        The allowance text.
    places : tuple[StopPlace, ...]
        Places offered as the To.
    to : StopPlace | None
        The To, or None.
    routes : tuple[str, ...]
        Routes offered for leg 2.
    only : tuple[str, ...]
        Routes chosen for leg 2.
    """

    on: ChangeStop | None = None
    allow: str = ''
    places: tuple[StopPlace, ...] = ()
    to: StopPlace | None = None
    routes: tuple[str, ...] = ()
    only: tuple[str, ...] = ()


def connection_draft(*, query: MultiMapping[str]) -> ConnectionDraft:
    """
    Read a connection editor's choices from form fields.

    Parameters
    ----------
    query : MultiMapping[str]
        Fields: `ref`, `stop`, `kind`, `route`, `off`, `on`, `allow`,
        `allow_for`, `to`, every `only2`, and `nickname`.

    Returns
    -------
    ConnectionDraft
        The draft; an unknown kind becomes None.
    """
    kind = query.get('kind', '')
    return ConnectionDraft(
        stop=query.get('stop', ''),
        kind=RouteKind(kind) if kind in RouteKind else None,
        route=query.get('route', ''),
        off=query.get('off', ''),
        on=query.get('on', ''),
        allow=query.get('allow', ''),
        allow_for=query.get('allow_for', ''),
        to=query.get('to', ''),
        only=tuple(query.getall('only2', [])),
        nickname=query.get('nickname') or None,
        ref=query.get('ref') or None,
    )


def connection_draft_of(*, pin: Pin, ref: str) -> ConnectionDraft:
    """
    Start a connection editor's choices from a saved pin.

    Parameters
    ----------
    pin : Pin
        A connection pin.
    ref : str
        Its ref.

    Returns
    -------
    ConnectionDraft
        The pin's choices; the change fields are empty without a change.
    """
    change = pin.change
    board = '' if change is None else change.board.stop_id
    return ConnectionDraft(
        stop=pin.origin.stop_id,
        kind=pin.kind,
        route=pin.routes[0] if pin.routes else '',
        off='' if change is None else change.alight.stop_id,
        on=board,
        allow='' if change is None else str(change.allow),
        allow_for=board,
        to='' if pin.destination is None else pin.destination.stop_id,
        only=() if change is None else change.only,
        nickname=pin.nickname,
        ref=ref,
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


def allow_text(*, draft: ConnectionDraft, on: ChangeStop) -> str:
    """
    Choose the allowance shown for a stop to get on at.

    Parameters
    ----------
    draft : ConnectionDraft
        The choices made.
    on : ChangeStop
        The stop leg 2 is joined at.

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


def second_leg(
    *, options: PinOptions, draft: ConnectionDraft, alight: StopPlace,
) -> tuple[tuple[ChangeStop, ...], SecondLeg]:
    """
    Check the change and leg 2 against what the timetable offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : ConnectionDraft
        The choices made.
    alight : StopPlace
        The stop leg 1 is left at.

    Returns
    -------
    tuple[tuple[ChangeStop, ...], SecondLeg]
        The stops offered to get on at, and the checked choices after
        them. A choice not offered drops itself and every later one.
    """
    changes = options.change_stops(place=alight)
    on = next(
        (stop for stop in changes if stop.place.stop_id == draft.on), None,
    )
    if on is None:
        return changes, SecondLeg()
    places = options.reached_from(place=on.place)
    to = chosen(places=places, given=draft.to)
    routes = () if to is None else options.routes_between(
        origin=on.place, destination=to,
    )
    return changes, SecondLeg(
        on=on,
        allow=allow_text(draft=draft, on=on),
        places=places,
        to=to,
        routes=routes,
        only=tuple(route for route in draft.only if route in routes),
    )


def connection_pin(
    *, draft: ConnectionDraft, leg: PinEditor, alight: StopPlace,
    second: SecondLeg,
) -> Pin | None:
    """
    Make the connection pin that checked choices stand for.

    Parameters
    ----------
    draft : ConnectionDraft
        The choices made, for the nickname.
    leg : PinEditor
        Leg 1's checked editor.
    alight : StopPlace
        The stop leg 1 is left at.
    second : SecondLeg
        The checked choices after leg 1.

    Returns
    -------
    Pin | None
        The pin; None until every step has a value and the allowance is
        1 to 60 minutes.
    """
    allow = allow_minutes(text=second.allow)
    if leg.pin is None or second.on is None or second.to is None:
        return None
    if allow is None:
        return None
    change = Change(
        alight=alight, board=second.on.place, allow=allow, only=second.only,
    )
    return Pin(
        origin=leg.pin.origin,
        destination=second.to,
        kind=leg.pin.kind,
        routes=leg.pin.routes,
        nickname=clean_nickname(text=draft.nickname or ''),
        change=change,
    )


def checked_draft(
    *, draft: ConnectionDraft, leg: PinEditor, second: SecondLeg,
) -> ConnectionDraft:
    """
    Keep only the choices that are offered.

    Parameters
    ----------
    draft : ConnectionDraft
        The choices made.
    leg : PinEditor
        Leg 1's checked editor.
    second : SecondLeg
        The checked choices after leg 1.

    Returns
    -------
    ConnectionDraft
        The checked draft.
    """
    on = '' if second.on is None else second.on.place.stop_id
    return ConnectionDraft(
        stop=leg.draft.stop,
        kind=leg.draft.kind,
        route=leg.draft.route,
        off=leg.draft.to,
        on=on,
        allow=second.allow,
        allow_for=on,
        to='' if second.to is None else second.to.stop_id,
        only=second.only,
        nickname=draft.nickname,
        ref=draft.ref,
    )


def first_leg(
    *, options: PinOptions, draft: ConnectionDraft, origin: StopPlace,
) -> tuple[PinEditor, StopPlace | None]:
    """
    Check leg 1 as a pin from the From to the stop got off at.

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
    tuple[PinEditor, StopPlace | None]
        Leg 1's editor, and the stop got off at when it is offered.
    """
    leg = with_origin(
        options=options,
        draft=PinDraft(
            stop=draft.stop,
            kind=draft.kind,
            route=draft.route,
            to=draft.off,
            nickname=draft.nickname,
            ref=draft.ref,
        ),
        origin=origin,
    )
    return leg, chosen(places=leg.destinations, given=leg.draft.to)


def empty_editor(
    *, options: PinOptions, draft: ConnectionDraft,
) -> ConnectionEditor:
    """
    Start an editor over for a From the timetable does not know.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : ConnectionDraft
        The choices made.

    Returns
    -------
    ConnectionEditor
        An editor keeping only the nickname and ref.
    """
    leg = edited(
        options=options,
        draft=PinDraft(nickname=draft.nickname, ref=draft.ref),
    )
    return ConnectionEditor(
        draft=ConnectionDraft(nickname=draft.nickname, ref=draft.ref),
        leg=leg,
        changes=(),
        walk=None,
        places=(),
        routes=(),
        pin=None,
    )


def connection_edited(
    *, options: PinOptions, draft: ConnectionDraft,
) -> ConnectionEditor:
    """
    Check a connection's choices and work out what is offered next.

    Leg 1 is checked as a pin from the From to `off`. Then `on`, `allow`,
    `to` and `only` in turn. A choice that is not offered drops itself
    and everything after it.

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
        has a value, the pin.
    """
    origin = options.place(stop_id=draft.stop) if draft.stop else None
    if origin is None:
        return empty_editor(options=options, draft=draft)
    leg, alight = first_leg(options=options, draft=draft, origin=origin)
    changes, second = ((), SecondLeg()) if alight is None else second_leg(
        options=options, draft=draft, alight=alight,
    )
    return ConnectionEditor(
        draft=checked_draft(draft=draft, leg=leg, second=second),
        leg=leg,
        changes=changes,
        walk=None if second.on is None else second.on.metres,
        places=second.places,
        routes=second.routes,
        pin=None if alight is None else connection_pin(
            draft=draft, leg=leg, alight=alight, second=second,
        ),
    )
