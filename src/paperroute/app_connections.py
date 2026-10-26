"""Reads for a connection pin, made once per leg and merged."""

import functools
from dataclasses import replace

from paperroute.core.alert_match import between_names
from paperroute.core.alert_model import BetweenLoaded, LoadBetween
from paperroute.core.connection_model import LEG_TWO_EXTRA, Legs, leg_pins
from paperroute.core.messages import DeparturesLoaded, LoadDepartures
from paperroute.core.model import RouteKind
from paperroute.ports import TimetableRepository


def departures_read(
    *, timetable: TimetableRepository, effect: LoadDepartures
) -> DeparturesLoaded:
    """
    Read a pin's departures, and a connection pin's other legs.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    effect : LoadDepartures
        Request.

    Returns
    -------
    DeparturesLoaded
        Leg 1 (the pin's own departures for a plain pin), with leg 2,
        read for `LEG_TWO_EXTRA` longer, and the direct list as `legs`.

    Raises
    ------
    sqlite3.Error
        When the timetable can't be read.
    """
    read = functools.partial(timetable.departures, start=effect.start)
    loaded = DeparturesLoaded(
        pin=effect.pin,
        departures=(),
        until=effect.end,
        timetable=effect.timetable,
    )
    if effect.pin.change is None:
        found = read(pin=effect.pin, end=effect.end)
        return replace(loaded, departures=tuple(found))
    first, second, direct = leg_pins(pin=effect.pin)
    departures = tuple(read(pin=first, end=effect.end))
    legs = Legs(
        second=tuple(read(pin=second, end=effect.end + LEG_TWO_EXTRA)),
        direct=tuple(read(pin=direct, end=effect.end)),
    )
    return replace(loaded, departures=departures, legs=legs)


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
    if effect.pin.change is None:
        return leg_between(timetable=timetable, effect=effect)
    first, second, _ = leg_pins(pin=effect.pin)
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
