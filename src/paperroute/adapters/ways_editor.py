"""Read and change the ways of a pin with a Destination."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from multidict import MultiMapping

from paperroute.adapters.state_change import ALLOW_MAX, ALLOW_MIN
from paperroute_transit.core.connection_model import (
    default_allow,
    direct_leg,
    walk_metres,
    way_legs,
)
from paperroute_transit.core.model import (
    MAX_CHANGES,
    Change,
    Pin,
    StopPlace,
    Way,
    way_key,
)
from paperroute_transit.ports import PinOptions

CHANGE_SEP: Final[str] = '|'  # between the changes of an encoded way
PART_SEP: Final[str] = ','  # between alight, board and allow


@dataclass(frozen=True)
class WaysDraft:
    """
    The choices made on the ways page.

    Attributes
    ----------
    origin : str
        From stop ID; '' for none.
    destination : str
        To stop ID; '' for none.
    ways : tuple[str, ...]
        Every way, encoded by `encode_way`, ticked or not.
    ticked : frozenset[int]
        Indexes into `ways` of the ticked ways.
    never : tuple[str, ...]
        Routes never used.
    nickname : str | None
        Name typed for the pin, or None.
    ref : str | None
        Ref of the pin being edited, or None for a new pin.
    pick : str
        A way picked from the suggestions, as stop IDs only; '' for none.
    """

    origin: str = ''
    destination: str = ''
    ways: tuple[str, ...] = ()
    ticked: frozenset[int] = frozenset()
    never: tuple[str, ...] = ()
    nickname: str | None = None
    ref: str | None = None
    pick: str = ''


def encode_way(*, way: Way) -> str:
    """
    Write a way as one form value.

    Parameters
    ----------
    way : Way
        Way.

    Returns
    -------
    str
        Each change as `alight,board,allow`, joined by `|`; the tick is
        not part of it.
    """
    return CHANGE_SEP.join(
        PART_SEP.join((c.alight.stop_id, c.board.stop_id, str(c.allow)))
        for c in way.changes
    )


def decoded_change(*, options: PinOptions, text: str) -> Change | None:
    """
    Read one change of an encoded way.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    text : str
        `alight,board,allow`.

    Returns
    -------
    Change | None
        The change; None for an unknown stop or an allow outside 1-60.
    """
    parts = text.split(PART_SEP)
    if len(parts) != 3 or not parts[2].isdecimal():
        return None
    alight = options.place(stop_id=parts[0])
    board = options.place(stop_id=parts[1])
    allow = int(parts[2])
    if alight is None or board is None:
        return None
    if not ALLOW_MIN <= allow <= ALLOW_MAX:
        return None
    return Change(alight=alight, board=board, allow=allow)


def decode_way(
    *, options: PinOptions, text: str, ticked: bool,
) -> Way | None:
    """
    Read an encoded way.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    text : str
        The way, as `encode_way` writes it.
    ticked : bool
        Whether the way is used.

    Returns
    -------
    Way | None
        The way; None when a change is broken or there are none or more
        than `MAX_CHANGES`.
    """
    parts = text.split(CHANGE_SEP)
    changes = [
        decoded_change(options=options, text=part) for part in parts
    ]
    kept = tuple(change for change in changes if change is not None)
    if len(kept) != len(parts) or len(kept) > MAX_CHANGES:
        return None
    return Way(changes=kept, ticked=ticked)


def whole_numbers(*, texts: Sequence[str]) -> frozenset[int]:
    """
    Read the whole numbers among some texts.

    Parameters
    ----------
    texts : Sequence[str]
        Texts.

    Returns
    -------
    frozenset[int]
        The texts that are whole numbers; others are skipped.
    """
    return frozenset(int(text) for text in texts if text.isdecimal())


def ways_draft(*, query: MultiMapping[str]) -> WaysDraft:
    """
    Read the ways page's choices from form fields.

    Parameters
    ----------
    query : MultiMapping[str]
        Fields: `from`, `to`, every `ways`, every ticked `way` index,
        every `never`, `nickname`, `ref` and `pick`.

    Returns
    -------
    WaysDraft
        The draft.
    """
    return WaysDraft(
        origin=query.get('from', ''),
        destination=query.get('to', ''),
        ways=tuple(query.getall('ways', [])),
        ticked=whole_numbers(texts=query.getall('way', [])),
        never=tuple(query.getall('never', [])),
        nickname=query.get('nickname') or None,
        ref=query.get('ref') or None,
        pick=query.get('pick', ''),
    )


def draft_ways(
    *, options: PinOptions, draft: WaysDraft,
) -> tuple[Way, ...]:
    """
    Read a draft's ways.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : WaysDraft
        The choices made.

    Returns
    -------
    tuple[Way, ...]
        The ways that read, in order, ticked as chosen; broken ones are
        dropped.
    """
    ways = (
        decode_way(options=options, text=text, ticked=index in draft.ticked)
        for index, text in enumerate(draft.ways)
    )
    return tuple(way for way in ways if way is not None)


def added_way(*, pin: Pin, way: Way) -> Pin:
    """
    Add a way to a pin, or refresh the way with the same stops.

    Parameters
    ----------
    pin : Pin
        Pin.
    way : Way
        Way to add.

    Returns
    -------
    Pin
        The pin with the way ticked: in place of a way with the same
        `way_key`, else at the end.
    """
    key = way_key(way=way)
    fresh = replace(way, ticked=True)
    if all(way_key(way=old) != key for old in pin.ways):
        return replace(pin, ways=(*pin.ways, fresh))
    ways = tuple(
        fresh if way_key(way=old) == key else old for old in pin.ways
    )
    return replace(pin, ways=ways)


def walk_change(*, alight: StopPlace, board: StopPlace) -> Change:
    """
    Make a change that allows for its walk.

    Parameters
    ----------
    alight : StopPlace
        Stop left.
    board : StopPlace
        Stop joined.

    Returns
    -------
    Change
        The change, allowing the walk at 80 m a minute plus 2 minutes.
    """
    metres = walk_metres(alight=alight, board=board)
    return Change(
        alight=alight, board=board, allow=default_allow(metres=metres),
    )


def stop_way(*, places: Sequence[StopPlace]) -> Way | None:
    """
    Make a way from its change stops, allowing for each walk.

    Parameters
    ----------
    places : Sequence[StopPlace]
        Alight and board of each change, in turn.

    Returns
    -------
    Way | None
        The way, with each allow worked out from the walk's distance;
        None for no change, more than `MAX_CHANGES` or an odd count.
    """
    pairs = list(zip(places[::2], places[1::2], strict=False))
    if len(places) % 2 or not pairs or len(pairs) > MAX_CHANGES:
        return None
    return Way(changes=tuple(
        walk_change(alight=alight, board=board) for alight, board in pairs
    ))


def picked_way(*, options: PinOptions, text: str) -> Way | None:
    """
    Read a way picked from the suggestions.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    text : str
        Change stop IDs as `alight,board`, joined by `|`.

    Returns
    -------
    Way | None
        The way, or None when a stop is unknown or the shape is wrong.
    """
    ids = [
        stop_id
        for part in text.split(CHANGE_SEP)
        for stop_id in part.split(PART_SEP)
    ]
    places = [options.place(stop_id=stop_id) for stop_id in ids]
    known = [place for place in places if place is not None]
    if len(known) != len(places):
        return None
    return stop_way(places=known)


def draft_pin(
    *, options: PinOptions, draft: WaysDraft, base: Pin | None,
) -> Pin | None:
    """
    Make the pin a ways draft stands for.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    draft : WaysDraft
        The choices made.
    base : Pin | None
        The saved pin being edited, or None.

    Returns
    -------
    Pin | None
        The pin, with a picked way added; None when the From or To is
        not known.
    """
    origin = options.place(stop_id=draft.origin)
    destination = options.place(stop_id=draft.destination)
    if origin is None or destination is None:
        return None
    pin = replace(
        base or Pin(origin=origin),
        origin=origin, destination=destination, routes=(), only=(),
        ways=draft_ways(options=options, draft=draft),
        never=draft.never, nickname=draft.nickname,
    )
    picked = picked_way(options=options, text=draft.pick)
    return pin if picked is None else added_way(pin=pin, way=picked)


def ways_query(*, pin: Pin, ref: str | None) -> list[tuple[str, str]]:
    """
    Write a pin's ways page as query fields.

    Parameters
    ----------
    pin : Pin
        A pin with a Destination.
    ref : str | None
        The saved pin's ref, or None.

    Returns
    -------
    list[tuple[str, str]]
        Fields `ways_draft` reads back.
    """
    to = '' if pin.destination is None else pin.destination.stop_id
    fields = [('ref', ref)] if ref else []
    fields += [('from', pin.origin.stop_id), ('to', to)]
    fields += [('ways', encode_way(way=way)) for way in pin.ways]
    fields += [
        ('way', str(index))
        for index, way in enumerate(pin.ways)
        if way.ticked
    ]
    fields += [('never', route) for route in pin.never]
    return fields + ([('nickname', pin.nickname)] if pin.nickname else [])


def leg_routes(*, options: PinOptions, leg: Pin) -> tuple[str, ...]:
    """
    List the routes of one leg.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    leg : Pin
        A leg pin.

    Returns
    -------
    tuple[str, ...]
        The routes from its From to its Destination; none without one.
    """
    if leg.destination is None:
        return ()
    return options.routes_between(
        origin=leg.origin, destination=leg.destination,
    )


def never_choices(*, options: PinOptions, pin: Pin) -> tuple[str, ...]:
    """
    List the routes the never list offers.

    Parameters
    ----------
    options : PinOptions
        What the timetable offers.
    pin : Pin
        A pin with a Destination.

    Returns
    -------
    tuple[str, ...]
        Every route of the direct leg and of each leg of every way, in
        the order first met, then routes already never used.
    """
    legs = [direct_leg(pin=pin)] + [
        leg for way in pin.ways for leg in way_legs(pin=pin, way=way)
    ]
    routes = [
        route
        for leg in legs
        for route in leg_routes(options=options, leg=leg)
    ]
    return tuple(dict.fromkeys([*routes, *pin.never]))
