"""Tests for chains: ways."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Final

from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute_testkit.core.connection_factories import (
    CAMPBELLTOWN,
    CAMPBELLTOWN_WAY,
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
    LegList,
    chains_of,
    leg_key,
    leg_list,
    next_leg,
    walked,
    way_chains,
)
from paperroute_transit.core.connection_model import trip_key, way_legs
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import Change, Pin, Way
from tests.core.chains.helpers import (
    FIRST,
    FROM_CAMPBELLTOWN,
    SECOND,
    THIRD,
    TIMES,
    TO_PARRAMATTA,
    TRAINS,
    ids,
    leg_lists,
    lived,
    two_change_case,
    work_chains,
)


def test_best_way_per_first_trip() -> None:
    """Ways sharing the 7:33: earliest arrival, then fewer changes."""
    fast = live(
        item=run(departs=(7, 46), arrives=(7, 50), route='T8', trip_id='c'),
    )
    pin = replace(WORK, ways=(*WORK.ways, CAMPBELLTOWN_WAY))
    chains = chains_of(
        pin=pin,
        lists=leg_lists(extra={FROM_CAMPBELLTOWN: (fast,)}),
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
        lists=leg_lists(
            extra={THIRD: lived(TRAINS), TO_PARRAMATTA: (level,)},
        ),
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
    assert not work_chains(first=lived(LEG1), second=only_665, pin=pin)


def test_never_route_removed_from_direct() -> None:
    """A direct trip on a never route is not offered."""
    quick = live(
        item=run(departs=(7, 35), arrives=(7, 50), route='609', trip_id='d'),
    )
    pin = replace(WORK, never=('609',))
    assert not work_chains(first=(), second=(), direct=(quick,), pin=pin)


def test_unticked_way_ignored() -> None:
    """An unticked way gives no chains."""
    pin = replace(WORK, ways=(replace(WORK.ways[0], ticked=False),))
    assert not work_chains(first=lived(LEG1), second=lived(LEG2), pin=pin)


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


SKIP_WAY: Final[Way] = Way(
    changes=(
        replace(WORK_CHANGE, allow=2),
        Change(alight=WESTMEAD, board=WESTMEAD, allow=2),
    ),
)


def trip(trip_id: str, leaves: int, rides: int) -> LiveDeparture:
    """
    Make a trip leaving some minutes after 7:00.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    leaves : int
        Minutes after 7:00 it leaves.
    rides : int
        Minutes it rides.

    Returns
    -------
    LiveDeparture
        The trip, on time.
    """
    return live(
        item=departure(
            departs=at(7, 0) + timedelta(minutes=leaves),
            ride=rides,
            trip_id=trip_id,
        ),
    )


def walk(*lists: Sequence[LiveDeparture]) -> tuple[LiveDeparture, ...] | None:
    """
    Walk SKIP_WAY from the first trip on the first leg.

    Parameters
    ----------
    *lists : Sequence[LiveDeparture]
        Each leg's departures.

    Returns
    -------
    tuple[LiveDeparture, ...] | None
        The finished trips.
    """
    legs = [leg_list(items=items, never=()) for items in lists]
    return walked(
        taken=(lists[0][0],), index=0, way=SKIP_WAY, legs=legs,
    )


def test_stay_on_never_skips_a_leg() -> None:
    """A trip on legs 0 and 2 only still gives one trip per leg."""
    taken = walk(
        (trip('x', 0, 10),),
        (trip('b', 20, 10),),
        (trip('x', 40, 10), trip('c', 40, 20)),
    )
    assert taken is not None
    assert [item.departure.trip_id for item in taken] == ['x', 'b', 'x']


def test_stay_on_not_before_alighting() -> None:
    """A looping trip's earlier pass of the next leg is not stayed on."""
    taken = walk(
        (trip('x', 0, 10),),
        (trip('x', -5, 10), trip('b', 20, 10)),
        (trip('c', 40, 20),),
    )
    assert taken is not None
    assert [item.departure.trip_id for item in taken] == ['x', 'b', 'c']


def test_way_chains_reuse_leg_lists() -> None:
    """Leg lists are built once per leg and read from the cache after."""
    lists = {FIRST: lived(LEG1[:1]), SECOND: lived(LEG2)}
    cache: dict[LegKey, LegList] = {}
    found = way_chains(
        pin=WORK, position=0, lists=lists, now=at(7, 30), cache=cache,
    )
    assert ids(found) == [('a0733', 'b0746')]
    assert set(cache) == {FIRST, SECOND}
    cache[FIRST] = leg_list(items=(), never=())
    assert way_chains(
        pin=WORK, position=0, lists=lists, now=at(7, 30), cache=cache,
    ) == []


@settings(max_examples=60, deadline=None)
@given(first=TIMES, second=TIMES, third=TIMES, allow=st.integers(1, 15))
def test_chains_are_workable(
    *,
    first: list[tuple[int, int]],
    second: list[tuple[int, int]],
    third: list[tuple[int, int]],
    allow: int,
) -> None:
    """Every change respects its allow; first legs are unique and sorted."""
    pin, lists = two_change_case(
        first=first, second=second, third=third, allow=allow,
    )
    chains = chains_of(pin=pin, lists=lists, direct=(), now=at(7, 0))
    for chain in chains:
        for before, after in zip(chain.legs, chain.legs[1:]):
            gap = (after.departs - before.arrives).total_seconds() / 60
            assert gap >= allow
    firsts = [trip_key(item=chain.legs[0]) for chain in chains]
    assert len(firsts) == len(set(firsts))
    departs = [chain.departs for chain in chains]
    assert departs == sorted(departs)
