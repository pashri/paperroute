"""Tests for the next bus from each kerb of a both-sides pin."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyMode
from paperroute_testkit.core.factories import START, departure, place
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import Pin, TripFacts
from paperroute_transit.core.stop_sides import (
    NOTHING_THIS_SIDE,
    OTHER_SIDE,
    THIS_SIDE,
    TOWARDS,
    each_way,
    hero_station,
)
from paperroute_transit.core.stop_under import Kerb
from paperroute_transit.core.stop_view import StopInputs, build_stop_view

BOTH: Final[Pin] = Pin(
    origin=place('s2'), kind=SydneyMode.BUS, opposite=place('x2'),
    towards=('Leichhardt', 'Newtown'),
)


def item(
    *, minutes: int, across: bool, cancelled: bool = False,
) -> LiveDeparture:
    """A live departure some minutes after START from one kerb."""
    dep = replace(
        departure(departs=START + timedelta(minutes=minutes)),
        facts=TripFacts(across=across),
    )
    return LiveDeparture(
        departure=dep, departs=dep.departs, arrives=dep.arrives,
        live=True, cancelled=cancelled,
    )


def inputs_of(*, items: tuple[LiveDeparture, ...]) -> StopInputs:
    """Inputs for a both-sides pin's Stop view at START."""
    return StopInputs(
        origin='Orchard Close, both sides',
        destination=None,
        departures=items,
        now=START,
        zone=SYDNEY_ZONE,
        scroll=0,
        live_since=START,
        has_feed=True,
        stale_after=timedelta(minutes=2),
        city=SYDNEY,
    )


def test_each_way_puts_the_picked_kerb_on_top() -> None:
    """The picked kerb's next bus leads, the kerb across's sits below."""
    view = each_way(inputs=inputs_of(items=(
        item(minutes=2, across=True), item(minutes=5, across=False),
        item(minutes=7, across=True), item(minutes=9, across=False),
    )), towards=BOTH.towards)
    assert view.each_way is not None
    here, across = view.each_way.here, view.each_way.across
    assert here.hero is not None and across.hero is not None
    assert (here.hero.countdown, across.hero.countdown) == ('5', '2')
    assert (here.label, here.end) == (TOWARDS, 'Leichhardt')
    assert (across.label, across.end) == (TOWARDS, 'Newtown')
    assert here.under is not None and across.under is not None
    assert (here.under.then, across.under.then) == (('8:09',), ('8:07',))
    assert [row.countdown for row in view.rows] == ['7', '9']


def test_each_way_with_one_kerb_empty_keeps_one_hero() -> None:
    """A kerb with nothing in view keeps its block with no bus."""
    view = each_way(
        inputs=inputs_of(items=(item(minutes=4, across=False),)),
        towards=BOTH.towards,
    )
    assert view.each_way is not None
    assert view.each_way.across == Kerb(
        label=TOWARDS, end='Newtown', hero=None, under=None,
    )
    assert not view.rows
    assert NOTHING_THIS_SIDE == 'Nothing in the next 3 hours'


def test_each_way_passes_over_a_cancelled_bus() -> None:
    """A cancelled bus never leads its kerb and stays in the list."""
    view = each_way(inputs=inputs_of(items=(
        item(minutes=3, across=False, cancelled=True),
        item(minutes=6, across=False), item(minutes=8, across=True),
    )), towards=BOTH.towards)
    assert view.each_way is not None
    assert view.each_way.here.hero is not None
    assert view.each_way.here.hero.countdown == '6'
    assert [row.countdown for row in view.rows] == ['3']


def test_each_way_names_a_side_where_no_end_was_named() -> None:
    """With no towards end the labels say which side each kerb is."""
    view = each_way(inputs=inputs_of(items=(
        item(minutes=2, across=True), item(minutes=5, across=False),
    )), towards=('', 'Newtown'))
    assert view.each_way is not None
    assert (view.each_way.here.label, view.each_way.here.end) == (
        THIS_SIDE, '',
    )
    unnamed = each_way(
        inputs=inputs_of(items=(item(minutes=2, across=True),)), towards=(),
    )
    assert unnamed.each_way is not None
    assert unnamed.each_way.across.label == OTHER_SIDE


def test_each_way_with_no_bus_is_the_empty_view() -> None:
    """With nothing in view the view is today's empty Stop view."""
    inputs = inputs_of(items=())
    assert each_way(inputs=inputs, towards=BOTH.towards) == build_stop_view(
        inputs=inputs,
    )


def test_hero_station_follows_the_hero_across() -> None:
    """The gauge reads the kerb the time-order hero leaves from."""
    view = build_stop_view(inputs=inputs_of(items=(
        item(minutes=2, across=True), item(minutes=5, across=False),
    )))
    assert hero_station(view=view, pin=BOTH, station='s2') == 'x2'
    assert hero_station(view=view, pin=BOTH, station='s9') == 's9'
    plain = replace(BOTH, opposite=None)
    assert hero_station(view=view, pin=plain, station='s2') == 's2'
