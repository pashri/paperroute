"""Tests for chains across a pin's ways."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute.core.chains import (
    Chain,
    LegKey,
    chains_of,
    leg_key,
    leg_list,
    next_leg,
)
from paperroute.core.connection_model import trip_key, way_legs
from paperroute.core.live import LiveDeparture
from paperroute.core.model import Change, Departure, Pin, Way
from paperroute.core.stop_view import clock_text
from tests.core.connection_factories import (
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
from tests.core.factories import departure, live

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


def test_chains_match_real_times() -> None:
    """The real Tuesday times chain as the timetable study found."""
    chains = work_chains(first=lived(LEG1), second=lived(LEG2))
    assert [chain.legs[1].departure.route for chain in chains] == [
        '665', '664', '665', '665', '665',
    ]
    assert [clock_text(moment=chain.arrives) for chain in chains] == [
        '7:57', '8:12', '8:27', '8:45', '9:04',
    ]
    assert all(chain.way == 0 and chain.changes == 1 for chain in chains)


def test_earliest_arrival_not_earliest_departure() -> None:
    """The 7:33 takes the 665 arriving first, not the 662 leaving first."""
    chains = work_chains(first=lived(LEG1[:1]), second=lived(LEG2))
    assert ids(chains) == [('a0733', 'b0746')]


def test_delay_repairs_silently() -> None:
    """A late leg 1 chains to the next catchable leg 2; ties list first."""
    chains = work_chains(
        first=(live(item=LEG1[0], late=6),), second=lived(LEG2),
    )
    assert ids(chains) == [('a0733', 'b0801a')]
    assert clock_text(moment=chains[0].arrives) == '8:12'


def test_change_exactly_allow_is_catchable() -> None:
    """A leg 2 leaving exactly `allow` minutes after arrival is caught."""
    chains = work_chains(
        first=(live(item=LEG1[0], late=5),), second=lived(LEG2),
    )
    assert ids(chains) == [('a0733', 'b0751')]


def test_cancelled_second_leg_skipped() -> None:
    """A cancelled leg 2 is passed over."""
    second = (live(item=LEG2[0], cancelled=True), *lived(LEG2[1:]))
    chains = work_chains(first=lived(LEG1[:1]), second=second)
    assert chains[0].legs[1].departure.route == '662'
    assert clock_text(moment=chains[0].arrives) == '8:02'


def test_no_workable_second_leg_hidden() -> None:
    """A leg 1 with no catchable leg 2 is hidden."""
    chains = work_chains(first=lived(LEG1), second=lived(LEG2[:1]))
    assert ids(chains) == [('a0733', 'b0746')]


@pytest.mark.parametrize(('arrives', 'stays'), [
    ((7, 55), True),
    ((7, 57), True),
    ((8, 10), False),
])
def test_staying_on_wins_when_faster(
    arrives: tuple[int, int], stays: bool,
) -> None:
    """Staying on competes with changing, and wins ties."""
    twin = live(
        item=run(
            departs=(7, 33), arrives=arrives, route='606', trip_id='a0733',
        ),
    )
    chains = work_chains(
        first=lived(LEG1[:1]), second=lived(LEG2), direct=(twin,),
    )
    expected = (
        Chain(legs=(twin,), way=None)
        if stays
        else Chain(legs=lived((LEG1[0], LEG2[0])), way=0)
    )
    assert chains == (expected,)


def test_stay_on_into_later_leg() -> None:
    """A trip that runs on into the next leg's list may be stayed on."""
    through = live(
        item=run(
            departs=(7, 45), arrives=(7, 56), route='606', trip_id='a0733',
        ),
    )
    chains = work_chains(
        first=lived(LEG1[:1]), second=(*lived(LEG2[:1]), through),
    )
    assert ids(chains) == [('a0733', 'a0733')]
    assert chains[0].changes == 0


def test_direct_without_twin_is_own_row() -> None:
    """A direct trip with no leg-1 twin is its own row in order."""
    lone = live(
        item=run(departs=(7, 40), arrives=(8, 0), route='606', trip_id='z'),
    )
    chains = work_chains(
        first=lived(LEG1[:2]), second=lived(LEG2), direct=(lone,),
    )
    assert chains[1] == Chain(legs=(lone,), way=None)
    assert [chain.legs[0].departure.trip_id for chain in chains] == [
        'a0733', 'z', 'a0748',
    ]


def test_departed_leg_one_dropped() -> None:
    """A chain whose first leg has left is dropped."""
    chains = work_chains(
        first=lived(LEG1), second=lived(LEG2), now=at(7, 34),
    )
    assert chains[0].legs[0].departure.trip_id == 'a0748'


def test_cancelled_leg_one_dropped() -> None:
    """A chain whose first leg is cancelled is dropped."""
    first = (live(item=LEG1[0], cancelled=True), *lived(LEG1[1:]))
    chains = work_chains(first=first, second=lived(LEG2))
    assert chains[0].legs[0].departure.trip_id == 'a0748'


def test_chains_across_midnight() -> None:
    """A leg 2 on the next service day still chains."""
    first = departure(departs=at(23, 50), ride=6, trip_id='late')
    second = departure(departs=at(0, 10, day=28), ride=10, trip_id='night')
    chains = work_chains(
        first=(live(item=first),), second=(live(item=second),),
        now=at(23, 40),
    )
    assert chains == (Chain(legs=lived((first, second)), way=0),)
    assert second.service_day.day == 28


def test_dominated_keeps_latest_first() -> None:
    """Of the first legs that reach one final trip, only the last stays."""
    chains = work_chains(first=lived(LEG1), second=lived(LEG2[-2:]))
    assert ids(chains) == [('a0842', 'b0855')]


def test_chain_properties() -> None:
    """A chain leaves with its first leg and arrives with its last."""
    chain = Chain(legs=lived((LEG1[0], LEG2[0])), way=0)
    assert (chain.departs, chain.arrives, chain.changes) == (
        LEG1[0].departs, LEG2[0].arrives, 1,
    )


def test_two_changes_chain() -> None:
    """606, T-Way, then train: the arrival is the third leg's."""
    pin = replace(WORK, destination=PARRAMATTA, ways=(TWO_WAY,))
    chains = chains_of(
        pin=pin,
        lists={
            FIRST: lived(LEG1[:1]),
            SECOND: lived(LEG2),
            THIRD: lived(TRAINS),
        },
        direct=(),
        now=at(7, 30),
    )
    assert ids(chains) == [('a0733', 'b0746', 't0803')]
    assert clock_text(moment=chains[0].arrives) == '8:08'
    assert chains[0].changes == 2


def test_best_way_per_first_trip() -> None:
    """Ways sharing the 7:33: earliest arrival, then fewer changes."""
    fast = live(
        item=run(departs=(7, 46), arrives=(7, 50), route='T8', trip_id='c'),
    )
    pin = replace(WORK, ways=(*WORK.ways, CAMPBELLTOWN_WAY))
    chains = chains_of(
        pin=pin,
        lists={
            FIRST: lived(LEG1[:1]),
            SECOND: lived(LEG2),
            FROM_CAMPBELLTOWN: (fast,),
        },
        direct=(),
        now=at(7, 30),
    )
    assert [(chain.way, ids([chain])[0]) for chain in chains] == [
        (1, ('a0733', 'c')),
    ]
    level = live(
        item=run(departs=(7, 50), arrives=(8, 8), route='T-Way', trip_id='p'),
    )
    pin = replace(
        WORK,
        destination=PARRAMATTA,
        ways=(TWO_WAY, Way(changes=(WORK_CHANGE,))),
    )
    chains = chains_of(
        pin=pin,
        lists={
            FIRST: lived(LEG1[:1]),
            SECOND: lived(LEG2),
            THIRD: lived(TRAINS),
            TO_PARRAMATTA: (level,),
        },
        direct=(),
        now=at(7, 30),
    )
    assert [(chain.way, chain.changes) for chain in chains] == [(1, 1)]


def test_direct_trip_competes() -> None:
    """A direct bus arriving before every way is its own one-leg row."""
    quick = live(
        item=run(departs=(7, 35), arrives=(7, 50), route='609', trip_id='d'),
    )
    chains = work_chains(
        first=lived(LEG1[:1]), second=lived(LEG2), direct=(quick,),
    )
    assert chains == (
        Chain(legs=lived((LEG1[0], LEG2[0])), way=0),
        Chain(legs=(quick,), way=None),
    )


def test_dominated_across_ways() -> None:
    """Two first trips from different ways reach one final trip."""
    elsewhere = Way(
        changes=(Change(alight=PARRAMATTA, board=CAMPBELLTOWN, allow=6),),
    )
    pin = replace(WORK, ways=(*WORK.ways, elsewhere))
    early = run(departs=(7, 40), arrives=(7, 50), route='T1', trip_id='p')
    shared = run(departs=(8, 1), arrives=(8, 12), route='664', trip_id='x')
    via = run(departs=(7, 58), arrives=(8, 12), route='664', trip_id='x')
    chains = chains_of(
        pin=pin,
        lists={
            FIRST: lived(LEG1[1:2]),
            SECOND: lived((shared,)),
            (HOLLIS.stop_id, PARRAMATTA.stop_id): lived((early,)),
            FROM_CAMPBELLTOWN: lived((via,)),
        },
        direct=(),
        now=at(7, 30),
    )
    assert [(chain.way, ids([chain])[0]) for chain in chains] == [
        (0, ('a0748', 'x')),
    ]


def test_dead_way_leaves_others() -> None:
    """A way with no workable last leg hides only its own chains."""
    quick = live(
        item=run(departs=(7, 35), arrives=(9, 50), route='609', trip_id='d'),
    )
    pin = replace(
        WORK,
        destination=PARRAMATTA,
        ways=(TWO_WAY, Way(changes=(WORK_CHANGE,))),
    )
    chains = chains_of(
        pin=pin,
        lists={
            FIRST: lived(LEG1[:2]),
            SECOND: lived(LEG2),
            TO_PARRAMATTA: lived(LEG2),
        },
        direct=(quick,),
        now=at(7, 30),
    )
    assert {chain.way for chain in chains} == {1, None}


def test_never_route_removed() -> None:
    """With the 665 never used, the 7:33 takes the 662."""
    pin = replace(WORK, never=('665',))
    chains = work_chains(first=lived(LEG1[:1]), second=lived(LEG2), pin=pin)
    assert ids(chains) == [('a0733', 'b0751')]
    only_665 = tuple(
        item for item in lived(LEG2) if item.departure.route == '665'
    )
    assert work_chains(first=lived(LEG1), second=only_665, pin=pin) == ()


def test_never_route_removed_from_direct() -> None:
    """A direct trip on a never route is not offered."""
    quick = live(
        item=run(departs=(7, 35), arrives=(7, 50), route='609', trip_id='d'),
    )
    pin = replace(WORK, never=('609',))
    assert work_chains(first=(), second=(), direct=(quick,), pin=pin) == ()


def test_unticked_way_ignored() -> None:
    """An unticked way gives no chains."""
    pin = replace(WORK, ways=(replace(WORK.ways[0], ticked=False),))
    assert work_chains(first=lived(LEG1), second=lived(LEG2), pin=pin) == ()


def test_leg_key_names_stops() -> None:
    """A leg is named by its From and To stop IDs."""
    first, second = way_legs(pin=WORK, way=WORK.ways[0])
    assert (leg_key(leg=first), leg_key(leg=second)) == (FIRST, SECOND)
    assert leg_key(leg=Pin(origin=HOLLIS)) == (HOLLIS.stop_id, '')


def test_next_leg_stops_past_best_arrival() -> None:
    """The scan stops once departures leave after the best arrival."""
    leg = leg_list(items=lived(LEG2), never=())
    found = next_leg(after=live(item=LEG1[0]), leg=leg, allow=7)
    assert found is not None
    assert found.departure.trip_id == 'b0746'


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


@settings(max_examples=60, deadline=None)
@given(first=TIMES, second=TIMES, third=TIMES, allow=st.integers(1, 15))
def test_chains_are_workable(
    first: list[tuple[int, int]],
    second: list[tuple[int, int]],
    third: list[tuple[int, int]],
    allow: int,
) -> None:
    """Every change respects its allow; first legs are unique and sorted."""
    way = Way(
        changes=(
            replace(WORK_CHANGE, allow=allow),
            Change(alight=WESTMEAD, board=WESTMEAD, allow=allow),
        ),
    )
    pin = replace(WORK, destination=PARRAMATTA, ways=(way,))
    chains = chains_of(
        pin=pin,
        lists={
            FIRST: leg_of(times=first, prefix='a'),
            SECOND: leg_of(times=second, prefix='b'),
            THIRD: leg_of(times=third, prefix='c'),
        },
        direct=(),
        now=at(7, 0),
    )
    for chain in chains:
        for before, after in zip(chain.legs, chain.legs[1:]):
            gap = (after.departs - before.arrives).total_seconds() / 60
            assert gap >= allow
    firsts = [trip_key(item=chain.legs[0]) for chain in chains]
    assert len(firsts) == len(set(firsts))
    departs = [chain.departs for chain in chains]
    assert departs == sorted(departs)
