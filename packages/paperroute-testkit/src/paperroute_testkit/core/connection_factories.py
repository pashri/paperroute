"""A real two-leg connection and its Tuesday 27 October times."""

from datetime import datetime
from types import MappingProxyType
from typing import Final

from paperroute.core.chains import Chain, Legs, chains_of, leg_key
from paperroute.core.connection_model import way_legs
from paperroute_testkit.core.factories import departure, live, place
from paperroute_transit.core.model import (
    SYDNEY,
    Change,
    Departure,
    Pin,
    RouteKind,
    StopPlace,
    Way,
)


def at(hour: int, minute: int, *, day: int = 27) -> datetime:
    """
    A Sydney time in October 2026.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.
    day : int
        Day of October.

    Returns
    -------
    datetime
        Aware Sydney time.
    """
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY)


HOLLIS: Final[StopPlace] = place(
    'G2145615', name='Hollis St Opp Constitution Hill Library', point=(0.0, 900.0),
)
HAMMERS: Final[StopPlace] = place(
    'G215254', name='Hammers Rd Opp Sherwood St', point=(0.0, 0.0),
)
CONSTITUTION: Final[StopPlace] = place(
    '2145648', name='Constitution Hill, North West Twy', point=(144.0, 0.0),
)
WESTMEAD: Final[StopPlace] = place(
    '214510', name='Westmead Station', point=(800.0, -1900.0),
)
PARRAMATTA: Final[StopPlace] = place(
    '2150', name='Parramatta Station', point=(2000.0, -2500.0),
)
CAMPBELLTOWN: Final[StopPlace] = place(
    '2560', name='Campbelltown Station', point=(-5000.0, -30000.0),
)
WORK_CHANGE: Final[Change] = Change(
    alight=HAMMERS, board=CONSTITUTION, allow=7,
)
WORK: Final[Pin] = Pin(
    origin=HOLLIS,
    destination=WESTMEAD,
    kind=RouteKind.BUS,
    nickname='Work',
    ways=(Way(changes=(WORK_CHANGE,)),),
)
CAMPBELLTOWN_WAY: Final[Way] = Way(
    changes=(Change(alight=HAMMERS, board=CAMPBELLTOWN, allow=6),),
)
TWO_WAY: Final[Way] = Way(
    changes=(
        WORK_CHANGE,
        Change(alight=WESTMEAD, board=WESTMEAD, allow=4),
    ),
)


def run(
    *, departs: tuple[int, int], arrives: tuple[int, int], route: str,
    trip_id: str,
) -> Departure:
    """
    A departure between two times on the 27th.

    Parameters
    ----------
    departs : tuple[int, int]
        Hour and minute it leaves.
    arrives : tuple[int, int]
        Hour and minute it arrives.
    route : str
        Route short name.
    trip_id : str
        Trip ID.

    Returns
    -------
    Departure
        The departure.
    """
    start = at(*departs)
    ride = int((at(*arrives) - start).total_seconds()) // 60
    return departure(departs=start, ride=ride, route=route, trip_id=trip_id)


LEG1: Final[tuple[Departure, ...]] = tuple(
    run(departs=leave, arrives=arrive, route='606', trip_id=trip_id)
    for leave, arrive, trip_id in (
        ((7, 33), (7, 39), 'a0733'),
        ((7, 48), (7, 54), 'a0748'),
        ((8, 3), (8, 8), 'a0803'),
        ((8, 22), (8, 27), 'a0822'),
        ((8, 42), (8, 46), 'a0842'),
    )
)
LEG2: Final[tuple[Departure, ...]] = tuple(
    run(departs=leave, arrives=arrive, route=route, trip_id=trip_id)
    for route, leave, arrive, trip_id in (
        ('665', (7, 46), (7, 57), 'b0746'),
        ('662', (7, 51), (8, 2), 'b0751'),
        ('664', (8, 1), (8, 12), 'b0801a'),
        ('660', (8, 1), (8, 12), 'b0801b'),
        ('665', (8, 17), (8, 27), 'b0817'),
        ('663', (8, 19), (8, 30), 'b0819'),
        ('665', (8, 36), (8, 45), 'b0836'),
        ('663', (8, 38), (8, 49), 'b0838'),
        ('665', (8, 55), (9, 4), 'b0855'),
        ('661', (8, 58), (9, 8), 'b0858'),
    )
)


def work_legs(
    *,
    pin: Pin = WORK,
    first: tuple[Departure, ...] = LEG1,
    second: tuple[Departure, ...] = LEG2,
) -> Legs:
    """
    Load a one-change pin's two way legs.

    Parameters
    ----------
    pin : Pin
        A pin with one one-change way, Work by default.
    first : tuple[Departure, ...]
        Leg-1 departures, From to Hammers Rd.
    second : tuple[Departure, ...]
        Leg-2 departures, Constitution Hill to Westmead.

    Returns
    -------
    Legs
        Both lists by their stops.
    """
    one, two = way_legs(pin=pin, way=pin.ways[0])
    return Legs(lists=MappingProxyType({
        leg_key(leg=one): first,
        leg_key(leg=two): second,
    }))


def work_chains(*, pin: Pin = WORK, now: datetime | None = None) -> tuple[
    Chain, ...,
]:
    """
    Find Work's chains over `LEG1` and `LEG2` without live times.

    Parameters
    ----------
    pin : Pin
        A pin with one one-change way, Work by default.
    now : datetime | None
        Current time; 7:30 by default.

    Returns
    -------
    tuple[Chain, ...]
        The workable chains.
    """
    return chains_of(
        pin=pin,
        lists={
            key: tuple(live(item=item) for item in items)
            for key, items in work_legs(pin=pin).lists.items()
        },
        direct=(),
        now=now or at(7, 30),
    )
