"""Tests for lateness marks and change fates on connection pins."""

from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute_testkit.core.connection_factories import WORK, at
from paperroute_testkit.core.factories import live
from paperroute_transit.core.chains import Chain, Missed
from paperroute_transit.core.connection_late import (
    ChangeFate,
    arrival_mark,
    leg_mark,
    ride_fates,
)
from paperroute_transit.core.rides import rides_of
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
