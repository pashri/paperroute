"""Tests for the frame the Map opens with."""

from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute_cambridge.city import CAMBRIDGE
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.strip_factories import PIN, on_view
from paperroute_transit.core.day_lines import line_choice
from paperroute_transit.core.map_fit import line_framed, steady_frame
from paperroute_transit.core.map_requests import (
    map_effects,
    map_feeds,
    map_inputs,
    pin_map_inputs,
    settled_key,
)
from paperroute_transit.core.map_vehicles import heard_vehicles
from paperroute_transit.core.model import Departure, TripFacts
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.saved import LiveView, MapFraming
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.core.tiles import ZOOMS
from tests.core.test_map_requests import bus_pin


def train(*, minutes: int = 0) -> Departure:
    """A train leaving the From, rail by its route type."""
    return replace(
        departure(departs=START + timedelta(minutes=minutes), route='GA'),
        facts=TripFacts(route_id='rail:GA:Norwich', road=False),
    )


def replacement_bus(*, minutes: int = 0) -> Departure:
    """A type 714 rail replacement bus, road by its own route type."""
    return replace(
        departure(departs=START + timedelta(minutes=minutes), route='GA'),
        facts=TripFacts(route_id='rail:GA:Norwich', road=True),
    )


def opened(*, kind: str, departures: tuple[Departure, ...]) -> State:
    """A plain pin of `kind` whose Map has drawn once."""
    state, _ = on_view(
        view=LiveView.MAP,
        pin=replace(PIN, destination=None, kind=kind),
        departures=departures,
    )
    state, _, _ = map_effects(
        state=state, stop_view=live_stop_view(state=state),
    )
    return state


def zoom_of(*, state: State) -> int:
    """The m/px the shown map is drawn at."""
    inputs = map_inputs(state=state, stop_view=live_stop_view(state=state))
    return ZOOMS[inputs.zoom]


@pytest.mark.parametrize('kind', ['train', 'metro', 'light_rail', 'ferry'])
def test_rail_and_ferry_open_centred_at_25_m_per_px(kind: str) -> None:
    """A Sydney line-mode pin opens at 25 m/px under either framing."""
    for framing in MapFraming:
        state = opened(kind=kind, departures=(train(),))
        board = replace(state.settings.board, map_framing=framing)
        state = replace(
            state, settings=replace(state.settings, board=board),
        )
        assert zoom_of(state=state) == 25


@pytest.mark.parametrize('kind', ['bus', 'coach', 'school', 'replacement'])
def test_road_pins_keep_the_bus_framing(kind: str) -> None:
    """Bus, coach, school and replacement-bus pins frame as buses."""
    state = opened(kind=kind, departures=bus_pin())
    shown = live_stop_view(state=state)
    expected = steady_frame(
        inputs=pin_map_inputs(state=state, stop_view=shown),
        framing=MapFraming.APPROACH,
        held=None,
        key=None,
        choice=line_choice(state=state, stop_view=shown),
    )
    assert map_inputs(state=state, stop_view=shown) == expected


def test_a_train_pin_opens_with_z2_whatever_runs_first() -> None:
    """A Cambridge train pin whose next departure is a 714 opens at 25."""
    first = (replacement_bus(), train(minutes=20))
    state = replace(opened(kind='train', departures=first), city=CAMBRIDGE)
    assert zoom_of(state=state) == 25
    board = replace(state.board, departures=(train(),))
    assert zoom_of(state=replace(state, board=board)) == 25


def test_only_kinds_chosen_by_line_take_z2() -> None:
    """Cambridge's trains take Z2; its buses, coaches and no kind do not."""
    assert line_framed(kind='train', modes=CAMBRIDGE.modes) is True
    assert not any(
        line_framed(kind=kind, modes=CAMBRIDGE.modes)
        for kind in ('bus', 'coach', None)
    )


def test_a_z2_frame_has_no_settled_key() -> None:
    """A map opened with Z2 holds no Approach frame."""
    state = opened(kind='train', departures=(train(),))
    inputs = map_inputs(state=state, stop_view=live_stop_view(state=state))
    assert settled_key(state=state, inputs=inputs) is None


def test_pin_map_inputs_vehicles_reused_across_ticks() -> None:
    """Two builds from one state pick the vehicles once."""
    state = opened(kind='bus', departures=bus_pin())
    bus = VehiclePosition(
        trip_id='t1',
        route_id='r',
        label='606',
        point=(0.0, 0.0),
        bearing=None,
        timestamp=1,
        start_date=None,
    )
    feed = map_feeds(state=state)[0]
    download = PositionsSnapshot(
        fetched_at=state.now, header_time=1, vehicles=(bus,),
    )
    state = replace(
        state, map=replace(state.map, positions={feed: download}),
    )
    shown = live_stop_view(state=state)
    first = pin_map_inputs(state=state, stop_view=shown).positions
    again = pin_map_inputs(state=state, stop_view=shown).positions
    assert first
    picked = heard_vehicles(positions=first)
    assert picked
    assert heard_vehicles(positions=again) is picked
