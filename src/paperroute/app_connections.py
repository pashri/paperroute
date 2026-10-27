"""Reads for a connection pin, made once per leg and merged."""

import functools
from collections.abc import Callable, Sequence
from dataclasses import replace
from types import MappingProxyType

from paperroute.adapters.leg_cache import LegCache
from paperroute.core.alert_match import between_names
from paperroute.core.alert_model import BetweenLoaded, LoadBetween
from paperroute.core.chains import Legs, open_legs
from paperroute.core.connection_model import LEG_EXTRA, direct_leg
from paperroute.core.messages import DeparturesLoaded, LoadDepartures
from paperroute.core.model import Departure, Pin, RouteKind, is_connection
from paperroute.ports import TimetableRepository


def departures_read(
    *,
    timetable: TimetableRepository,
    effect: LoadDepartures,
    cache: LegCache | None = None,
) -> DeparturesLoaded:
    """
    Read a pin's departures, and a connection pin's way legs.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadDepartures
        Request.
    cache : LegCache | None
        Scheduled departures held from earlier reads; read straight from
        the timetable when None.

    Returns
    -------
    DeparturesLoaded
        For a pin with a To, every route from its From to its To; for a
        connection pin, also each distinct way leg read once, the k-th
        leg of a way read `k * LEG_EXTRA` longer. Never routes,
        and school routes when the effect skips them, are dropped. A pin
        without a To reads its own departures.

    Raises
    ------
    sqlite3.Error
        When the timetable can't be read.
    """
    pin = effect.pin
    read = reader(timetable=timetable, effect=effect, cache=cache)
    loaded = DeparturesLoaded(
        pin=pin,
        departures=(),
        until=effect.end,
        timetable=effect.timetable,
        skip_school=effect.skip_school,
    )
    if pin.destination is None:
        return replace(loaded, departures=tuple(read(pin=pin, end=effect.end)))
    keep = functools.partial(usable, pin=pin, skip_school=effect.skip_school)
    direct = read(pin=direct_leg(pin=pin), end=effect.end)
    loaded = replace(loaded, departures=keep(items=direct))
    if not is_connection(pin=pin):
        return loaded
    lists = {
        key: keep(
            items=read(pin=leg, end=effect.end + position * LEG_EXTRA),
        )
        for key, (leg, position) in open_legs(pin=pin).items()
    }
    return replace(loaded, legs=Legs(lists=MappingProxyType(lists)))


def reader(
    *,
    timetable: TimetableRepository,
    effect: LoadDepartures,
    cache: LegCache | None,
) -> Callable[..., Sequence[Departure]]:
    """
    Choose how a load reads departures.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadDepartures
        Request.
    cache : LegCache | None
        Scheduled departures held from earlier reads, or None.

    Returns
    -------
    Callable[..., Sequence[Departure]]
        Reads a pin's departures from the effect's start to an end,
        through the cache when there is one.
    """
    if cache is None:
        return functools.partial(timetable.departures, start=effect.start)
    return functools.partial(
        cache.departures,
        timetable=timetable,
        start=effect.start,
        version=effect.timetable,
    )


def usable(
    *, items: Sequence[Departure], pin: Pin, skip_school: bool,
) -> tuple[Departure, ...]:
    """
    Drop departures on routes a pin never uses.

    Parameters
    ----------
    items : Sequence[Departure]
        Departures.
    pin : Pin
        Pin with its never list.
    skip_school : bool
        Whether school routes are dropped too.

    Returns
    -------
    tuple[Departure, ...]
        The departures kept, in order.
    """
    return tuple(
        item
        for item in items
        if item.route not in pin.never
        and not (skip_school and item.facts.kind is RouteKind.SCHOOL)
    )


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
        The names; for a connection pin, the names and families of the
        direct leg and every ticked way's legs merged, and NSW TrainLink
        when any leg has it.
    """
    pin = effect.pin
    if not is_connection(pin=pin):
        return leg_between(timetable=timetable, effect=effect)
    legs = (
        direct_leg(pin=pin),
        *(leg for leg, _ in open_legs(pin=pin).values()),
    )
    loaded = [
        leg_between(timetable=timetable, effect=replace(effect, pin=leg))
        for leg in legs
    ]
    return BetweenLoaded(
        pin=pin,
        names=frozenset().union(*(part.names for part in loaded)),
        timetable=effect.timetable,
        families={
            stop: family
            for part in loaded
            for stop, family in part.families.items()
        },
        trainlink=any(part.trainlink for part in loaded),
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
