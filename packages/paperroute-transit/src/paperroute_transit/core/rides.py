"""Rides: a chain's legs with each stay on one vehicle folded together."""

from dataclasses import dataclass
from datetime import datetime

from paperroute_transit.core.chains import Chain
from paperroute_transit.core.connection_model import (
    direct_leg,
    trip_key,
    way_legs,
)
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import Change, Pin, StopPlace


@dataclass(frozen=True)
class Ride:
    """
    One vehicle ridden from boarding to getting off.

    Attributes
    ----------
    item : LiveDeparture
        The leg it is boarded on.
    last : LiveDeparture
        The leg it is left on; `item` itself unless staying on.
    start : StopPlace
        Where it is boarded.
    end : StopPlace
        Where it is left.
    change : Change | None
        The change made to board it; None for the first ride.
    stays : bool
        True when it folds legs stayed on through a change.
    """

    item: LiveDeparture
    last: LiveDeparture
    start: StopPlace
    end: StopPlace
    change: Change | None = None
    stays: bool = False

    @property
    def departs(self) -> datetime:
        """
        Say when the ride leaves.

        Returns
        -------
        datetime
            The boarded leg's departure.
        """
        return self.item.departs

    @property
    def arrives(self) -> datetime:
        """
        Say when the ride is left.

        Returns
        -------
        datetime
            The last leg's arrival.
        """
        return self.last.arrives


def chain_ends(
    *, chain: Chain, pin: Pin,
) -> tuple[tuple[Pin, ...], tuple[Change, ...]]:
    """
    Give the leg pins and changes of the way a chain takes.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[tuple[Pin, ...], tuple[Change, ...]]
        The direct leg and no changes for a direct chain, else the way's
        leg pins and changes.
    """
    if chain.way is None:
        return (direct_leg(pin=pin),), ()
    way = pin.ways[chain.way]
    return way_legs(pin=pin, way=way), way.changes


def rides_of(*, chain: Chain, pin: Pin) -> tuple[Ride, ...]:
    """
    Fold a chain's legs into the vehicles ridden.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[Ride, ...]
        One ride per vehicle, in order; neighbouring legs on one trip
        become one ride that stays on.
    """
    legs, changes = chain_ends(chain=chain, pin=pin)
    rides: list[Ride] = []
    for index, (item, leg) in enumerate(zip(chain.legs, legs, strict=True)):
        end = leg.destination or leg.origin
        if rides and trip_key(item=rides[-1].item) == trip_key(item=item):
            held = rides.pop()
            rides.append(Ride(
                item=held.item, last=item, start=held.start, end=end,
                change=held.change, stays=True,
            ))
            continue
        rides.append(Ride(
            item=item, last=item, start=leg.origin, end=end,
            change=changes[index - 1] if index else None,
        ))
    return tuple(rides)
