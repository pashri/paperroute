"""Tests for the pins and the place shown."""

from dataclasses import replace
from datetime import timedelta

from paperroute_sydney.city import SYDNEY, SydneyFeed
from paperroute_testkit.core.factories import (
    START,
    departure,
    pin_at,
    place,
    snapshot,
)
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.map_state import area_box
from paperroute_transit.core.messages import MapData, ModesLoaded
from paperroute_transit.core.model import Pin
from paperroute_transit.core.places import (
    flipped,
    is_stale,
    matched,
    rematched,
    shown,
    shown_index,
    toggled,
    with_place,
)
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import (
    LiveView,
    Saved,
    default_saved,
    default_settings,
)
from paperroute_transit.core.state_model import Board, State
from paperroute_transit.core.tiles import ZOOMS
from tests.core.test_map_requests import fitted_on

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
        settings=default_settings(city=SYDNEY),
        pins=pins,
        current=current,
        unpinned=unpinned,
        view=LiveView.STOP,
    )
    return initial_state(saved=saved, now=START, city=SYDNEY)


def test_new_place_resets_step() -> None:
    """Showing another place drops the knob step."""
    state = state_with()
    state = replace(state, map=replace(state.map, step=3))
    assert with_place(state=state, pin=ELSEWHERE).map.step == 0


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
    """The board, scroll, map data, failures and step go; positions stay."""
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
            step=-2,
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
    assert after.map.step == 0
    assert not after.map.shapes
    assert not after.map.requested_shapes
    assert set(after.map.positions) == {'p'}
    assert not after.map.failed_at


def test_with_place_keeps_given_departures_and_their_feeds() -> None:
    """Departures handed over show at once and keep their feed's data."""
    bus = departure(departs=START + timedelta(minutes=3))
    train = departure(departs=START + timedelta(minutes=5), mode=SydneyFeed.TRAIN)
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


def test_matched_same_id_refreshes_name_and_point() -> None:
    """A stop found under its own ID takes the new name and point."""
    fresh = place('second', name='Second St', point=(250.0, 5.0))
    state = matched(state=state_with(), found={'second': fresh})
    assert state.places.pins[1] == Pin(origin=fresh)
    assert state.places.pins[0] == FIRST


def test_unmatched_pin_is_marked_missing() -> None:
    """A stop not found marks its pin missing and keeps its old data."""
    state = matched(state=state_with(), found={'third': None})
    assert state.places.pins[2] == replace(THIRD, missing=True)


def test_unmatched_destination_marks_the_pin_missing() -> None:
    """A Destination not found marks the pin missing too."""
    pin = replace(FIRST, destination=place('far', name='Far'))
    state = state_with(pins=(pin,))
    state = matched(
        state=state, found={'first': FIRST.origin, 'far': None}
    )
    assert state.places.pins == (replace(pin, missing=True),)


def test_matched_again_clears_missing() -> None:
    """A later match clears the missing mark."""
    state = matched(state=state_with(), found={'third': None})
    state = matched(state=state, found={'third': THIRD.origin})
    assert state.places.pins[2] == THIRD


def test_matched_onto_another_pin_marks_missing() -> None:
    """A re-match that would duplicate another pin keeps the old stop."""
    state = matched(state=state_with(), found={'third': FIRST.origin})
    assert state.places.pins == (FIRST, SECOND, replace(THIRD, missing=True))


def test_matched_updates_the_unpinned_place() -> None:
    """The unpinned place is matched like the pins."""
    moved = place('elsewhere-2', name='elsewhere', point=(905.0, 0.0))
    state = state_with(unpinned=ELSEWHERE)
    state = matched(state=state, found={'elsewhere': moved})
    assert state.places.unpinned == Pin(origin=moved)


def test_initial_state_without_any_place_shows_the_default() -> None:
    """A saved file with no pins and no unpinned place shows Town Hall."""
    saved = replace(default_saved(city=SYDNEY), unpinned=None, current=3)
    state = initial_state(saved=saved, now=START, city=SYDNEY)
    assert shown(state=state) == default_saved(city=SYDNEY).unpinned
    assert state.places.current == 0


def test_initial_state_clamps_an_out_of_range_current() -> None:
    """A current past the last pin shows the last pin."""
    assert shown(state=state_with(current=7)) == THIRD
    assert shown(state=state_with(current=-2)) == FIRST


def test_matched_partial_answer_keeps_missing() -> None:
    """A missing pin stays missing until every one of its stops is found."""
    far = place('far', name='Far')
    pin = replace(FIRST, destination=far)
    state = matched(state=state_with(pins=(pin,)), found={'far': None})
    renamed = replace(FIRST.origin, name='First St')
    state = matched(state=state, found={'first': renamed})
    assert state.places.pins == (
        replace(pin, origin=renamed, missing=True),
    )
    state = matched(state=state, found={'first': renamed, 'far': far})
    assert not state.places.pins[0].missing


def test_rematch_keeps_nickname_and_filter() -> None:
    """A new ID for the From keeps the nickname and route filter."""
    pin = Pin(
        origin=place('old'),
        nickname='Work',
        only=('606',),
    )
    fresh = place('new', name='New St')
    result = rematched(pin=pin, found={'old': fresh})
    assert result.origin == fresh
    assert result.nickname == 'Work'
    assert result.only == ('606',)


def test_modes_reply_for_the_frame_is_kept() -> None:
    """A modes reply for the fitted frame's area is not stale."""
    state, _ = fitted_on()
    assert state.map.frame is not None
    centre, zoom = state.map.frame
    start = state.map.centre
    assert start is not None
    assert centre != start
    mpp = ZOOMS[zoom]
    reply = ModesLoaded(
        mpp=mpp,
        box=area_box(centre=centre, mpp=mpp),
        modes=frozenset({SydneyFeed.BUS}),
    )
    assert not is_stale(state=state, event=reply)
    away = replace(reply, box=area_box(centre=start, mpp=mpp))
    assert is_stale(state=state, event=away)
