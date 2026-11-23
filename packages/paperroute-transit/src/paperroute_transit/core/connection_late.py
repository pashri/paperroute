"""Lateness marks and change fates on connection pins."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Final

from paperroute_transit.core.chains import Chain
from paperroute_transit.core.connection_model import (
    TIGHT_SPARE,
    spare_seconds,
    trip_key,
)
from paperroute_transit.core.late_marks import signed
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.rides import Ride
from paperroute_transit.core.stop_view import late_minutes

TIGHT: Final[str] = 'tight'
MISSED: Final[str] = 'change missed'


class ChangeFate(StrEnum):
    """How a change stands."""

    EASY = 'easy'
    TIGHT = 'tight'
    MISSED = 'missed'
    STRANDED = 'stranded'


def leg_mark(*, item: LiveDeparture) -> str:
    """
    Mark how late a leg runs.

    Parameters
    ----------
    item : LiveDeparture
        The leg.

    Returns
    -------
    str
        The signed minutes late, with ``?`` for a guess; empty unless
        the leg is live and running, or when the lateness is too small
        to show.
    """
    if not item.live or item.cancelled:
        return ''
    return signed(
        late=late_minutes(item=item),
        uncertain=item.estimated and item.uncertain,
    )


def arrival_mark(*, item: LiveDeparture) -> str:
    """
    Mark how late a leg arrives.

    Parameters
    ----------
    item : LiveDeparture
        The leg.

    Returns
    -------
    str
        As `leg_mark`, from the arrival's clock minute less the
        timetabled one.
    """
    if not item.live or item.cancelled:
        return ''
    late = int(item.arrives.timestamp() // 60) - int(
        item.departure.arrives.timestamp() // 60,
    )
    return signed(late=late, uncertain=item.estimated and item.uncertain)


def missed_item(*, chain: Chain) -> LiveDeparture | None:
    """
    Give the leg a chain's missed change boards.

    Parameters
    ----------
    chain : Chain
        The chain.

    Returns
    -------
    LiveDeparture | None
        The leg at the miss's index, or the last leg when the index is
        not one of the chain's; None without a miss.
    """
    if chain.missed is None:
        return None
    if chain.missed.leg in range(len(chain.legs)):
        return chain.legs[chain.missed.leg]
    return chain.legs[-1]


def ride_fate(
    *, chain: Chain, ride: Ride, before: Ride | None,
) -> ChangeFate:
    """
    Judge the change made to board one ride.

    Parameters
    ----------
    chain : Chain
        The chain.
    ride : Ride
        The ride.
    before : Ride | None
        The ride before it, or None for the first.

    Returns
    -------
    ChangeFate
        `EASY` for the first ride; `MISSED` or `STRANDED` for the ride
        boarded on the chain's missed leg, or on its last leg when the
        missed index is past the chain; a missed leg the fresh plan stays
        on through is no boarding and leaves its ride's fate to the spare
        (see `missed_ride`); `TIGHT` when the spare at the change is under
        `TIGHT_SPARE`; else `EASY`.
    """
    missed = chain.missed
    if before is None or ride.change is None:
        return ChangeFate.EASY
    if missed is not None and ride.item == missed_item(chain=chain):
        return ChangeFate.MISSED if missed.later else ChangeFate.STRANDED
    spare = spare_seconds(
        arrives=before.arrives, departs=ride.departs, change=ride.change,
    )
    return ChangeFate.TIGHT if spare < TIGHT_SPARE else ChangeFate.EASY


def ride_fates(
    *, chain: Chain, rides: Sequence[Ride],
) -> tuple[ChangeFate, ...]:
    """
    Judge every ride's change.

    Parameters
    ----------
    chain : Chain
        The chain.
    rides : Sequence[Ride]
        The chain's rides.

    Returns
    -------
    tuple[ChangeFate, ...]
        One fate per ride; see `ride_fate`.
    """
    return tuple(
        ride_fate(chain=chain, ride=ride, before=before)
        for before, ride in zip((None, *rides), rides, strict=False)
    )


def stranded(*, chain: Chain) -> bool:
    """
    Say whether the chain missed its change with nothing later to catch.

    Parameters
    ----------
    chain : Chain
        The chain.

    Returns
    -------
    bool
        True when the chain carries a miss with no later trip.
    """
    return chain.missed is not None and not chain.missed.later


def missed_ride(*, chain: Chain, rides: Sequence[Ride]) -> int | None:
    """
    Find the ride a missed change is folded into.

    Parameters
    ----------
    chain : Chain
        The chain.
    rides : Sequence[Ride]
        The chain's rides.

    Returns
    -------
    int | None
        The index of the ride holding the miss's leg when that leg is
        stayed on rather than boarded; None without a miss, or when a
        ride boards the leg (see `ride_fate`).
    """
    missed = chain.missed
    if missed is None:
        return None
    last = len(chain.legs) - 1
    target = missed.leg if missed.leg in range(last + 1) else last
    start = 0
    for index, ride in enumerate(rides):
        size = 1
        key = trip_key(item=ride.item)
        while (
            start + size <= last
            and trip_key(item=chain.legs[start + size]) == key
        ):
            size += 1
        if start <= target < start + size:
            return None if index and target == start else index
        start += size
    return None
