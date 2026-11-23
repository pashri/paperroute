"""Tests for lateness marks and change fates on connection pins."""

from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute_testkit.core.connection_factories import (
    WORK,
    WORK_CHANGE,
    at,
    run,
)
from paperroute_testkit.core.factories import live
from paperroute_transit.core.chains import Chain, Missed
from paperroute_transit.core.connection_late import (
    ChangeFate,
    arrival_mark,
    leg_mark,
    missed_ride,
    ride_fates,
)
from paperroute_transit.core.connection_model import TIGHT_SPARE, spare_seconds
from paperroute_transit.core.rides import rides_of
from tests.core.test_chains import TWO_PIN
from tests.core.test_connection_view import A0733, B0746, B0801A


def test_leg_mark_follows_the_row_rule() -> None:
    """A mark shows from two minutes late, with ? for a guess."""
    late = live(item=A0733, late=4)
    assert leg_mark(item=late) == '+4'
    assert leg_mark(item=live(item=A0733, late=1)) == ''
    assert leg_mark(item=live(item=A0733, late=-2)) == '−2'
    assert leg_mark(item=live(item=A0733)) == ''
    assert leg_mark(item=live(item=A0733, late=4, cancelled=True)) == ''
    guess = replace(late, estimated=True, uncertain=True)
    assert leg_mark(item=guess) == '+4?'
    assert leg_mark(item=replace(late, uncertain=True)) == '+4'


def test_leg_mark_is_empty_for_a_bus_not_live() -> None:
    """A lateness figure on a bus not live shows nothing."""
    assert leg_mark(item=replace(live(item=A0733, late=4), live=False)) == ''


def test_arrival_mark_reads_the_live_arrival() -> None:
    """The mark compares the live arrival with the timetabled one."""
    assert arrival_mark(item=live(item=A0733, late=3)) == '+3'
    early = replace(
        live(item=A0733, late=3), arrives=A0733.arrives + timedelta(minutes=1),
    )
    assert arrival_mark(item=early) == ''


@pytest.mark.parametrize(('late', 'fate'), [
    (0, ChangeFate.EASY),
    (3, ChangeFate.EASY),
    (4, ChangeFate.TIGHT),
    (5, ChangeFate.TIGHT),
    (6, ChangeFate.TIGHT),
])
def test_ride_fates_judge_a_change_on_its_spare(
    late: int, fate: ChangeFate,
) -> None:
    """Two minutes spare is easy; less is tight; only memory misses."""
    chain = Chain(legs=(live(item=A0733, late=late), live(item=B0746)), way=0)
    rides = rides_of(chain=chain, pin=WORK)
    assert ride_fates(chain=chain, rides=rides) == (ChangeFate.EASY, fate)


def test_ride_fates_name_a_missed_change() -> None:
    """A ride on the missed leg is missed, or stranded with nothing later."""
    gone = Missed(leg=1, arrives=at(7, 57), later=True)
    chain = Chain(
        legs=(live(item=A0733, late=6), live(item=B0801A)), way=0,
        missed=gone,
    )
    rides = rides_of(chain=chain, pin=WORK)
    assert ride_fates(chain=chain, rides=rides)[1] is ChangeFate.MISSED
    alone = Chain(
        legs=(live(item=A0733, late=6), live(item=B0746)), way=0,
        missed=replace(gone, later=False),
    )
    rides = rides_of(chain=alone, pin=WORK)
    assert ride_fates(chain=alone, rides=rides)[1] is ChangeFate.STRANDED


@pytest.mark.parametrize(('spare', 'fate'), [
    (TIGHT_SPARE, ChangeFate.EASY),
    (TIGHT_SPARE - 60, ChangeFate.TIGHT),
])
def test_ride_fates_draw_the_tight_line_in_whole_minutes(
    spare: int, fate: ChangeFate,
) -> None:
    """Exactly `TIGHT_SPARE` spare is easy; a minute less is tight."""
    base = spare_seconds(
        arrives=A0733.arrives, departs=B0746.departs, change=WORK_CHANGE,
    )
    first = live(item=A0733, late=int(base - spare) // 60)
    assert spare_seconds(
        arrives=first.arrives, departs=B0746.departs, change=WORK_CHANGE,
    ) == spare
    chain = Chain(legs=(first, live(item=B0746)), way=0)
    rides = rides_of(chain=chain, pin=WORK)
    assert ride_fates(chain=chain, rides=rides) == (ChangeFate.EASY, fate)


def test_ride_fates_take_a_missed_leg_past_the_chain_as_its_last() -> None:
    """A missed leg beyond the chain falls on its last ride."""
    chain = Chain(
        legs=(live(item=A0733, late=6), live(item=B0801A)), way=0,
        missed=Missed(leg=5, arrives=at(7, 57), later=True),
    )
    rides = rides_of(chain=chain, pin=WORK)
    assert ride_fates(chain=chain, rides=rides) == (
        ChangeFate.EASY, ChangeFate.MISSED,
    )


def test_ride_fates_leave_a_missed_leg_folded_into_a_ride_unmissed() -> None:
    """A stay-on leg is no boarding: no ride's fate is missed."""
    chain = Chain(
        legs=(
            live(item=A0733, late=6),
            live(item=B0746, late=7),
            live(
                item=run(
                    departs=(7, 57), arrives=(8, 10), route='665',
                    trip_id='b0746',
                ),
                late=7,
            ),
        ),
        way=0, missed=Missed(leg=2, arrives=at(8, 8), later=True),
    )
    rides = rides_of(chain=chain, pin=TWO_PIN)
    assert len(rides) == 2
    assert ChangeFate.MISSED not in ride_fates(chain=chain, rides=rides)
    assert missed_ride(chain=chain, rides=rides) == 1
    boarded = Chain(
        legs=chain.legs[:2], way=0,
        missed=Missed(leg=1, arrives=at(8, 8), later=True),
    )
    rides = rides_of(chain=boarded, pin=WORK)
    assert missed_ride(chain=boarded, rides=rides) is None
