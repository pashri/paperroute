"""Shared helpers for the chains tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final

from hypothesis import strategies as st

from paperroute_device.core.text import clock_text
from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.connection_factories import (
    CAMPBELLTOWN,
    CAMPBELLTOWN_WAY,
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    LEG1,
    LEG2,
    PARRAMATTA,
    TWO_WAY,
    WESTMEAD,
    WORK,
    WORK_CHANGE,
    at,
    run,
)
from paperroute_testkit.core.factories import departure, live
from paperroute_transit.core.chains import (
    Chain,
    LegKey,
    Offer,
    Offers,
    chains_of,
    offers_after,
)
from paperroute_transit.core.connection_model import TripKey
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import (
    Change,
    Departure,
    Pin,
    StopPlace,
    Way,
)

FIRST: Final[LegKey] = (HOLLIS.stop_id, HAMMERS.stop_id)


SECOND: Final[LegKey] = (CONSTITUTION.stop_id, WESTMEAD.stop_id)


TO_PARRAMATTA: Final[LegKey] = (CONSTITUTION.stop_id, PARRAMATTA.stop_id)


THIRD: Final[LegKey] = (WESTMEAD.stop_id, PARRAMATTA.stop_id)


FROM_CAMPBELLTOWN: Final[LegKey] = (CAMPBELLTOWN.stop_id, WESTMEAD.stop_id)


TRAINS: Final[tuple[Departure, ...]] = tuple(
    run(departs=leave, arrives=arrive, route='T1', trip_id=trip_id)
    for leave, arrive, trip_id in (
        ((8, 0), (8, 5), 't0800'),
        ((8, 3), (8, 8), 't0803'),
        ((8, 15), (8, 20), 't0815'),
    )
)


def lived(items: Sequence[Departure]) -> tuple[LiveDeparture, ...]:
    """
    Wrap departures as on time.

    Parameters
    ----------
    items : Sequence[Departure]
        Departures.

    Returns
    -------
    tuple[LiveDeparture, ...]
        Live departures with no delay.
    """
    return tuple(live(item=item) for item in items)


def work_chains(
    *,
    first: Sequence[LiveDeparture],
    second: Sequence[LiveDeparture],
    direct: Sequence[LiveDeparture] = (),
    now: datetime | None = None,
    pin: Pin = WORK,
    kept: Mapping[TripKey, Offer] = MappingProxyType({}),
) -> tuple[Chain, ...]:
    """
    Chain WORK's two legs.

    Parameters
    ----------
    first : Sequence[LiveDeparture]
        Leg-1 departures.
    second : Sequence[LiveDeparture]
        Leg-2 departures.
    direct : Sequence[LiveDeparture]
        Direct departures.
    now : datetime | None
        Current time; 7:30 when None.
    pin : Pin
        The pin.
    kept : Mapping[TripKey, Offer]
        Offers held, by first trip.

    Returns
    -------
    tuple[Chain, ...]
        The chains.
    """
    return chains_of(
        pin=pin,
        lists={FIRST: first, SECOND: second},
        direct=direct,
        now=now or at(7, 30),
        kept=kept,
    )


def ids(chains: Sequence[Chain]) -> list[tuple[str, ...]]:
    """
    Give each chain's trip IDs.

    Parameters
    ----------
    chains : Sequence[Chain]
        Chains.

    Returns
    -------
    list[tuple[str, ...]]
        Trip IDs per chain.
    """
    return [
        tuple(item.departure.trip_id for item in chain.legs)
        for chain in chains
    ]


TIMES = st.lists(
    st.tuples(st.integers(0, 120), st.integers(1, 40)),
    min_size=1,
    max_size=8,
)


def leg_of(
    *, times: list[tuple[int, int]], prefix: str,
) -> tuple[LiveDeparture, ...]:
    """
    Build one leg's departures from minute offsets.

    Parameters
    ----------
    times : list[tuple[int, int]]
        Minutes after 7:00 each leaves, and minutes it rides.
    prefix : str
        Trip ID prefix.

    Returns
    -------
    tuple[LiveDeparture, ...]
        Departures sorted by when they leave.
    """
    items = [
        departure(
            departs=at(7, 0) + timedelta(minutes=offset),
            ride=ride,
            trip_id=f'{prefix}{index}',
        )
        for index, (offset, ride) in enumerate(times)
    ]
    return tuple(sorted(lived(items), key=lambda item: item.departs))


def two_change_case(
    *,
    first: list[tuple[int, int]],
    second: list[tuple[int, int]],
    third: list[tuple[int, int]],
    allow: int,
) -> tuple[Pin, dict[LegKey, tuple[LiveDeparture, ...]]]:
    """
    Build a two-change pin and its three legs from minute offsets.

    Parameters
    ----------
    first : list[tuple[int, int]]
        First leg's offsets and rides.
    second : list[tuple[int, int]]
        Second leg's offsets and rides.
    third : list[tuple[int, int]]
        Third leg's offsets and rides.
    allow : int
        Minutes allowed at each change.

    Returns
    -------
    tuple[Pin, dict[LegKey, tuple[LiveDeparture, ...]]]
        The pin, and its departures by leg.
    """
    way = Way(
        changes=(
            replace(WORK_CHANGE, allow=allow),
            Change(alight=WESTMEAD, board=WESTMEAD, allow=allow),
        ),
    )
    pin = replace(WORK, destination=PARRAMATTA, ways=(way,))
    return pin, {
        FIRST: leg_of(times=first, prefix='a'),
        SECOND: leg_of(times=second, prefix='b'),
        THIRD: leg_of(times=third, prefix='c'),
    }


DAY: Final = LEG1[0].service_day


ON_TIME: Final[tuple[Chain, ...]] = work_chains(
    first=lived(LEG1), second=lived(LEG2),
)


KEPT: Final[Mapping[TripKey, Offer]] = offers_after(
    offers=Offers(), pin=WORK, chains=ON_TIME,
).kept


TWO_PIN: Final[Pin] = replace(WORK, destination=PARRAMATTA, ways=(TWO_WAY,))


def late_first(minutes: int) -> tuple[LiveDeparture, ...]:
    """
    The 7:33 running late, alone.

    Parameters
    ----------
    minutes : int
        Minutes late.

    Returns
    -------
    tuple[LiveDeparture, ...]
        The late 7:33.
    """
    return (live(item=LEG1[0], late=minutes),)


CAMP_PIN: Final[Pin] = replace(WORK, ways=(*WORK.ways, CAMPBELLTOWN_WAY))


NO_665: Final[tuple[LiveDeparture, ...]] = tuple(
    live(item=item, cancelled=item.trip_id in {'b0746', 'b0751'})
    for item in LEG2
)


C0750: Final[Departure] = run(
    departs=(7, 50), arrives=(8, 5), route='880', trip_id='c0750',
)


def camp_offers(camp: Sequence[Departure]) -> Mapping[TripKey, Offer]:
    """
    Offer CAMP_PIN's chains with every bus on time.

    Parameters
    ----------
    camp : Sequence[Departure]
        Campbelltown departures.

    Returns
    -------
    Mapping[TripKey, Offer]
        The offers, the 7:33 by way 1 and the 7:48 by way 0.
    """
    chains = chains_of(
        pin=CAMP_PIN,
        lists={
            FIRST: lived(LEG1[:2]),
            SECOND: NO_665,
            FROM_CAMPBELLTOWN: lived(camp),
        },
        direct=(),
        now=at(7, 30),
    )
    assert [(chain.way, ids([chain])[0][0]) for chain in chains] == [
        (1, 'a0733'), (0, 'a0748'),
    ]
    return offers_after(offers=Offers(), pin=CAMP_PIN, chains=chains).kept


LEVEL: Final[Departure] = run(
    departs=(7, 50), arrives=(8, 15), route='T-Way', trip_id='p0750',
)


def leg_lists(
    *, extra: Mapping[LegKey, tuple[LiveDeparture, ...]]
) -> dict[LegKey, tuple[LiveDeparture, ...]]:
    """
    List the 606 and T-Way legs, with more lists beside them.

    Parameters
    ----------
    extra : Mapping[LegKey, tuple[LiveDeparture, ...]]
        The other lists, by leg.

    Returns
    -------
    dict[LegKey, tuple[LiveDeparture, ...]]
        The lists by leg.
    """
    return {FIRST: lived(LEG1[:1]), SECOND: lived(LEG2), **extra}


THREE_OR_TWO: Final[Pin] = replace(
    WORK,
    destination=PARRAMATTA,
    ways=(TWO_WAY, Way(changes=(WORK_CHANGE,))),
)


def three_then_two() -> Chain:
    """
    Offer the 7:33 by three legs, then miss its second change.

    Returns
    -------
    Chain
        The chain shown once the 665 runs seven late: the two-leg way,
        which arrives before the three-leg way's later train.
    """
    lists = {
        FIRST: lived(LEG1[:1]),
        SECOND: lived(LEG2[:1]),
        THIRD: lived(TRAINS),
        TO_PARRAMATTA: lived((LEVEL,)),
    }
    on_time = chains_of(
        pin=THREE_OR_TWO, lists=lists, direct=(), now=at(7, 30),
    )
    assert ids(on_time) == [('a0733', 'b0746', 't0803')]
    kept = offers_after(
        offers=Offers(), pin=THREE_OR_TWO, chains=on_time,
    ).kept
    (chain,) = chains_of(
        pin=THREE_OR_TWO,
        lists={**lists, SECOND: (live(item=LEG2[0], late=7),)},
        direct=(),
        now=at(7, 30),
        kept=kept,
    )
    return chain


EXPRESS: Final[Departure] = run(
    departs=(7, 48), arrives=(7, 59), route='665', trip_id='b0748x',
)


def express_plan() -> Chain:
    """
    Offer the 7:33 then the 7:46 665, then run the 665 six late.

    Returns
    -------
    Chain
        The chain shown once an express 665 at 7:48 still makes the 8:03
        train, which the 7:46 no longer does.
    """
    second = tuple(sorted(lived((*LEG2, EXPRESS)), key=lambda x: x.departs))
    lists = {FIRST: lived(LEG1[:1]), SECOND: second, THIRD: lived(TRAINS)}
    on_time = chains_of(pin=TWO_PIN, lists=lists, direct=(), now=at(7, 30))
    kept = offers_after(offers=Offers(), pin=TWO_PIN, chains=on_time).kept
    late = tuple(sorted(
        (live(item=LEG2[0], late=6), *lived((*LEG2[1:], EXPRESS))),
        key=lambda x: x.departs,
    ))
    (chain, *_) = chains_of(
        pin=TWO_PIN,
        lists={**lists, SECOND: late},
        direct=(),
        now=at(7, 30),
        kept=kept,
    )
    return chain


def two_change_pin(*, board: StopPlace) -> Pin:
    """
    Make the Hollis to Parramatta pin with a Work change and a Westmead one.

    Parameters
    ----------
    board : StopPlace
        Where the second change boards.

    Returns
    -------
    Pin
        The pin.
    """
    return Pin(
        origin=HOLLIS,
        destination=PARRAMATTA,
        kind=None,
        ways=(Way(changes=(
            WORK_CHANGE, Change(alight=WESTMEAD, board=board, allow=4),
        )),),
    )


def arrival_clocks(*, chains: Sequence[Chain]) -> list[str]:
    """
    Give each chain's arrival time as clock text.

    Parameters
    ----------
    chains : Sequence[Chain]
        The chains.

    Returns
    -------
    list[str]
        The arrival times.
    """
    return [
        clock_text(moment=chain.arrives, zone=SYDNEY_ZONE) for chain in chains
    ]
