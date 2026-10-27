"""Reads for a connection pin, made once per leg and merged."""

import functools
from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType

from paperroute.core.alert_match import between_names
from paperroute.core.alert_model import BetweenLoaded, LoadBetween
from paperroute.core.chains import Legs, open_legs
from paperroute.core.connection_model import (
    LEG_EXTRA,
    direct_leg,
    stopgap_way,
    way_legs,
)
from paperroute.core.messages import DeparturesLoaded, LoadDepartures
from paperroute.core.model import Departure, Pin, RouteKind, is_connection
from paperroute.ports import TimetableRepository


def departures_read(
    *, timetable: TimetableRepository, effect: LoadDepartures
) -> DeparturesLoaded:
    """
    Read a pin's departures, and a connection pin's way legs.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadDepartures
        Request.

    Returns
    -------
    DeparturesLoaded
        For a pin with a To, every route from its From to its To; for a
        connection pin, also each distinct way leg read once, the k-th
        leg of a way read `k * LEG_EXTRA` longer. Never routes are
        dropped. A pin without a To reads its own departures.

    Raises
    ------
    sqlite3.Error
        When the timetable can't be read.
    """
    pin = effect.pin
    read = functools.partial(timetable.departures, start=effect.start)
    loaded = DeparturesLoaded(
        pin=pin, departures=(), until=effect.end, timetable=effect.timetable,
    )
    if pin.destination is None:
        found = read(pin=pin, end=effect.end)
        return replace(loaded, departures=tuple(found))
    direct = read(pin=direct_leg(pin=pin), end=effect.end)
    loaded = replace(loaded, departures=usable(items=direct, pin=pin))
    if not is_connection(pin=pin):
        return loaded
    lists = {
        key: usable(
            items=read(pin=leg, end=effect.end + position * LEG_EXTRA),
            pin=pin,
        )
        for key, (leg, position) in open_legs(pin=pin).items()
    }
    return replace(loaded, legs=Legs(lists=MappingProxyType(lists)))


def usable(
    *, items: Sequence[Departure], pin: Pin,
) -> tuple[Departure, ...]:
    """
    Drop departures on routes a pin never uses.

    Parameters
    ----------
    items : Sequence[Departure]
        Departures.
    pin : Pin
        Pin with its never list.

    Returns
    -------
    tuple[Departure, ...]
        The departures not on a never route, in order.
    """
    return tuple(item for item in items if item.route not in pin.never)


def between_read(
    *, timetable: TimetableRepository, effect: LoadBetween
) -> BetweenLoaded:
    """
    Read the stations between a pin's ends, leg by leg for a connection.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadBetween
        Request, echoed back.

    Returns
    -------
    BetweenLoaded
        The names; for a connection pin, both legs' names and families
        merged, and NSW TrainLink when either leg has it.
    """
    way = stopgap_way(pin=effect.pin)
    if way is None:
        return leg_between(timetable=timetable, effect=effect)
    first, second = way_legs(pin=effect.pin, way=way)
    loaded = [
        leg_between(timetable=timetable, effect=replace(effect, pin=leg))
        for leg in (first, second)
    ]
    return BetweenLoaded(
        pin=effect.pin,
        names=loaded[0].names | loaded[1].names,
        timetable=effect.timetable,
        families={**loaded[0].families, **loaded[1].families},
        trainlink=loaded[0].trainlink or loaded[1].trainlink,
    )


def leg_between(
    *, timetable: TimetableRepository, effect: LoadBetween
) -> BetweenLoaded:
    """
    Read the place names of the stations between a plain pin's ends.

    A pin's From or To may be a platform, so their stop families are read
    too and each is found through its station. With no trips asked
    about, only the families are read. For a train pin or one of no
    kind, NSW TrainLink is asked about every stop in either end's family.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadBetween
        Request, echoed back.

    Returns
    -------
    BetweenLoaded
        The names.
    """
    pin = effect.pin
    ends = [pin.origin.stop_id]
    if pin.destination is not None:
        ends.append(pin.destination.stop_id)
    families = timetable.alert_places(route_ids=(), stop_ids=ends).stops
    patterns = (
        timetable.patterns(trip_ids=effect.trip_ids, around=effect.day)
        if effect.trip_ids
        else {}
    )
    names = between_names(pin=pin, patterns=patterns, families=families)
    stop_ids = {
        stop for end in ends for stop in families.get(end, frozenset({end}))
    }
    return BetweenLoaded(
        pin=pin,
        names=names,
        timetable=effect.timetable,
        families=families,
        trainlink=pin.kind in (None, RouteKind.TRAIN)
        and timetable.serves_trainlink(stop_ids=stop_ids),
    )
