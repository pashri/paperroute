"""Chains: one trip per leg along a pin's ways, or a direct trip."""

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType

from paperroute.core.connection_model import TripKey, trip_key, way_legs
from paperroute.core.live import LiveDeparture
from paperroute_transit.core.model import Departure, Pin, Way, ticked_ways

type LegKey = tuple[str, str]


@dataclass(frozen=True)
class Legs:
    """
    Departures of a connection pin's way legs.

    Attributes
    ----------
    lists : Mapping[LegKey, tuple[Departure, ...]]
        Each distinct leg's departures, by its stops. The direct list is
        the board's own departures, not one of these.
    """

    lists: Mapping[LegKey, tuple[Departure, ...]] = field(
        default_factory=lambda: MappingProxyType({}),
    )


def all_departures(
    *, departures: Sequence[Departure], legs: Legs | None,
) -> tuple[Departure, ...]:
    """
    List every departure a pin holds.

    Parameters
    ----------
    departures : Sequence[Departure]
        The pin's own departures; the direct list for a pin with a To.
    legs : Legs | None
        The way legs, or None for a plain pin.

    Returns
    -------
    tuple[Departure, ...]
        The pin's own departures, then each leg list in turn.
    """
    if legs is None:
        return tuple(departures)
    return (
        *departures,
        *(item for items in legs.lists.values() for item in items),
    )


def open_legs(*, pin: Pin) -> dict[LegKey, tuple[Pin, int]]:
    """
    List the distinct legs of a pin's ticked ways.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    dict[LegKey, tuple[Pin, int]]
        Each leg pin by its stops, in the order first met, with the
        latest position (0 for a first leg) it takes in any way.
    """
    found: dict[LegKey, tuple[Pin, int]] = {}
    for way in ticked_ways(pin=pin):
        for position, leg in enumerate(way_legs(pin=pin, way=way)):
            key = leg_key(leg=leg)
            known = found.get(key, (leg, position))
            found[key] = (leg, max(position, known[1]))
    return found


@dataclass(frozen=True)
class Chain:
    """
    One way through a pin: the trip taken on each leg.

    Attributes
    ----------
    legs : tuple[LiveDeparture, ...]
        The trips in order. Two neighbours on the same trip mean staying
        on at that change.
    way : int | None
        Index of the way in the pin's ways, or None for a direct trip.
    """

    legs: tuple[LiveDeparture, ...]
    way: int | None

    @property
    def departs(self) -> datetime:
        """
        Say when the first leg leaves.

        Returns
        -------
        datetime
            The first leg's departure.
        """
        return self.legs[0].departs

    @property
    def arrives(self) -> datetime:
        """
        Say when the chain reaches the Destination.

        Returns
        -------
        datetime
            The last leg's arrival.
        """
        return self.legs[-1].arrives

    @property
    def changes(self) -> int:
        """
        Count the changes of vehicle.

        Returns
        -------
        int
            Neighbouring legs on different trips.
        """
        return sum(
            trip_key(item=first) != trip_key(item=second)
            for first, second in zip(self.legs, self.legs[1:])
        )


@dataclass(frozen=True)
class LegList:
    """
    One leg's departures, ready to search.

    Attributes
    ----------
    items : tuple[LiveDeparture, ...]
        Departures sorted by when they leave.
    keys : tuple[datetime, ...]
        Each departure's leaving time, for bisecting.
    trips : Mapping[TripKey, LiveDeparture]
        Departures by trip.
    """

    items: tuple[LiveDeparture, ...]
    keys: tuple[datetime, ...]
    trips: Mapping[TripKey, LiveDeparture]


def leg_key(*, leg: Pin) -> LegKey:
    """
    Name the stops a leg runs between.

    Parameters
    ----------
    leg : Pin
        Leg pin.

    Returns
    -------
    LegKey
        From and To stop IDs; '' for a missing To.
    """
    destination = leg.destination
    return (
        leg.origin.stop_id,
        '' if destination is None else destination.stop_id,
    )


def leg_list(
    *, items: Sequence[LiveDeparture], never: Sequence[str],
) -> LegList:
    """
    Prepare one leg's departures for searching.

    Parameters
    ----------
    items : Sequence[LiveDeparture]
        Departures sorted by when they leave.
    never : Sequence[str]
        Route short names never used.

    Returns
    -------
    LegList
        The departures not on a never route.
    """
    kept = tuple(
        item for item in items if item.departure.route not in never
    )
    return LegList(
        items=kept,
        keys=tuple(item.departs for item in kept),
        trips={trip_key(item=item): item for item in kept},
    )


def next_leg(
    *, after: LiveDeparture, leg: LegList, allow: int,
) -> LiveDeparture | None:
    """
    Find the trip that gets to a leg's end soonest after a change.

    Parameters
    ----------
    after : LiveDeparture
        The trip the change is from.
    leg : LegList
        The next leg's departures.
    allow : int
        Minutes allowed for the change.

    Returns
    -------
    LiveDeparture | None
        The uncancelled trip leaving at least `allow` minutes after
        `after` arrives with the earliest arrival, then departure; the
        first listed wins a tie. None when none can be caught.
    """
    ready = after.arrives + timedelta(minutes=allow)
    best: LiveDeparture | None = None
    for item in leg.items[bisect.bisect_left(leg.keys, ready):]:
        if best is not None and item.departs > best.arrives:
            break
        if item.cancelled:
            continue
        if best is None or (item.arrives, item.departs) < (
            best.arrives, best.departs,
        ):
            best = item
    return best


def best_of(
    options: Sequence[tuple[LiveDeparture, ...]],
) -> tuple[LiveDeparture, ...] | None:
    """
    Pick the finished leg sequence that arrives first.

    Parameters
    ----------
    options : Sequence[tuple[LiveDeparture, ...]]
        Finished sequences; staying on comes before changing.

    Returns
    -------
    tuple[LiveDeparture, ...] | None
        The earliest arrival, then the fewest changes, then the first
        listed; None without options.
    """
    return min(
        options,
        key=lambda legs: (
            legs[-1].arrives, Chain(legs=legs, way=None).changes,
        ),
        default=None,
    )


def stayable(*, stay: LiveDeparture, current: LiveDeparture) -> bool:
    """
    Tell whether a trip can be stayed on into the next leg.

    Parameters
    ----------
    stay : LiveDeparture
        The same trip on the next leg.
    current : LiveDeparture
        The trip as ridden on this leg.

    Returns
    -------
    bool
        True when it runs and leaves the next leg's stop no earlier than
        it reaches this leg's alight stop; a looping trip can pass the
        next stop earlier too.
    """
    return not stay.cancelled and stay.departs >= current.arrives


def walked(
    *,
    taken: tuple[LiveDeparture, ...],
    index: int,
    way: Way,
    legs: Sequence[LegList],
) -> tuple[LiveDeparture, ...] | None:
    """
    Finish a chain from the trip taken on one leg.

    Parameters
    ----------
    taken : tuple[LiveDeparture, ...]
        Trips so far; the last is on leg `index`.
    index : int
        Leg the last trip is on.
    way : Way
        The way walked.
    legs : Sequence[LegList]
        Each leg's departures.

    Returns
    -------
    tuple[LiveDeparture, ...] | None
        The best finished trips, staying on winning ties; None when no
        trip can be caught onward.
    """
    if index == len(legs) - 1:
        return taken
    current = taken[-1]
    options = []
    stay = legs[index + 1].trips.get(trip_key(item=current))
    if stay is not None and stayable(stay=stay, current=current):
        options.append(
            walked(taken=(*taken, stay), index=index + 1, way=way, legs=legs),
        )
    onward = next_leg(
        after=current, leg=legs[index + 1], allow=way.changes[index].allow,
    )
    if onward is not None:
        options.append(
            walked(
                taken=(*taken, onward), index=index + 1, way=way, legs=legs,
            ),
        )
    return best_of([option for option in options if option is not None])


def way_chains(
    *,
    pin: Pin,
    position: int,
    lists: Mapping[LegKey, Sequence[LiveDeparture]],
    now: datetime,
    cache: dict[LegKey, LegList],
) -> list[Chain]:
    """
    Walk one way from each trip on its first leg.

    Parameters
    ----------
    pin : Pin
        Pin.
    position : int
        Index of the way in the pin's ways.
    lists : Mapping[LegKey, Sequence[LiveDeparture]]
        Departures by leg, sorted by when they leave.
    now : datetime
        Current time.
    cache : dict[LegKey, LegList]
        Leg lists already built for this pin; filled in as legs are built.

    Returns
    -------
    list[Chain]
        One chain per first-leg trip still to leave that can finish.
    """
    way = pin.ways[position]
    legs = []
    for leg in way_legs(pin=pin, way=way):
        key = leg_key(leg=leg)
        if key not in cache:
            cache[key] = leg_list(items=lists.get(key, ()), never=pin.never)
        legs.append(cache[key])
    found = (
        walked(taken=(item,), index=0, way=way, legs=legs)
        for item in legs[0].items
        if not item.cancelled and item.departs >= now
    )
    return [
        Chain(legs=taken, way=position) for taken in found if taken
    ]


def best_per_first(*, chains: Sequence[Chain]) -> list[Chain]:
    """
    Keep the best chain for each first-leg trip.

    Parameters
    ----------
    chains : Sequence[Chain]
        Direct chains, then each way's in way order.

    Returns
    -------
    list[Chain]
        Per first trip, the earliest arrival, then the fewest changes,
        then a direct trip, then the first way.
    """
    best: dict[TripKey, Chain] = {}
    for chain in chains:
        key = trip_key(item=chain.legs[0])
        held = best.get(key)
        if held is None or (chain.arrives, chain.changes) < (
            held.arrives, held.changes,
        ):
            best[key] = chain
    return list(best.values())


def latest_per_final(*, chains: Sequence[Chain]) -> tuple[Chain, ...]:
    """
    Drop chains that leave earlier for the same last trip as another.

    Parameters
    ----------
    chains : Sequence[Chain]
        Chains in first-leg departure order.

    Returns
    -------
    tuple[Chain, ...]
        For each last-leg trip, the chain whose first leg leaves last.
    """
    last = {
        trip_key(item=chain.legs[-1]): index
        for index, chain in enumerate(chains)
    }
    return tuple(
        chain
        for index, chain in enumerate(chains)
        if last[trip_key(item=chain.legs[-1])] == index
    )


def chains_of(
    *,
    pin: Pin,
    lists: Mapping[LegKey, Sequence[LiveDeparture]],
    direct: Sequence[LiveDeparture],
    now: datetime,
) -> tuple[Chain, ...]:
    """
    Find the workable chains across a pin's ticked ways and direct trips.

    Parameters
    ----------
    pin : Pin
        Pin with a Destination.
    lists : Mapping[LegKey, Sequence[LiveDeparture]]
        Departures by leg, live times applied, sorted by when they leave.
    direct : Sequence[LiveDeparture]
        Departures from the From to the Destination, sorted likewise.
    now : datetime
        Current time.

    Returns
    -------
    tuple[Chain, ...]
        Chains in first-leg departure order. Routes in the pin's never
        list are not used, first legs that have left or are cancelled
        are dropped, and of chains ending on one trip only the last to
        leave is kept.
    """
    found = [
        Chain(legs=(item,), way=None)
        for item in leg_list(items=direct, never=pin.never).items
        if not item.cancelled and item.departs >= now
    ]
    cache: dict[LegKey, LegList] = {}
    for position, way in enumerate(pin.ways):
        if way.ticked:
            found.extend(way_chains(
                pin=pin, position=position, lists=lists, now=now,
                cache=cache,
            ))
    kept = best_per_first(chains=found)
    return latest_per_final(
        chains=sorted(kept, key=lambda chain: chain.departs),
    )
