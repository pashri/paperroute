"""Tests for the cache of the train Strip a Reach is laid out from."""

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute_testkit.core.factories import START, place
from paperroute_transit.core import strip_state
from paperroute_transit.core.places import shown
from paperroute_transit.core.reach import Reach
from paperroute_transit.core.state_model import State
from paperroute_transit.core.strip_state import shown_reach
from paperroute_transit.core.train_follow import train_strip
from tests.core.test_strip_state import train_state, train_trip

LINE = ('E2', 'C', 'D', 'F', 'G')
TRIPS = [
    train_trip(trip_id='a', stations=LINE, at=5),
    train_trip(trip_id='b', stations=LINE, at=15),
]


class Builds:
    """
    Count the calls to the builder the Strip cache stands in front of.

    Attributes
    ----------
    count : int
        Calls so far.
    """

    def __init__(self, *, build: Callable[..., Reach]) -> None:
        self.count = 0
        self.build = build

    def __call__(self, **kwargs: object) -> Reach:
        """
        Count one build and run it.

        Parameters
        ----------
        **kwargs : object
            The builder's arguments.

        Returns
        -------
        Reach
            The built Reach.
        """
        self.count += 1
        return self.build(**kwargs)


@pytest.fixture(name='builds')
def _builds(monkeypatch: pytest.MonkeyPatch) -> Builds:
    counted = Builds(build=train_strip)
    monkeypatch.setattr(strip_state, 'train_strip', counted)
    return counted


def fresh(*, destination: str | None = 'F') -> State:
    """
    Make a train state whose inputs are new objects.

    Parameters
    ----------
    destination : str | None
        The Destination station ID.

    Returns
    -------
    State
        State on a train Strip.
    """
    return train_state(trips=TRIPS, destination=destination)


def cold() -> State:
    """
    Make a train state that another state's Strip has replaced.

    Returns
    -------
    State
        State on a train Strip whose Strip is not remembered.
    """
    state = fresh()
    fresh()
    return state


def with_destination(*, state: State, destination: str) -> State:
    """
    Move the shown pin's Destination.

    Parameters
    ----------
    state : State
        State on a train Strip.
    destination : str
        The new Destination station ID.

    Returns
    -------
    State
        State with only the Destination changed.
    """
    pin = replace(
        shown(state=state), destination=place(destination, name=destination),
    )
    places = state.places
    if places.unpinned is not None:
        return replace(state, places=replace(places, unpinned=pin))
    pins = tuple(
        pin if index == places.current else old
        for index, old in enumerate(places.pins)
    )
    return replace(state, places=replace(places, pins=pins))


def test_the_same_inputs_build_once(builds: Builds) -> None:
    """A second ask on an unchanged state returns the first Reach."""
    state = cold()
    before = builds.count
    first = shown_reach(state=state)
    second = shown_reach(state=state)
    assert builds.count == before + 1
    assert first is second


def test_the_same_minute_builds_once(builds: Builds) -> None:
    """Seconds inside the frame's minute do not rebuild."""
    state = cold()
    before = builds.count
    first = shown_reach(state=state)
    later = replace(state, now=state.now + timedelta(seconds=20))
    assert shown_reach(state=later) is first
    assert builds.count == before + 1


def rebuilt_by(*, change: Callable[[State], State], builds: Builds) -> bool:
    """
    Say whether a change to a warm state's input rebuilds the Strip.

    Parameters
    ----------
    change : Callable[[State], State]
        The change to make.
    builds : Builds
        The builder's call count.

    Returns
    -------
    bool
        True when the next ask built again.
    """
    state = fresh()
    shown_reach(state=state)
    before = builds.count
    shown_reach(state=change(state))
    return builds.count == before + 1


def new_reach(state: State) -> State:
    """
    Replace the built reach by an equal one.

    Parameters
    ----------
    state : State
        State on a train Strip.

    Returns
    -------
    State
        State holding a copy of the reach.
    """
    strip = state.strip
    return replace(state, strip=replace(strip, reaches={
        key: replace(reach) for key, reach in strip.reaches.items()
        if reach is not None
    }))


CHANGES: dict[str, Callable[[State], State]] = {
    'departures': lambda s: replace(
        s, board=replace(s.board, departures=(*s.board.departures,)),
    ),
    'patterns': lambda s: replace(
        s, map=replace(s.map, patterns=dict(s.map.patterns)),
    ),
    'snapshots': lambda s: replace(s, snapshots=dict(s.snapshots)),
    'selected': lambda s: replace(
        s, strip=replace(s.strip, selected=('b', START.date())),
    ),
    'destination': lambda s: with_destination(state=s, destination='G'),
    'reach': new_reach,
    'minute': lambda s: replace(s, now=s.now + timedelta(minutes=1)),
}


@pytest.mark.parametrize('name', sorted(CHANGES))
def test_each_changed_input_rebuilds(name: str, builds: Builds) -> None:
    """Changing any one keyed input builds the Strip again."""
    assert rebuilt_by(change=CHANGES[name], builds=builds)


def test_a_cached_strip_equals_a_fresh_one(builds: Builds) -> None:
    """The remembered Reach is the Reach a rebuild gives."""
    state = cold()
    before = builds.count
    cached = shown_reach(state=state)
    assert shown_reach(state=state) is cached
    assert shown_reach(state=new_reach(state)) == cached
    assert builds.count == before + 2


def test_two_pins_alternate_correctly(builds: Builds) -> None:
    """Asking for one pin, then another, then the first gives each its own."""
    near = fresh(destination='D')
    far = train_state(
        trips=[train_trip(trip_id='x', stations=('C', 'S1', 'S2'), at=5)],
    )
    before = builds.count
    one, two = shown_reach(state=near), shown_reach(state=far)
    assert one != two
    assert shown_reach(state=near) == one
    assert shown_reach(state=far) == two
    assert builds.count == before + 4
