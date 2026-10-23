"""Tests for the positions feeds the Map and Strip poll."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute.core.feeds import ALL_POSITION_MODES, position_feeds
from paperroute.core.geo import Point
from paperroute.core.map_requests import map_feeds, request_positions
from paperroute.core.map_state import area_box
from paperroute.core.messages import LoadModes, MapDataFailed, ModesLoaded
from paperroute.core.model import SYDNEY, Departure, Mode
from paperroute.core.saved import LiveView
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.tiles import ZOOMS
from tests.core.factories import START, departure
from tests.core.strip_factories import CENTRE, on_view, trains


def bus_pin() -> tuple[Departure, ...]:
    """
    Make one bus departure.

    Returns
    -------
    tuple[Departure, ...]
        The departure.
    """
    return (
        departure(departs=START + timedelta(minutes=3), mode=Mode.BUS),
    )


def on_map() -> State:
    """
    Start on the Map with a bus pin and no area modes.

    Returns
    -------
    State
        The state.
    """
    state, _ = on_view(view=LiveView.MAP, departures=bus_pin())
    return state


def area(*, state: State, centre: Point = CENTRE) -> LoadModes:
    """
    Ask for the modes in the area at the state's zoom.

    Parameters
    ----------
    state : State
        State on the Map.
    centre : Point
        Map centre.

    Returns
    -------
    LoadModes
        The request.
    """
    mpp = ZOOMS[state.map.zoom]
    return LoadModes(mpp=mpp, box=area_box(centre=centre, mpp=mpp))


def test_map_polls_all_feeds_before_modes_load() -> None:
    """Until the area's modes are known, every position feed is polled."""
    feeds = map_feeds(state=on_map())
    assert feeds == position_feeds(modes=ALL_POSITION_MODES)
    assert len(feeds) == 9


def test_map_polls_all_feeds_after_modes_fail() -> None:
    """A failed modes load leaves every position feed polled."""
    state = on_map()
    state, _ = step(state, MapDataFailed(request=area(state=state)))
    assert map_feeds(state=state) == position_feeds(
        modes=ALL_POSITION_MODES
    )


def test_map_polls_all_feeds_for_empty_modes() -> None:
    """An area with no shapes polls every position feed."""
    state = on_map()
    request = area(state=state)
    event = ModesLoaded(
        mpp=request.mpp, box=request.box, modes=frozenset()
    )
    state, _ = step(state, event)
    assert map_feeds(state=state) == position_feeds(
        modes=ALL_POSITION_MODES
    )


def test_map_polls_area_modes_when_known() -> None:
    """Known area modes poll exactly their feeds."""
    state = on_map()
    request = area(state=state)
    modes = frozenset({Mode.TRAIN, Mode.BUS})
    event = ModesLoaded(mpp=request.mpp, box=request.box, modes=modes)
    state, _ = step(state, event)
    assert map_feeds(state=state) == position_feeds(modes=modes)
    assert len(map_feeds(state=state)) == 2


def test_strip_feeds_unchanged() -> None:
    """The Strip polls only its departures' modes."""
    state, _ = on_view(
        view=LiveView.STRIP, departures=(*bus_pin(), *trains()),
    )
    assert map_feeds(state=state) == position_feeds(modes={Mode.TRAIN})


def test_positions_follow_rate() -> None:
    """Off-peak, the Map's positions are due two minutes after a fetch."""
    noon = datetime(2026, 10, 16, 12, 0, tzinfo=SYDNEY)
    state = replace(on_map(), now=noon)
    for ago, due in ((60, False), (120, True)):
        fetched = noon - timedelta(seconds=ago)
        fetched_at = dict.fromkeys(map_feeds(state=state), fetched)
        stale = replace(state, map=replace(state.map, fetched_at=fetched_at))
        _, effects = request_positions(state=stale)
        assert bool(effects) is due
