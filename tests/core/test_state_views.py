"""Tests for the views built from the board's state."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_sydney.city import SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import START, departure, place
from paperroute_testkit.core.strip_factories import on_view
from paperroute_transit.core.device import on_settings
from paperroute_transit.core.messages import SettingsChanged
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.saved import LiveView, StopSides
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import live_stop_view

BOTH: Final[Pin] = Pin(
    origin=place('s2'), kind=SydneyMode.BUS, opposite=place('x2'),
    towards=('Leichhardt', 'Newtown'),
)


def bus(*, minutes: int, across: bool) -> Departure:
    """A bus some minutes after START from one kerb."""
    dep = departure(
        departs=START + timedelta(minutes=minutes), mode=SydneyFeed.BUS,
    )
    return replace(dep, facts=replace(dep.facts, across=across))


def stop_state(*, pin: Pin, sides: StopSides) -> State:
    """The Stop view of one pin with a bus from each kerb loaded."""
    state, _ = on_view(
        view=LiveView.STOP,
        pin=pin,
        departures=(bus(minutes=2, across=True), bus(minutes=5, across=False)),
    )
    state, _ = on_settings(
        state=state, event=SettingsChanged(stop_sides=sides),
    )
    return state


def test_live_stop_view_shows_each_way_when_the_setting_asks() -> None:
    """A both-sides pin under next bus each way gets its two kerbs."""
    view = live_stop_view(
        state=stop_state(pin=BOTH, sides=StopSides.EACH_WAY),
    )
    assert view.each_way is not None
    assert view.each_way.here.end == 'Leichhardt'
    assert view.gauge is None


def test_live_stop_view_keeps_time_order_otherwise() -> None:
    """Time order, or a pin of one kerb, gives today's view."""
    time_order = stop_state(pin=BOTH, sides=StopSides.TIME_ORDER)
    assert live_stop_view(state=time_order).each_way is None
    plain = replace(BOTH, opposite=None, towards=())
    one_kerb = stop_state(pin=plain, sides=StopSides.EACH_WAY)
    assert live_stop_view(state=one_kerb).each_way is None
