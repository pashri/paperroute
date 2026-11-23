"""Chains: one trip per leg along a pin's ways, or a direct trip."""

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import MappingProxyType

from paperroute_transit.core.connection_model import (
    TripKey,
    spare_seconds,
    trip_key,
    way_legs,
)
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import (
    Change,
    Departure,
    Pin,
    Way,
    ticked_ways,
)

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
class Missed:
    """
    A remembered change that can no longer be made.

    Attributes
    ----------
    leg : int
        Index in the chain's legs of the first leg after the missed
        change.
    arrives : datetime | None
        When the chain first offered arrives, live; None when its last
        trip is no longer listed.
    later : bool
        True when the chain goes on by a later trip; False when the legs
        from `leg` on are the missed ones.
    """

    leg: int
    arrives: datetime | None
    later: bool


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
    missed : Missed | None
        The remembered change this chain could not make, or None.
    """

    legs: tuple[LiveDeparture, ...]
    way: int | None
    missed: Missed | None = None

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
class Offer:
    """
    The trips a chain was first offered on.

    Attributes
    ----------
    way : int
        Index of the way in the pin's ways.
    trips : tuple[TripKey, ...]
        The trip taken on each leg.
    """

    way: int
    trips: tuple[TripKey, ...]


@dataclass(frozen=True)
class Offers:
    """
    The chains a pin has offered, remembered by their first trip.

    Attributes
    ----------
    pin : Pin | None
        The pin the offers were made for, or None before any.
    kept : Mapping[TripKey, Offer]
        Each offer by its first leg's trip.
    """

    pin: Pin | None = None
    kept: Mapping[TripKey, Offer] = field(
        default_factory=lambda: MappingProxyType({}),
    )


def kept_for(*, offers: Offers, pin: Pin) -> Mapping[TripKey, Offer]:
    """
    Give the offers remembered for a pin.

    Parameters
    ----------
    offers : Offers
        Offers remembered.
    pin : Pin
        The pin now shown.

    Returns
    -------
    Mapping[TripKey, Offer]
        The offers when they were made for this pin, else none.
    """
    if offers.pin == pin:
        return offers.kept
    return MappingProxyType({})


def offer_of(
    *, chain: Chain, held: Mapping[TripKey, Offer],
) -> Offer | None:
    """
    Give the offer to remember for one listed chain.

    Parameters
    ----------
    chain : Chain
        A chain listed.
    held : Mapping[TripKey, Offer]
        Offers held for the pin, by first trip.

    Returns
    -------
    Offer | None
        The offer held for its first trip when the chain changes vehicle
        or carries a miss; else its own trips when it is on a way and
        changes vehicle; else None.
    """
    offer = held.get(trip_key(item=chain.legs[0]))
    if offer is not None and (chain.changes or chain.missed is not None):
        return offer
    if chain.way is None or not chain.changes:
        return None
    return Offer(
        way=chain.way,
        trips=tuple(trip_key(item=item) for item in chain.legs),
    )


def offers_after(
    *, offers: Offers, pin: Pin, chains: Sequence[Chain],
) -> Offers:
    """
    Remember the chains now listed that change vehicle or carry a miss.

    Parameters
    ----------
    offers : Offers
        Offers remembered so far.
    pin : Pin
        The pin the chains are for.
    chains : Sequence[Chain]
        The chains listed.

    Returns
    -------
    Offers
        For each chain, the offer `offer_of` gives, by its first trip.
        Other first trips are forgotten.
    """
    held = kept_for(offers=offers, pin=pin)
    kept: dict[TripKey, Offer] = {}
    for chain in chains:
        offer = offer_of(chain=chain, held=held)
        if offer is not None:
            kept[trip_key(item=chain.legs[0])] = offer
    return Offers(pin=pin, kept=MappingProxyType(kept))


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


def holds(
    *, before: LiveDeparture, onward: LiveDeparture, change: Change,
) -> bool:
    """
    Tell whether a remembered change can still be made.

    Parameters
    ----------
    before : LiveDeparture
        The trip the change is from.
    onward : LiveDeparture
        The trip remembered for the next leg.
    change : Change
        The change.

    Returns
    -------
    bool
        For the same trip, whether it can be stayed on. Otherwise
        whether the onward trip runs and either leaves time spare after
        the walk or still meets the change's allowance.
    """
    if trip_key(item=onward) == trip_key(item=before):
        return stayable(stay=onward, current=before)
    if onward.cancelled:
        return False
    spare = spare_seconds(
        arrives=before.arrives, departs=onward.departs, change=change,
    )
    ready = before.arrives + timedelta(minutes=change.allow)
    return spare > 0 or onward.departs >= ready


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


def way_lists(
    *,
    pin: Pin,
    way: Way,
    lists: Mapping[LegKey, Sequence[LiveDeparture]],
    cache: dict[LegKey, LegList],
) -> list[LegList]:
    """
    Prepare each leg of a way for searching.

    Parameters
    ----------
    pin : Pin
        Pin.
    way : Way
        One of its ways.
    lists : Mapping[LegKey, Sequence[LiveDeparture]]
        Departures by leg, sorted by when they leave.
    cache : dict[LegKey, LegList]
        Leg lists already built for this pin; filled in as legs are built.

    Returns
    -------
    list[LegList]
        Each leg's departures, in the way's order.
    """
    legs = []
    for leg in way_legs(pin=pin, way=way):
        key = leg_key(leg=leg)
        if key not in cache:
            cache[key] = leg_list(items=lists.get(key, ()), never=pin.never)
        legs.append(cache[key])
    return legs


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
    legs = way_lists(pin=pin, way=way, lists=lists, cache=cache)
    found = (
        walked(taken=(item,), index=0, way=way, legs=legs)
        for item in legs[0].items
        if not item.cancelled and item.departs >= now
    )
    return [
        Chain(legs=taken, way=position) for taken in found if taken
    ]


def held_chain(
    *, offer: Offer, first: LiveDeparture, way: Way, legs: Sequence[LegList],
) -> Chain | None:
    """
    Follow a remembered chain while each change can still be made.

    Parameters
    ----------
    offer : Offer
        The chain first offered.
    first : LiveDeparture
        Its first trip as now listed.
    way : Way
        The offer's way.
    legs : Sequence[LegList]
        Each leg's departures.

    Returns
    -------
    Chain | None
        The offered trips when every change holds; else the chain
        `missed_chain` gives from the first change that does not.
    """
    taken: tuple[LiveDeparture, ...] = (first,)
    for index, key in enumerate(offer.trips[1:], start=1):
        onward = legs[index].trips.get(key)
        if onward is not None and holds(
            before=taken[-1], onward=onward, change=way.changes[index - 1],
        ):
            taken = (*taken, onward)
            continue
        return missed_chain(offer=offer, taken=taken, way=way, legs=legs)
    return Chain(legs=taken, way=offer.way)


def missed_chain(
    *,
    offer: Offer,
    taken: tuple[LiveDeparture, ...],
    way: Way,
    legs: Sequence[LegList],
) -> Chain | None:
    """
    Finish a remembered chain past the change it missed.

    Parameters
    ----------
    offer : Offer
        The chain first offered.
    taken : tuple[LiveDeparture, ...]
        Its trips up to the missed change.
    way : Way
        The offer's way.
    legs : Sequence[LegList]
        Each leg's departures.

    Returns
    -------
    Chain | None
        The best chain on from the last trip taken, the miss noted; else
        the offered trips, noted as missed; None when those are no
        longer all listed.
    """
    index = len(taken)
    old = [
        leg.trips.get(key)
        for leg, key in zip(legs[index:], offer.trips[index:], strict=True)
    ]
    arrives = None if old[-1] is None else old[-1].arrives
    rest = walked(taken=taken, index=index - 1, way=way, legs=legs)
    if rest is not None:
        return Chain(
            legs=rest,
            way=offer.way,
            missed=Missed(leg=index, arrives=arrives, later=True),
        )
    found = tuple(item for item in old if item is not None)
    if len(found) < len(old):
        return None
    return Chain(
        legs=(*taken, *found),
        way=offer.way,
        missed=Missed(leg=index, arrives=arrives, later=False),
    )


def offered_way(*, pin: Pin, offer: Offer) -> Way | None:
    """
    Find the way an offer was made on, if it can still be held.

    Parameters
    ----------
    pin : Pin
        Pin.
    offer : Offer
        A remembered offer.

    Returns
    -------
    Way | None
        The way when the pin has it, it is ticked and it has one leg per
        offered trip; else None.
    """
    if not 0 <= offer.way < len(pin.ways):
        return None
    way = pin.ways[offer.way]
    if not way.ticked or len(offer.trips) != len(way.changes) + 1:
        return None
    return way


def held_chains(
    *,
    pin: Pin,
    kept: Mapping[TripKey, Offer],
    lists: Mapping[LegKey, Sequence[LiveDeparture]],
    now: datetime,
    cache: dict[LegKey, LegList],
) -> list[Chain]:
    """
    Follow each remembered chain whose first trip is still to leave.

    Parameters
    ----------
    pin : Pin
        Pin.
    kept : Mapping[TripKey, Offer]
        Offers remembered for the pin, by first trip.
    lists : Mapping[LegKey, Sequence[LiveDeparture]]
        Departures by leg, sorted by when they leave.
    now : datetime
        Current time.
    cache : dict[LegKey, LegList]
        Leg lists already built for this pin; filled in as legs are built.

    Returns
    -------
    list[Chain]
        One chain per offer on a way still held whose first trip is
        listed, runs and has not left, and which `held_chain` can give.
    """
    found = []
    for offer in kept.values():
        way = offered_way(pin=pin, offer=offer)
        if way is None:
            continue
        legs = way_lists(pin=pin, way=way, lists=lists, cache=cache)
        first = legs[0].trips.get(offer.trips[0])
        if first is None or first.cancelled or first.departs < now:
            continue
        chain = held_chain(offer=offer, first=first, way=way, legs=legs)
        if chain is not None:
            found.append(chain)
    return found


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


def held_or_fresh(
    *, held: Chain | None, fresh: Chain, kept: Mapping[TripKey, Offer],
) -> Chain:
    """
    Choose between a held chain and the best fresh one for its first trip.

    Parameters
    ----------
    held : Chain | None
        The chain the first trip's offer gives, or None.
    fresh : Chain
        The best chain the search found for the same first trip.
    kept : Mapping[TripKey, Offer]
        Offers remembered for the pin, by first trip.

    Returns
    -------
    Chain
        The held chain when it misses nothing, or goes on by a later trip
        arriving no later than the fresh one. Otherwise the fresh chain,
        carrying the held chain's miss; `later` is False only when its
        legs from the missed change on are the offered trips.
    """
    if held is None:
        return fresh
    missed = held.missed
    if missed is None or (missed.later and held.arrives <= fresh.arrives):
        return held
    offer = kept[trip_key(item=held.legs[0])]
    rest = tuple(trip_key(item=item) for item in fresh.legs[missed.leg:])
    return replace(
        fresh,
        missed=replace(missed, later=rest != offer.trips[missed.leg:]),
    )


def latest_per_final(
    *, chains: Sequence[Chain], held: frozenset[TripKey] = frozenset(),
) -> tuple[Chain, ...]:
    """
    Drop chains that leave earlier for the same last trip as another.

    Parameters
    ----------
    chains : Sequence[Chain]
        Chains in first-leg departure order.
    held : frozenset[TripKey]
        First trips whose chains are never dropped.

    Returns
    -------
    tuple[Chain, ...]
        For each last-leg trip, the chain whose first leg leaves last,
        and every chain whose first trip is held.
    """
    last = {
        trip_key(item=chain.legs[-1]): index
        for index, chain in enumerate(chains)
    }
    return tuple(
        chain
        for index, chain in enumerate(chains)
        if trip_key(item=chain.legs[0]) in held
        or last[trip_key(item=chain.legs[-1])] == index
    )


def chains_of(
    *,
    pin: Pin,
    lists: Mapping[LegKey, Sequence[LiveDeparture]],
    direct: Sequence[LiveDeparture],
    now: datetime,
    kept: Mapping[TripKey, Offer] = MappingProxyType({}),
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
    kept : Mapping[TripKey, Offer]
        Offers remembered for the pin, by first trip.

    Returns
    -------
    tuple[Chain, ...]
        Chains in first-leg departure order. Routes in the pin's never
        list are not used, first legs that have left or are cancelled
        are dropped, and of chains ending on one trip only the last to
        leave is kept. A held first trip takes the chain its offer
        gives, and that chain is never dropped. After a missed change,
        that chain gives way to the fresh best chain for its first trip,
        the miss carried over, when it has no onward plan or arrives
        later.
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
    held = held_chains(pin=pin, kept=kept, lists=lists, now=now, cache=cache)
    swap = {trip_key(item=chain.legs[0]): chain for chain in held}
    best = [
        held_or_fresh(
            held=swap.pop(trip_key(item=chain.legs[0]), None),
            fresh=chain,
            kept=kept,
        )
        for chain in best_per_first(chains=found)
    ]
    return latest_per_final(
        chains=sorted(
            [*best, *swap.values()], key=lambda chain: chain.departs,
        ),
        held=frozenset(trip_key(item=chain.legs[0]) for chain in held),
    )
