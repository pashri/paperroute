"""Shared helpers for the state tests."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute_device.core.kit_effects import SaveState
from paperroute_device.core.kit_events import Tick
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
)
from paperroute_sydney.city import SYDNEY, SydneyFeed
from paperroute_testkit.core.factories import (
    START,
    begun,
    departure,
    pin_at,
    place,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import step_answered
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_state import area_frame
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    FetchFeed,
)
from paperroute_transit.core.model import Pin
from paperroute_transit.core.places import shown
from paperroute_transit.core.saved import (
    LIVE_VIEWS,
    LiveView,
    Saved,
    default_settings,
    seeded,
)
from paperroute_transit.core.state_model import RELOAD_BEFORE, State
from paperroute_transit.core.state_views import live_stop_view, saved_of
from paperroute_transit.core.stop_view import HORIZON
from paperroute_transit.core.strip_model import trip_day
from paperroute_transit.core.strip_select import panel_trains

PIN = Pin(
    origin=place('2145615', name='Hollis St'),
    destination=place('215020', name='Parramatta'),
)


BUSES = 'v1/gtfs/realtime/buses'


ALERTS: Final[str] = 'v2/gtfs/alerts/buses'


BUS_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/buses'


FERRY_POSITIONS: Final[str] = 'v1/gtfs/vehiclepos/ferries/sydneyferries'


CENTRE: Final[Point] = (-17_000.0, 1_000.0)


PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)


PINS: Final[tuple[Pin, ...]] = (
    pin_at('first', x=100.0),
    pin_at('second', x=200.0),
    pin_at('third', x=300.0),
)


ELSEWHERE: Final[Pin] = pin_at('elsewhere', x=900.0)


def fresh(*, now: datetime = START, centre: Point | None = None) -> State:
    """
    A state with nothing loaded, started long enough ago to draw.

    Parameters
    ----------
    now : datetime
        Start time.
    centre : Point | None
        The From stop in metres.

    Returns
    -------
    State
        Initial state.
    """
    pin = replace(PIN, origin=replace(PIN.origin, point=centre))
    return begun(saved=seeded(pin=pin, city=SYDNEY), now=now)


def loaded(
    *,
    state: State,
    count: int = 3,
    mode: SydneyFeed = SydneyFeed.BUS,
    shaped: bool = False,
    kind: str | None = None,
) -> State:
    """
    Apply a DeparturesLoaded event.

    Parameters
    ----------
    state : State
        State before.
    count : int
        Departures, three minutes apart.
    mode : SydneyFeed
        Their mode.
    shaped : bool
        Give each departure its own shape ID.
    kind : str | None
        Their transport mode, if set.

    Returns
    -------
    State
        State after.
    """
    deps = tuple(
        replace(
            departure(
                departs=START + timedelta(minutes=3 * (i + 1)),
                mode=mode,
                kind=kind,
            ),
            shape_id=f'b:{i}' if shaped else None,
        )
        for i in range(count)
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=deps,
        until=max(START, state.now) + HORIZON + RELOAD_BEFORE,
    )
    return step_answered(state=state, event=event)[0]


def effects_of[T](
    *, effects: tuple[Effect, ...], kind: type[T]
) -> list[T]:
    """
    Pick the effects of one type.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.
    kind : type[T]
        Effect type.

    Returns
    -------
    list[T]
        The matching effects, in order.
    """
    return [effect for effect in effects if isinstance(effect, kind)]


def trip_fetches(*, effects: tuple[Effect, ...]) -> list[FetchFeed]:
    """
    Pick the feed fetches other than alert fetches.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[FetchFeed]
        The trip and position fetches, in order.
    """
    return [
        fetch
        for fetch in effects_of(effects=effects, kind=FetchFeed)
        if fetch.kind != FeedKind.ALERTS
    ]


def turned(*, state: State, steps: int) -> tuple[State, tuple[Effect, ...]]:
    """
    Turn the bottom knob.

    Parameters
    ----------
    state : State
        State before.
    steps : int
        Detents, clockwise positive.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects after.
    """
    return step(state, KnobTurned(knob=Knob.BOTTOM, steps=steps))


def map_zoom(*, state: State) -> int:
    """
    Give the zoom of the map area shown.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    int
        The frame's zoom, else the resting zoom.
    """
    return area_frame(map_state=state.map)[1]


def left_for_stop(*, state: State) -> State:
    """
    Press from the Map round to the Stop view, all at one moment.

    Parameters
    ----------
    state : State
        State on the Map.

    Returns
    -------
    State
        State on the Stop view, which polls no positions.
    """
    for _ in range(len(LIVE_VIEWS) - 1):
        state, _ = step(state, PRESS)
    assert state.view is LiveView.STOP
    return state


def check_selection(*, before: State, after: State, event: Event) -> None:
    """
    Assert a picked train is in the panel of the Strip on screen, and a
    turn while held changes nothing saved.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    """
    selected = after.strip.selected
    if selected is not None:
        assert after.view is LiveView.STRIP and after.chooser is None
        trains = panel_trains(stop_view=live_stop_view(state=after))
        assert selected in {trip_day(departure=train) for train in trains}
    if isinstance(event, KnobHeldTurned):
        counted = replace(saved_of(state=after), quota=before.quota)
        assert counted == saved_of(state=before)
        assert after.chooser == before.chooser
        if event.knob is Knob.TOP or before.view is not LiveView.STRIP:
            assert after.strip.selected == before.strip.selected


HOLD: Final[KnobHeld] = KnobHeld(knob=Knob.BOTTOM)


def pinned(
    *,
    current: int = 0,
    unpinned: Pin | None = None,
    view: LiveView = LiveView.STOP,
) -> State:
    """
    A state with three pins, drawn once.

    Parameters
    ----------
    current : int
        Index of the current pin.
    unpinned : Pin | None
        The unpinned place, if shown.
    view : LiveView
        The Live view shown.

    Returns
    -------
    State
        The state after its first tick.
    """
    saved = Saved(
        settings=default_settings(city=SYDNEY),
        pins=PINS,
        current=current,
        unpinned=unpinned,
        view=view,
    )
    state = begun(saved=saved)
    state, _ = step(state, Tick(now=START))
    return state


def top(*, steps: int) -> KnobTurned:
    """
    Turn the top knob.

    Parameters
    ----------
    steps : int
        Detents.

    Returns
    -------
    KnobTurned
        The turn.
    """
    return KnobTurned(knob=Knob.TOP, steps=steps)


def saves(*, effects: tuple[Effect, ...]) -> list[SaveState]:
    """
    Pick the saves from some effects.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[SaveState]
        The saves, in order.
    """
    return effects_of(effects=effects, kind=SaveState)


def at(*, seconds: int) -> Tick:
    """
    Tick some seconds after START.

    Parameters
    ----------
    seconds : int
        Seconds after START.

    Returns
    -------
    Tick
        The tick.
    """
    return Tick(now=START + timedelta(seconds=seconds))
