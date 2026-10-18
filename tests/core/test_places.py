"""Tests for the pins and the place shown."""

from dataclasses import replace
from datetime import timedelta

from paperroute.core.geo import make_shape
from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.messages import MapData
from paperroute.core.model import Mode, Pin
from paperroute.core.places import (
    flipped,
    shown,
    shown_index,
    toggled,
    with_place,
)
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state
from paperroute.core.state_model import Board, State
from tests.core.factories import START, departure, pin_at, snapshot

FIRST = pin_at('first', x=100.0)
SECOND = pin_at('second', x=200.0)
THIRD = pin_at('third', x=300.0)
ELSEWHERE = pin_at('elsewhere', x=900.0)
BUSES = 'v1/gtfs/realtime/buses'
TRAINS = 'v2/gtfs/realtime/sydneytrains'


def state_with(
    *,
    pins: tuple[Pin, ...] = (FIRST, SECOND, THIRD),
    current: int = 0,
    unpinned: Pin | None = None,
) -> State:
    """
    A state on the Stop view with some pins.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        The pins.
    current : int
        Index of the current pin.
    unpinned : Pin | None
        The unpinned place, if shown.

    Returns
    -------
    State
        Initial state.
    """
    saved = Saved(
        pins=pins,
        current=current,
        unpinned=unpinned,
        view=LiveView.STOP,
        zoom=DEFAULT_ZOOM,
    )
    return initial_state(saved=saved, now=START)


def test_shown_is_the_current_pin() -> None:
    """With no unpinned place, the current pin is shown at its index."""
    state = state_with(current=1)
    assert shown(state=state) == SECOND
    assert shown_index(state=state) == 1


def test_shown_is_the_unpinned_place_first() -> None:
    """An unpinned place is shown ahead of the pins, with no index."""
    state = state_with(unpinned=ELSEWHERE)
    assert shown(state=state) == ELSEWHERE
    assert shown_index(state=state) is None


def test_with_place_resets_what_belonged_to_the_old_place() -> None:
    """The board, scroll, map data and failures go; zoom and positions stay."""
    state = state_with()
    bus = departure(departs=START + timedelta(minutes=3))
    state = replace(
        state,
        board=Board(departures=(bus,), loaded_until=START, loading=True),
        feeds=(BUSES,),
        snapshots={BUSES: RealtimeSnapshot(fetched_at=START, trips={})},
        last_fetch=START,
        scroll=4,
        map=replace(
            state.map,
            zoom=0,
            shapes={'s': make_shape(shape_id='s', points=[(0, 0), (1, 1)])},
            requested_shapes=frozenset({'s'}),
            positions={'p': snapshot(fetched_at=START, vehicles=())},
            failed_at={MapData.SHAPES: START},
        ),
    )
    after = with_place(state=state, pin=ELSEWHERE)
    assert shown(state=after) == ELSEWHERE
    assert after.board == Board()
    assert (after.feeds, after.snapshots, after.scroll) == ((), {}, 0)
    assert after.last_fetch is None
    assert after.map.centre == ELSEWHERE.origin.point
    assert after.map.zoom == 0
    assert not after.map.shapes
    assert not after.map.requested_shapes
    assert set(after.map.positions) == {'p'}
    assert not after.map.failed_at


def test_with_place_keeps_given_departures_and_their_feeds() -> None:
    """Departures handed over show at once and keep their feed's data."""
    bus = departure(departs=START + timedelta(minutes=3))
    train = departure(departs=START + timedelta(minutes=5), mode=Mode.TRAIN)
    state = replace(
        state_with(),
        board=Board(departures=(train,)),
        feeds=(TRAINS,),
        snapshots={TRAINS: RealtimeSnapshot(fetched_at=START, trips={})},
    )
    after = with_place(state=state, pin=None, departures=(bus,))
    assert after.board == Board(departures=(bus,))
    assert after.feeds == (BUSES,)
    assert not after.snapshots


def test_flipped_wraps_both_ways() -> None:
    """Turning past either end wraps round the pins."""
    state = state_with()
    assert shown(state=flipped(state=state, steps=-1)) == THIRD
    assert shown(state=flipped(state=state, steps=4)) == SECOND


def test_flipped_by_whole_turns_keeps_the_state() -> None:
    """A flip that lands on the same pin changes nothing."""
    state = state_with()
    assert flipped(state=state, steps=3) is state


def test_unpinning_the_last_pin_keeps_current_in_range() -> None:
    """Unpinning the last pin moves current back onto the new last pin."""
    state = toggled(state=state_with(current=2))
    assert state.places.pins == (FIRST, SECOND)
    assert state.places.current == 1
    assert shown(state=state) == THIRD


def test_unpinning_the_only_pin_leaves_no_pins() -> None:
    """The only pin becomes the unpinned place."""
    state = toggled(state=state_with(pins=(FIRST,)))
    assert state.places.pins == ()
    assert state.places.current == 0
    assert shown(state=state) == FIRST
