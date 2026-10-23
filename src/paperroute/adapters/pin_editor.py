"""Check the choices made in a pin's editor against the timetable."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from paperroute.core.chooser import Choice
from paperroute.core.model import Pin, RouteKind, StopPlace
from paperroute.core.pin_choices import PinDraft, PinEditor, clean_nickname
from paperroute.ports import PinOptions


@dataclass(frozen=True)
class Step:
    """
    The result of checking one choice.

    Attributes
    ----------
    value : str
        The choice kept, or the only one offered when none is kept;
        '' for none.
    kept : bool
        False when a choice was given that is not offered.
    """

    value: str
    kept: bool


def pick(*, given: str, offered: Sequence[str]) -> Step:
    """
    Check one choice against what is offered.

    Parameters
    ----------
    given : str
        The choice made; '' for none.
    offered : Sequence[str]
        What is offered.

    Returns
    -------
    Step
        The given choice when it is offered. Otherwise the only choice
        offered, or '' when there are several or none. It is not kept
        when something was given and not offered.
    """
    if given in offered:
        return Step(value=given, kept=True)
    lone = offered[0] if len(offered) == 1 else ''
    return Step(value=lone, kept=not given)


def kind_of(*, kinds: Sequence[RouteKind], value: str) -> RouteKind | None:
    """
    Find the kind with a name.

    Parameters
    ----------
    kinds : Sequence[RouteKind]
        Kinds to look in.
    value : str
        Name of a kind; '' for none.

    Returns
    -------
    RouteKind | None
        The kind, or None when none is named.
    """
    return next((kind for kind in kinds if kind == value), None)


def kind_step(
    *, options: PinOptions, stop: str, given: RouteKind | None,
) -> tuple[tuple[RouteKind, ...], RouteKind | None, bool]:
    """
    Check the kind against the kinds the From offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    stop : str
        The From's ID.
    given : RouteKind | None
        The kind given, or None for none.

    Returns
    -------
    tuple[tuple[RouteKind, ...], RouteKind | None, bool]
        The kinds offered, the kind chosen, and whether the given kind
        was kept.
    """
    kinds = options.kinds_at(stop_id=stop)
    step = pick(given='' if given is None else given, offered=kinds)
    return kinds, kind_of(kinds=kinds, value=step.value), step.kept


def line_step(
    *,
    options: PinOptions,
    stop: str,
    kind: RouteKind | None,
    given: str,
) -> tuple[tuple[Choice, ...], Choice | None, bool]:
    """
    Check the route number against the lines the kind offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    stop : str
        The From's ID.
    kind : RouteKind | None
        The kind chosen, or None for none.
    given : str
        The route number given; '' for none.

    Returns
    -------
    tuple[tuple[Choice, ...], Choice | None, bool]
        The lines offered, the line chosen, and whether the given route
        was kept.
    """
    lines = () if kind is None else options.lines_at(stop_id=stop, kind=kind)
    step = pick(given=given, offered=[line.badge for line in lines])
    line = next((line for line in lines if line.badge == step.value), None)
    return lines, line, step.kept


def to_step(
    *,
    options: PinOptions,
    origin: StopPlace,
    kind: RouteKind | None,
    line: Choice | None,
    given: str,
) -> tuple[tuple[StopPlace, ...], StopPlace | None]:
    """
    Check the To against the places the line offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    origin : StopPlace
        The From.
    kind : RouteKind | None
        The kind chosen, or None for none.
    line : Choice | None
        The line chosen, or None for none.
    given : str
        The To's ID; '' for none.

    Returns
    -------
    tuple[tuple[StopPlace, ...], StopPlace | None]
        The places offered, and the one given when it is offered. A To
        is never chosen for the owner.
    """
    if kind is None or line is None:
        return (), None
    places = options.destinations(origin=origin, kind=kind, line=line)
    return places, next(
        (place for place in places if place.stop_id == given), None,
    )


def filter_step(
    *,
    options: PinOptions,
    stop: str,
    kind: RouteKind | None,
    given: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Check the route filter against the routes the kind offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    stop : str
        The From's ID.
    kind : RouteKind | None
        The kind chosen, or None for none.
    given : tuple[str, ...]
        The route numbers given.

    Returns
    -------
    tuple[tuple[str, ...], tuple[str, ...]]
        The route numbers offered, and the given ones that are offered.
    """
    if kind is None:
        return (), ()
    filters = options.filters_at(stop_id=stop, kind=kind)
    return filters, tuple(route for route in given if route in filters)


def pin_of(
    *,
    draft: PinDraft,
    origin: StopPlace,
    destination: StopPlace | None,
    line: Choice | None,
) -> Pin | None:
    """
    Make the pin that a checked draft stands for.

    Parameters
    ----------
    draft : PinDraft
        The checked draft.
    origin : StopPlace
        The From.
    destination : StopPlace | None
        The To, or None for none.
    line : Choice | None
        The line or route chosen, or None for none.

    Returns
    -------
    Pin | None
        The pin; None until a kind and a line are chosen.
    """
    if draft.kind is None or line is None:
        return None
    return Pin(
        origin=origin,
        destination=destination,
        kind=draft.kind,
        routes=(line.badge,),
        only=draft.only,
        nickname=clean_nickname(text=draft.nickname or ''),
    )


def edited(
    *, options: PinOptions, draft: PinDraft, missing: Pin | None = None,
) -> PinEditor:
    """
    Check a draft and work out what the editor offers next.

    Each choice is checked in turn: stop, kind, line, To, then the route
    filter. A choice that is not offered is dropped, with every choice
    after it, except that the filter depends on the stop and kind alone.
    A kind or line is chosen when only one is offered; a To never is.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : PinDraft
        The choices made so far.
    missing : Pin | None
        A pin the timetable no longer knows, whose nearby stops are
        offered; None for none.

    Returns
    -------
    PinEditor
        The checked draft, what is offered after it and, when a stop,
        kind and line are chosen, the pin they make.
    """
    near = () if missing is None else options.nearby(
        place=missing.origin, kind=missing.kind,
    )
    origin = options.place(stop_id=draft.stop) if draft.stop else None
    if origin is None:
        clean = PinDraft(nickname=draft.nickname, ref=draft.ref)
        return PinEditor(
            draft=clean,
            origin=None,
            kinds=(),
            lines=(),
            destinations=(),
            filters=(),
            nearby=near,
            pin=None,
        )
    return replace(
        with_origin(options=options, draft=draft, origin=origin),
        nearby=near,
    )


def with_origin(
    *, options: PinOptions, draft: PinDraft, origin: StopPlace,
) -> PinEditor:
    """
    Check the choices that follow a From the timetable knows.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : PinDraft
        The choices made so far.
    origin : StopPlace
        The From.

    Returns
    -------
    PinEditor
        The checked draft and what is offered after it, with no nearby
        stops.
    """
    stop = origin.stop_id
    kinds, kind, kind_kept = kind_step(
        options=options, stop=stop, given=draft.kind,
    )
    lines, line, line_kept = line_step(
        options=options, stop=stop, kind=kind,
        given=draft.route if kind_kept else '',
    )
    places, destination = to_step(
        options=options, origin=origin, kind=kind, line=line,
        given=draft.to if kind_kept and line_kept else '',
    )
    filters, only = filter_step(
        options=options, stop=stop, kind=kind,
        given=draft.only if kind_kept else (),
    )
    checked = PinDraft(
        stop=stop,
        kind=kind,
        route='' if line is None else line.badge,
        to='' if destination is None else destination.stop_id,
        only=only,
        nickname=draft.nickname,
        ref=draft.ref,
    )
    return PinEditor(
        draft=checked,
        origin=origin,
        kinds=kinds,
        lines=lines,
        destinations=places,
        filters=filters,
        nearby=(),
        pin=pin_of(
            draft=checked, origin=origin, destination=destination, line=line,
        ),
    )
