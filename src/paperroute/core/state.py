"""The device's state and the pure function that advances it."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from paperroute.core.feeds import feed_for
from paperroute.core.live import resolve
from paperroute.core.model import Departure, Pin, shifted
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.stop_view import HORIZON, StopView, build_stop_view

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)


class Knob(StrEnum):
    """The two knobs."""

    TOP = 'top'
    BOTTOM = 'bottom'


@dataclass(frozen=True)
class Tick:
    """Time passed."""

    now: datetime


@dataclass(frozen=True)
class DeparturesLoaded:
    """Departures arrived from the timetable."""

    departures: tuple[Departure, ...]
    until: datetime


@dataclass(frozen=True)
class DeparturesFailed:
    """The timetable couldn't be read; the next tick tries again."""


@dataclass(frozen=True)
class FeedFetched:
    """A realtime feed was downloaded and parsed."""

    feed: str
    snapshot: RealtimeSnapshot


@dataclass(frozen=True)
class FeedFailed:
    """A realtime fetch failed."""

    feed: str
    at: datetime
    reason: str


@dataclass(frozen=True)
class KnobTurned:
    """A knob turned by some detents (negative is anticlockwise)."""

    knob: Knob
    steps: int


@dataclass(frozen=True)
class KnobPressed:
    """A knob was pressed."""

    knob: Knob


@dataclass(frozen=True)
class QuitRequested:
    """The user asked to quit (development monitor only)."""


Event = (
    Tick
    | DeparturesLoaded
    | DeparturesFailed
    | FeedFetched
    | FeedFailed
    | KnobTurned
    | KnobPressed
    | QuitRequested
)


@dataclass(frozen=True)
class LoadDepartures:
    """Ask the timetable for departures."""

    pin: Pin
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FetchFeed:
    """Download a realtime feed."""

    feed: str


@dataclass(frozen=True)
class Render:
    """Draw a view."""

    view: StopView


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


Effect = LoadDepartures | FetchFeed | Render | Stop


@dataclass(frozen=True)
class State:
    """Everything the app knows."""

    pin: Pin
    origin: str
    destination: str
    now: datetime
    departures: tuple[Departure, ...] = ()
    loaded_until: datetime | None = None
    loading: bool = False
    feeds: tuple[str, ...] = ()
    snapshots: Mapping[str, RealtimeSnapshot] = field(default_factory=dict)
    last_fetch: datetime | None = None
    scroll: int = 0
    last_view: StopView | None = None
    running: bool = True


def initial_state(
    *, pin: Pin, origin: str, destination: str, now: datetime
) -> State:
    """
    Make the state at start-up.

    Parameters
    ----------
    pin : Pin
        Pin to show.
    origin : str
        From name.
    destination : str
        Destination name.
    now : datetime
        Start time.

    Returns
    -------
    State
        State.
    """
    return State(pin=pin, origin=origin, destination=destination, now=now)


def current_view(*, state: State) -> StopView:
    """
    Build the view for the current state.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    StopView
        View.
    """
    resolved = [
        resolve(
            departure=dep,
            snapshot=state.snapshots.get(feed_for(mode=dep.mode) or ''),
            now=state.now,
        )
        for dep in state.departures
    ]
    fetched = [
        state.snapshots[feed].fetched_at if feed in state.snapshots else None
        for feed in state.feeds
    ]
    since = (
        None
        if None in fetched
        else min(
            (f for f in fetched if f is not None),
            key=lambda moment: moment.timestamp(),
            default=state.now,
        )
    )
    return build_stop_view(
        origin=state.origin,
        destination=state.destination,
        departures=resolved,
        now=state.now,
        scroll=state.scroll,
        live_since=since,
        has_feed=bool(state.feeds) or not state.departures,
        loaded=state.loaded_until is not None,
    )


def rendered(
    *, state: State, effects: tuple[Effect, ...] = ()
) -> tuple[State, tuple[Effect, ...]]:
    """
    Add a Render effect when the view changed.

    Parameters
    ----------
    state : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects so far.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the view and scroll recorded, and effects.
    """
    view = current_view(state=state)
    state = replace(state, scroll=view.scroll)
    if view == state.last_view:
        return state, effects
    return replace(state, last_view=view), (*effects, Render(view=view))


def reload_due(*, state: State, now: datetime) -> bool:
    """
    Say whether the loaded departures are missing or reach under HORIZON.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when less than HORIZON of real time remains loaded.
    """
    if state.loaded_until is None:
        return True
    left = state.loaded_until.timestamp() - now.timestamp()
    return left < HORIZON.total_seconds()


def poll_due(*, state: State, now: datetime) -> bool:
    """
    Say whether the feeds should be fetched again.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when there are feeds and POLL_EVERY of real time has passed
        since the last fetch.
    """
    if not state.feeds:
        return False
    if state.last_fetch is None:
        return True
    since = now.timestamp() - state.last_fetch.timestamp()
    return since >= POLL_EVERY.total_seconds()


def on_tick(
    *, state: State, event: Tick
) -> tuple[State, tuple[Effect, ...]]:
    """
    Advance time: reload, poll and redraw as needed.

    Parameters
    ----------
    state : State
        State.
    event : Tick
        Tick.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    state = replace(state, now=event.now)
    effects: list[Effect] = []
    if reload_due(state=state, now=event.now) and not state.loading:
        reach = HORIZON + RELOAD_BEFORE
        start = shifted(
            moment=event.now, seconds=-int(LOOKBACK.total_seconds())
        )
        end = shifted(moment=event.now, seconds=int(reach.total_seconds()))
        effects.append(LoadDepartures(pin=state.pin, start=start, end=end))
        state = replace(state, loading=True)
    if poll_due(state=state, now=event.now):
        effects.extend(FetchFeed(feed=feed) for feed in state.feeds)
        state = replace(state, last_fetch=event.now)
    return rendered(state=state, effects=tuple(effects))


def on_loaded(
    *, state: State, event: DeparturesLoaded
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store departures and the feeds their modes need.

    Parameters
    ----------
    state : State
        State.
    event : DeparturesLoaded
        Loaded departures.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    feeds = tuple(
        sorted(
            {
                feed
                for dep in event.departures
                if (feed := feed_for(mode=dep.mode)) is not None
            }
        )
    )
    return rendered(
        state=replace(
            state,
            departures=event.departures,
            loaded_until=event.until,
            loading=False,
            feeds=feeds,
        )
    )


def step(state: State, event: Event) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply one event.

    Parameters
    ----------
    state : State
        Current state.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Next state and the effects to perform.
    """
    result: tuple[State, tuple[Effect, ...]] = (state, ())
    match event:
        case Tick():
            result = on_tick(state=state, event=event)
        case DeparturesLoaded():
            result = on_loaded(state=state, event=event)
        case DeparturesFailed():
            result = rendered(state=replace(state, loading=False))
        case FeedFetched(feed=feed, snapshot=snapshot):
            result = rendered(
                state=replace(
                    state, snapshots={**state.snapshots, feed: snapshot}
                )
            )
        case FeedFailed():
            result = rendered(state=state)
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            result = rendered(
                state=replace(state, scroll=max(0, state.scroll + steps))
            )
        case QuitRequested():
            result = replace(state, running=False), (Stop(),)
    return result
