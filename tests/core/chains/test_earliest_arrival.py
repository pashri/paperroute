"""Tests for chains: earliest arrival."""

from dataclasses import replace

import pytest

from paperroute_device.core.text import clock_text
from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.connection_factories import (
    LEG1,
    LEG2,
    PARRAMATTA,
    TWO_WAY,
    WORK,
    at,
    run,
)
from paperroute_testkit.core.factories import departure, live
from paperroute_transit.core.chains import Chain, chains_of
from tests.core.chains.helpers import (
    THIRD,
    TRAINS,
    ids,
    leg_lists,
    lived,
    work_chains,
)


def test_chains_match_real_times() -> None:
    """The real Tuesday times chain as the timetable study found."""
    chains = work_chains(first=lived(LEG1), second=lived(LEG2))
    assert [chain.legs[1].departure.route for chain in chains] == [
        '665', '664', '665', '665', '665',
    ]
    assert [clock_text(
        moment=chain.arrives,
        zone=SYDNEY_ZONE,
    ) for chain in chains] == [
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
    assert clock_text(moment=chains[0].arrives, zone=SYDNEY_ZONE) == '8:12'


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
    assert clock_text(moment=chains[0].arrives, zone=SYDNEY_ZONE) == '8:02'


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
        lists=leg_lists(extra={THIRD: lived(TRAINS)}),
        direct=(),
        now=at(7, 30),
    )
    assert ids(chains) == [('a0733', 'b0746', 't0803')]
    assert clock_text(moment=chains[0].arrives, zone=SYDNEY_ZONE) == '8:08'
    assert chains[0].changes == 2
