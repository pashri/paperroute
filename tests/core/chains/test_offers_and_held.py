"""Tests for chains: offers and held."""

from dataclasses import replace
from typing import Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute_testkit.core.connection_factories import (
    LEG1,
    LEG2,
    WORK,
    WORK_CHANGE,
    at,
    run,
)
from paperroute_testkit.core.factories import live
from paperroute_transit.core.chains import (
    Chain,
    Missed,
    Offer,
    Offers,
    chains_of,
    offers_after,
)
from paperroute_transit.core.model import Departure, Pin, Way
from tests.core.chains.helpers import (
    DAY,
    FIRST,
    KEPT,
    ON_TIME,
    SECOND,
    THIRD,
    TIMES,
    TRAINS,
    TWO_PIN,
    ids,
    late_first,
    lived,
    two_change_case,
    work_chains,
)

SHORT: Final[Pin] = replace(
    WORK, ways=(Way(changes=(replace(WORK_CHANGE, allow=1),)),),
)


def test_offers_after_keeps_each_changing_chain_by_its_first_trip() -> None:
    """Each chain that changes is offered by its first trip; direct is not."""
    offers = offers_after(offers=Offers(), pin=WORK, chains=ON_TIME)
    assert offers.pin == WORK and len(offers.kept) == 5
    assert offers.kept[('a0733', DAY)] == Offer(
        way=0, trips=(('a0733', DAY), ('b0746', DAY)),
    )
    direct = Chain(legs=lived(LEG1[:1]), way=None)
    assert offers_after(offers=Offers(), pin=WORK, chains=(direct,)).kept == {}


def test_offers_after_holds_the_first_offer_and_drops_the_rest() -> None:
    """A held first trip keeps its first offer; unlisted ones are dropped."""
    later = work_chains(first=late_first(6), second=lived(LEG2))
    offers = offers_after(
        offers=Offers(pin=WORK, kept=KEPT), pin=WORK, chains=later,
    )
    assert offers.kept == {('a0733', DAY): KEPT[('a0733', DAY)]}


def test_offers_after_starts_again_for_another_pin() -> None:
    """Offers made for one pin are not kept for another."""
    offers = offers_after(
        offers=Offers(pin=WORK, kept=KEPT), pin=SHORT, chains=(),
    )
    assert offers == Offers(pin=SHORT)
    later = work_chains(first=late_first(6), second=lived(LEG2))
    offers = offers_after(
        offers=Offers(pin=WORK, kept=KEPT), pin=SHORT, chains=later,
    )
    assert offers.kept == {
        ('a0733', DAY): Offer(
            way=0, trips=(('a0733', DAY), ('b0801a', DAY)),
        ),
    }


def test_held_change_with_time_spare_is_kept() -> None:
    """Four late leaves one minute spare: below the allowance, still held."""
    chains = work_chains(first=late_first(4), second=lived(LEG2), kept=KEPT)
    assert ids(chains) == [('a0733', 'b0746')]
    assert chains[0].missed is None
    plain = work_chains(first=late_first(4), second=lived(LEG2))
    assert ids(plain) == [('a0733', 'b0751')]


@pytest.mark.parametrize(('late', 'onward'), [(5, 'b0751'), (6, 'b0801a')])
def test_held_change_with_no_time_spare_is_missed(
    late: int, onward: str,
) -> None:
    """No spare (exactly none at five late): the next bus, the miss noted."""
    chains = work_chains(
        first=late_first(late), second=lived(LEG2), kept=KEPT,
    )
    assert ids(chains) == [('a0733', onward)]
    assert chains[0].missed == Missed(leg=1, arrives=at(7, 57), later=True)


def test_missed_change_with_nothing_later_keeps_the_missed_bus() -> None:
    """With no later bus, the chain keeps the bus it missed."""
    chains = work_chains(
        first=late_first(6), second=lived(LEG2[:1]), kept=KEPT,
    )
    assert ids(chains) == [('a0733', 'b0746')]
    assert chains[0].missed == Missed(leg=1, arrives=at(7, 57), later=False)


def test_held_change_to_a_cancelled_bus_is_missed() -> None:
    """A cancelled second bus misses the change; the next bus follows."""
    second = (live(item=LEG2[0], cancelled=True), *lived(LEG2[1:]))
    chains = work_chains(first=lived(LEG1[:1]), second=second, kept=KEPT)
    assert ids(chains) == [('a0733', 'b0751')]
    assert chains[0].missed == Missed(leg=1, arrives=at(7, 57), later=True)


def test_held_change_to_a_vanished_bus_with_nothing_later_is_dropped() -> None:
    """A vanished second bus with nothing after it leaves no chain."""
    assert not work_chains(first=lived(LEG1[:1]), second=(), kept=KEPT)


def test_held_chain_is_not_dropped_for_a_shared_last_trip() -> None:
    """The late 7:33 and the 7:48 both end on the 8:01; both stay."""
    first = (live(item=LEG1[0], late=6), live(item=LEG1[1]))
    held = work_chains(first=first, second=lived(LEG2), kept=KEPT)
    assert ids(held) == [('a0733', 'b0801a'), ('a0748', 'b0801a')]
    plain = work_chains(first=first, second=lived(LEG2))
    assert ids(plain) == [('a0748', 'b0801a')]


def test_change_inside_a_short_allowance_is_never_missed() -> None:
    """An allowance below the walk keeps the board's own pair."""
    kept = offers_after(
        offers=Offers(),
        pin=SHORT,
        chains=work_chains(first=lived(LEG1), second=lived(LEG2), pin=SHORT),
    ).kept
    chains = work_chains(
        first=late_first(6), second=lived(LEG2), pin=SHORT, kept=kept,
    )
    assert ids(chains) == [('a0733', 'b0746')]
    assert chains[0].missed is None
    chains = work_chains(
        first=late_first(7), second=lived(LEG2), pin=SHORT, kept=kept,
    )
    assert ids(chains) == [('a0733', 'b0751')]
    assert chains[0].missed == Missed(leg=1, arrives=at(7, 57), later=True)


def test_held_chain_missed_at_its_second_change() -> None:
    """The 665 seven late misses the 8:03 train; the 8:15 follows."""
    lists = {
        FIRST: lived(LEG1[:1]), SECOND: lived(LEG2), THIRD: lived(TRAINS),
    }
    on_time = chains_of(pin=TWO_PIN, lists=lists, direct=(), now=at(7, 30))
    kept = offers_after(offers=Offers(), pin=TWO_PIN, chains=on_time).kept
    late = sorted(
        (live(item=LEG2[0], late=7), *lived(LEG2[1:])),
        key=lambda item: item.departs,
    )
    chains = chains_of(
        pin=TWO_PIN, lists={**lists, SECOND: tuple(late)}, direct=(),
        now=at(7, 30), kept=kept,
    )
    assert ids(chains) == [('a0733', 'b0746', 't0815')]
    assert chains[0].missed == Missed(leg=2, arrives=at(8, 8), later=True)
    held = sorted(
        (live(item=LEG2[0], late=4), *lived(LEG2[1:])),
        key=lambda item: item.departs,
    )
    chains = chains_of(
        pin=TWO_PIN, lists={**lists, SECOND: tuple(held)}, direct=(),
        now=at(7, 30), kept=kept,
    )
    assert ids(chains) == [('a0733', 'b0746', 't0803')]
    assert chains[0].missed is None


def test_held_chain_missed_at_its_first_change() -> None:
    """The late 7:33 misses its 665; the miss names the offered train."""
    lists = {
        FIRST: lived(LEG1[:1]), SECOND: lived(LEG2), THIRD: lived(TRAINS),
    }
    on_time = chains_of(pin=TWO_PIN, lists=lists, direct=(), now=at(7, 30))
    kept = offers_after(offers=Offers(), pin=TWO_PIN, chains=on_time).kept
    chains = chains_of(
        pin=TWO_PIN, lists={**lists, FIRST: late_first(6)}, direct=(),
        now=at(7, 30), kept=kept,
    )
    assert ids(chains) == [('a0733', 'b0746', 't0803')]
    assert chains[0].missed == Missed(leg=1, arrives=at(8, 8), later=False)


@settings(max_examples=60, deadline=None)
@given(first=TIMES, second=TIMES, third=TIMES, allow=st.integers(1, 15))
def test_offered_chains_search_again_unchanged(
    *,
    first: list[tuple[int, int]],
    second: list[tuple[int, int]],
    third: list[tuple[int, int]],
    allow: int,
) -> None:
    """Holding every chain offered gives the same chains back, in order."""
    pin, lists = two_change_case(
        first=first, second=second, third=third, allow=allow,
    )
    chains = chains_of(pin=pin, lists=lists, direct=(), now=at(7, 0))
    kept = offers_after(offers=Offers(), pin=pin, chains=chains).kept
    assert chains_of(
        pin=pin, lists=lists, direct=(), now=at(7, 0), kept=kept,
    ) == chains


THROUGH: Final[Departure] = run(
    departs=(7, 57), arrives=(8, 6), route='665', trip_id='b0746',
)


def test_held_stay_on_holds_until_it_cannot_be_stayed_on() -> None:
    """The 665 runs on to Parramatta; once it cannot be stayed on, missed."""
    lists = {
        FIRST: lived(LEG1[:1]),
        SECOND: lived(LEG2[:1]),
        THIRD: (live(item=THROUGH), *lived(TRAINS)),
    }
    on_time = chains_of(pin=TWO_PIN, lists=lists, direct=(), now=at(7, 30))
    assert ids(on_time) == [('a0733', 'b0746', 'b0746')]
    kept = offers_after(offers=Offers(), pin=TWO_PIN, chains=on_time).kept
    assert chains_of(
        pin=TWO_PIN, lists=lists, direct=(), now=at(7, 30), kept=kept,
    ) == on_time
    late = {**lists, SECOND: (live(item=LEG2[0], late=2),)}
    chains = chains_of(
        pin=TWO_PIN, lists=late, direct=(), now=at(7, 30), kept=kept,
    )
    assert ids(chains) == [('a0733', 'b0746', 't0803')]
    assert chains[0].missed == Missed(leg=2, arrives=at(8, 6), later=True)
