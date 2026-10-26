"""Connection pins: their legs, the walk between them and their pairs."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.model import Departure, Pin, StopPlace

LEG_TWO_EXTRA: Final[timedelta] = timedelta(hours=2)
WALK_STEP: Final[int] = 10  # metres the walk is rounded up to
WALK_PACE: Final[int] = 80  # metres walked a minute
SPARE_MINUTES: Final[int] = 2

type TripKey = tuple[str, date]


@dataclass(frozen=True)
class Legs:
    """
    Departures of a connection pin beyond its first leg.

    Attributes
    ----------
    second : tuple[Departure, ...]
        Leg-2 departures from the board stop to the destination.
    direct : tuple[Departure, ...]
        Leg-1 route departures that run on to the destination.
    """

    second: tuple[Departure, ...] = ()
    direct: tuple[Departure, ...] = ()


@dataclass(frozen=True)
class Pair:
    """
    One way through a connection: a first leg and the second it meets.

    Attributes
    ----------
    first : LiveDeparture
        Leg-1 departure, or the direct departure when staying on.
    second : LiveDeparture | None
        Leg-2 departure, or None when staying on.
    """

    first: LiveDeparture
    second: LiveDeparture | None

    @property
    def arrives(self) -> datetime:
        """
        Say when the pair reaches the destination.

        Returns
        -------
        datetime
            Leg 2's arrival, or the first leg's when staying on.
        """
        return self.first.arrives if self.second is None else (
            self.second.arrives
        )


def leg_pins(*, pin: Pin) -> tuple[Pin, Pin, Pin]:
    """
    Split a connection pin into its two legs and its direct pin.

    Parameters
    ----------
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[Pin, Pin, Pin]
        Leg 1 (From to the alight stop on the pin's routes), leg 2 (board
        stop to the destination on any route, filtered by the change's
        `only`) and the direct pin (From to the destination on the pin's
        routes).

    Raises
    ------
    ValueError
        When the pin has no change.
    """
    change = pin.change
    if change is None:
        raise ValueError('A pin without a change has no legs.')
    first = Pin(
        origin=pin.origin,
        destination=change.alight,
        kind=pin.kind,
        routes=pin.routes,
    )
    second = Pin(
        origin=change.board,
        destination=pin.destination,
        kind=None,
        routes=(),
        only=change.only,
    )
    direct = Pin(
        origin=pin.origin,
        destination=pin.destination,
        kind=pin.kind,
        routes=pin.routes,
    )
    return first, second, direct


def all_departures(
    *, departures: Sequence[Departure], legs: Legs | None,
) -> tuple[Departure, ...]:
    """
    List every departure a connection pin holds.

    Parameters
    ----------
    departures : Sequence[Departure]
        Leg-1 departures.
    legs : Legs | None
        The other legs, or None for a direct pin.

    Returns
    -------
    tuple[Departure, ...]
        Leg 1, then leg 2, then the direct list.
    """
    if legs is None:
        return tuple(departures)
    return (*departures, *legs.second, *legs.direct)


def walk_metres(*, alight: StopPlace, board: StopPlace) -> int:
    """
    Measure the walk between two stops in a straight line.

    Parameters
    ----------
    alight : StopPlace
        Stop left.
    board : StopPlace
        Stop joined.

    Returns
    -------
    int
        Metres rounded up to the next 10; 0 when either has no point.
    """
    if alight.point is None or board.point is None:
        return 0
    metres = math.dist(alight.point, board.point)
    return math.ceil(metres / WALK_STEP) * WALK_STEP


def default_allow(*, metres: int) -> int:
    """
    Suggest the minutes to allow for a change.

    Parameters
    ----------
    metres : int
        Walk in metres.

    Returns
    -------
    int
        Minutes to walk at 80 m a minute, rounded up, plus 2.
    """
    return math.ceil(metres / WALK_PACE) + SPARE_MINUTES


def trip_key(*, item: LiveDeparture) -> TripKey:
    """
    Name a departure's trip on its service day.

    Parameters
    ----------
    item : LiveDeparture
        Departure.

    Returns
    -------
    TripKey
        Trip ID and service day.
    """
    return item.departure.trip_id, item.departure.service_day


def catchable(
    *, first: LiveDeparture, second: Sequence[LiveDeparture], allow: int,
) -> LiveDeparture | None:
    """
    Find the leg 2 that gets there soonest after a leg 1.

    Parameters
    ----------
    first : LiveDeparture
        Leg-1 departure.
    second : Sequence[LiveDeparture]
        Leg-2 departures in loaded order.
    allow : int
        Minutes allowed for the change.

    Returns
    -------
    LiveDeparture | None
        The uncancelled leg 2 leaving at least `allow` minutes after leg 1
        arrives with the earliest arrival, then departure; the first loaded
        wins a tie. None when none can be caught.
    """
    ready = first.arrives + timedelta(minutes=allow)
    options = [
        item
        for item in second
        if not item.cancelled and item.departs >= ready
    ]
    return min(
        options,
        key=lambda item: (item.arrives, item.departs),
        default=None,
    )


def pair_for(
    *,
    first: LiveDeparture,
    second: Sequence[LiveDeparture],
    twins: Mapping[TripKey, LiveDeparture],
    allow: int,
) -> Pair | None:
    """
    Pair one leg 1 with its leg 2, or with staying on.

    Parameters
    ----------
    first : LiveDeparture
        Leg-1 departure.
    second : Sequence[LiveDeparture]
        Leg-2 departures in loaded order.
    twins : Mapping[TripKey, LiveDeparture]
        Direct departures by trip.
    allow : int
        Minutes allowed for the change.

    Returns
    -------
    Pair | None
        The faster of changing and staying on, staying on in a tie; None
        when neither works.
    """
    onward = catchable(first=first, second=second, allow=allow)
    twin = twins.get(trip_key(item=first))
    if twin is not None and (onward is None or twin.arrives <= onward.arrives):
        return Pair(first=twin, second=None)
    return None if onward is None else Pair(first=first, second=onward)


def pairs_of(
    *,
    first: Sequence[LiveDeparture],
    second: Sequence[LiveDeparture],
    direct: Sequence[LiveDeparture],
    allow: int,
    now: datetime,
) -> tuple[Pair, ...]:
    """
    Pair each leg 1 with the leg 2 that gets there first.

    Parameters
    ----------
    first : Sequence[LiveDeparture]
        Leg-1 departures in loaded order.
    second : Sequence[LiveDeparture]
        Leg-2 departures in loaded order.
    direct : Sequence[LiveDeparture]
        Leg-1 route departures that run on to the destination.
    allow : int
        Minutes allowed for the change.
    now : datetime
        Current time.

    Returns
    -------
    tuple[Pair, ...]
        Workable pairs in leg-1 departure order. A direct trip with no
        leg-1 twin is a stay pair. Pairs whose first leg has left or is
        cancelled are dropped.
    """
    twins = {trip_key(item=item): item for item in direct}
    known = {trip_key(item=item) for item in first}
    found = [
        pair_for(first=item, second=second, twins=twins, allow=allow)
        for item in first
    ]
    found.extend(
        Pair(first=item, second=None)
        for item in direct
        if trip_key(item=item) not in known
    )
    kept = [
        pair
        for pair in found
        if pair is not None
        and not pair.first.cancelled
        and pair.first.departs >= now
    ]
    return tuple(sorted(kept, key=lambda pair: pair.first.departs))
