"""Tests for chains: missed."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType

import pytest

from paperroute_testkit.core.connection_factories import (
    LEG1,
    LEG2,
    WORK,
    at,
    run,
)
from paperroute_testkit.core.factories import live
from paperroute_transit.core.chains import (
    Missed,
    Offer,
    Offers,
    chains_of,
    offers_after,
)
from paperroute_transit.core.connection_model import TripKey
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import Departure
from tests.core.chains.helpers import (
    C0750,
    CAMP_PIN,
    DAY,
    FIRST,
    FROM_CAMPBELLTOWN,
    KEPT,
    NO_665,
    SECOND,
    camp_offers,
    express_plan,
    ids,
    late_first,
    lived,
    three_then_two,
    work_chains,
)


def test_missed_change_may_fold_into_a_stay_on() -> None:
    """The late 7:33 misses its 665 and is best stayed on; the miss stays."""
    through = run(
        departs=(7, 45), arrives=(7, 58), route='606', trip_id='a0733',
    )
    second = (live(item=through), *lived(LEG2[:1]))
    on_time = work_chains(first=lived(LEG1[:1]), second=second)
    assert ids(on_time) == [('a0733', 'b0746')]
    kept = offers_after(offers=Offers(), pin=WORK, chains=on_time).kept
    late = (live(item=through, late=6), *lived(LEG2[:1]))
    chains = work_chains(first=late_first(6), second=late, kept=kept)
    assert ids(chains) == [('a0733', 'a0733')]
    assert chains[0].missed == Missed(leg=1, arrives=at(7, 57), later=True)


def test_missed_change_folded_into_a_stay_on_is_remembered() -> None:
    """The folded chain keeps its offer, so the miss shows again."""
    through = run(
        departs=(7, 45), arrives=(7, 58), route='606', trip_id='a0733',
    )
    second = (live(item=through), *lived(LEG2[:1]))
    on_time = work_chains(first=lived(LEG1[:1]), second=second)
    kept = offers_after(offers=Offers(), pin=WORK, chains=on_time).kept
    late = (live(item=through, late=6), *lived(LEG2[:1]))
    chains = work_chains(first=late_first(6), second=late, kept=kept)
    offers = offers_after(
        offers=Offers(pin=WORK, kept=kept), pin=WORK, chains=chains,
    )
    assert offers.kept == kept
    again = work_chains(first=late_first(6), second=late, kept=offers.kept)
    assert again == chains
    assert again[0].missed == Missed(leg=1, arrives=at(7, 57), later=True)


def camp_chains(
    *,
    first: Sequence[LiveDeparture],
    camp: Sequence[Departure],
    kept: Mapping[TripKey, Offer] = MappingProxyType({}),
) -> list[tuple[int | None, tuple[str, ...], Missed | None]]:
    """
    Chain CAMP_PIN's two ways with the 7:46 and 7:51 cancelled.

    Parameters
    ----------
    first : Sequence[LiveDeparture]
        Leg-1 departures.
    camp : Sequence[Departure]
        Campbelltown departures, on time.
    kept : Mapping[TripKey, Offer]
        Offers held, by first trip.

    Returns
    -------
    list[tuple[int | None, tuple[str, ...], Missed | None]]
        Each chain's way, trip IDs and miss.
    """
    chains = chains_of(
        pin=CAMP_PIN,
        lists={FIRST: first, SECOND: NO_665, FROM_CAMPBELLTOWN: lived(camp)},
        direct=(),
        now=at(7, 30),
        kept=kept,
    )
    return [
        (chain.way, ids([chain])[0], chain.missed) for chain in chains
    ]


def test_missed_change_stranded_on_its_way_goes_on_by_another() -> None:
    """Nothing later from Campbelltown: the late 7:33 goes on by way 0."""
    kept = camp_offers((C0750,))
    first = (*late_first(6), *lived(LEG1[1:2]))
    assert camp_chains(first=first, camp=(C0750,), kept=kept) == [
        (0, ('a0733', 'b0801a'), Missed(leg=1, arrives=at(8, 5), later=True)),
        (0, ('a0748', 'b0801a'), None),
    ]


@pytest.mark.parametrize('arrives', [(8, 10), (8, 12)])
def test_missed_change_keeps_its_way_when_it_arrives_no_later(
    arrives: tuple[int, int],
) -> None:
    """A later Campbelltown train no later than way 0 keeps way 1."""
    camp = (
        C0750,
        run(departs=(7, 55), arrives=arrives, route='880', trip_id='c0755'),
    )
    kept = camp_offers(camp)
    first = (*late_first(6), *lived(LEG1[1:2]))
    assert camp_chains(first=first, camp=camp, kept=kept) == [
        (1, ('a0733', 'c0755'), Missed(leg=1, arrives=at(8, 5), later=True)),
        (0, ('a0748', 'b0801a'), None),
    ]


def test_offers_off_the_pin_ways_are_not_held() -> None:
    """An offer on a missing or unticked way, or the wrong length, is free."""
    offer = KEPT[('a0733', DAY)]
    plain = work_chains(first=late_first(6), second=lived(LEG2))
    for wrong in (
        replace(offer, way=1), replace(offer, trips=offer.trips[:1]),
        replace(offer, way=-1),
    ):
        kept = {('a0733', DAY): wrong}
        assert work_chains(
            first=late_first(6), second=lived(LEG2), kept=kept,
        ) == plain
    off = replace(WORK.ways[0], ticked=False)
    pin = replace(WORK, ways=(off, WORK.ways[0]))
    free = work_chains(first=late_first(6), second=lived(LEG2), pin=pin)
    assert work_chains(
        first=late_first(6), second=lived(LEG2), pin=pin, kept=KEPT,
    ) == free


def test_held_first_trip_that_left_or_is_cancelled_is_dropped() -> None:
    """A held first trip that has left or is cancelled is not followed."""
    cancelled = (live(item=LEG1[0], cancelled=True), *lived(LEG1[1:]))
    assert work_chains(
        first=cancelled, second=lived(LEG2), kept=KEPT,
    ) == work_chains(first=cancelled, second=lived(LEG2))
    assert work_chains(
        first=lived(LEG1), second=lived(LEG2), now=at(7, 34), kept=KEPT,
    ) == work_chains(first=lived(LEG1), second=lived(LEG2), now=at(7, 34))
    chains = work_chains(
        first=late_first(6), second=lived(LEG2), now=at(7, 39), kept=KEPT,
    )
    assert chains[0].missed == Missed(leg=1, arrives=at(7, 57), later=True)


def test_missed_change_points_at_a_shorter_fresh_plan() -> None:
    """A three-leg offer shown by a two-leg plan notes its first new bus."""
    chain = three_then_two()
    assert (chain.way, ids([chain])[0]) == (1, ('a0733', 'p0750'))
    assert chain.missed == Missed(leg=1, arrives=at(8, 8), later=True)


def test_missed_change_with_a_fresh_plan_always_has_a_way_on() -> None:
    """A fresh plan reaches the destination, so it is never stranded."""
    chain = express_plan()
    assert ids([chain])[0] == ('a0733', 'b0748x', 't0803')
    assert chain.missed is not None and chain.missed.later
