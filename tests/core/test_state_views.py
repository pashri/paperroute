"""Tests for the views built from the board's state."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_sydney.city import SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import START, departure, place
from paperroute_testkit.core.strip_factories import on_view
from paperroute_transit.core import state_views
from paperroute_transit.core.device import on_settings
from paperroute_transit.core.messages import SettingsChanged
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.saved import LiveView, StopSides
from paperroute_transit.core.state_model import State, with_device
from paperroute_transit.core.state_views import (
    STOP_VIEWS,
    banner_live_view,
    device_notice,
    live_stop_view,
    settled,
)
from paperroute_transit.core.stop_view import (
    StopInputs,
    StopView,
    build_stop_view,
)

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


def counted_builds(*, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the Stop views built from here on."""
    builds: list[int] = []
    STOP_VIEWS.clear()
    real = build_stop_view

    def build(*, inputs: StopInputs) -> StopView:
        """Build a view and note it."""
        builds.append(1)
        return real(inputs=inputs)

    monkeypatch.setattr(state_views, 'build_stop_view', build)
    return builds


def test_banner_live_view_builds_once_for_settle_and_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settling and then rendering one tick builds the view once."""
    state = stop_state(pin=BOTH, sides=StopSides.TIME_ORDER)
    builds = counted_builds(monkeypatch=monkeypatch)
    settled_state, _ = settled(state=state)
    banner = device_notice(state=settled_state)
    first = banner_live_view(state=settled_state, banner=banner)
    again = banner_live_view(
        state=with_device(state=settled_state, device=DeviceState(now=START)),
        banner=banner,
    )
    assert len(builds) == 1
    assert again is first


def test_banner_live_view_builds_again_on_the_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later frame time, new departures or another banner rebuild."""
    state = stop_state(pin=BOTH, sides=StopSides.TIME_ORDER)
    builds = counted_builds(monkeypatch=monkeypatch)
    banner_live_view(state=state, banner=None)
    later = replace(state, now=state.now + timedelta(minutes=1))
    banner_live_view(state=later, banner=None)
    fresh = replace(
        state, board=replace(state.board, departures=state.board.departures),
    )
    banner_live_view(state=fresh, banner=None)
    banner_live_view(state=state, banner=Banner(text='x'))
    assert len(builds) == 4
