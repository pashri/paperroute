"""Lateness marks and change fates on connection pins."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Final

from paperroute_transit.core.chains import Chain
from paperroute_transit.core.connection_model import TIGHT_SPARE, spare_seconds
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
        `EASY` for the first ride or a stay on; `MISSED` or `STRANDED`
        for the ride boarded on the chain's missed leg, its last leg when
        the missed leg is not one of its legs; `TIGHT` when the spare at
        the change is under `TIGHT_SPARE`; else `EASY`.
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
