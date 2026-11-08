"""Tests that an idle board redraws only on a poll cycle or the minute."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import (
    START,
    calls,
    departure,
    pattern,
    snapshot,
    vehicle,
)
from paperroute_testkit.core.steps import wakeful
from paperroute_transit.core.alert_model import AlertsFetched, AlertsSnapshot
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import Point
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    FeedFetched,
    FetchFeed,
    LoadPatterns,
    PatternsLoaded,
    PositionsFetched,
    Render,
    Tick,
)
from paperroute_transit.core.model import service_seconds
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.places import shown
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import RELOAD_BEFORE, State
from paperroute_transit.core.stop_view import HORIZON
from tests.core.test_state import CENTRE, PRESS, fresh

TICK: Final[timedelta] = timedelta(seconds=5)
IDLE_FOR: Final[timedelta] = timedelta(minutes=15)
LAGS: Final[tuple[int, ...]] = (30, 50, 70, 90)  # seconds
EAST: Final[Point] = (CENTRE[0] + 2000.0, CENTRE[1])


def board() -> State:
    """
    Load buses leaving 20 s past every third minute, off-peak.

    Returns
    -------
    State
        State at `START` on the Stop view, centred on `CENTRE`, with
        five departures, no peak windows and no quiet hours.
    """
    state = off_peak(state=fresh(centre=CENTRE))
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=tuple(
            departure(departs=START + timedelta(minutes=3 * n, seconds=20))
            for n in range(1, 6)
        ),
        until=START + HORIZON + RELOAD_BEFORE,
    )
    return stepped(state=state, event=event)[0]


def off_peak(*, state: State) -> State:
    """
    Make every hour off-peak and none quiet.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with no peak windows and no quiet hours.
    """
    board = replace(state.settings.board, peak_windows=())
    settings = replace(state.settings, board=board)
    return wakeful(state=replace(state, settings=settings))


def running(*, trip_id: str) -> TripPattern:
    """
    Make a trip that runs past `CENTRE` all through the test.

    Parameters
    ----------
    trip_id : str
        Trip ID.

    Returns
    -------
    TripPattern
        A pattern from `CENTRE` to 2 km east, over the idle time.
    """
    begins = service_seconds(day=START.date(), moment=START, zone=SYDNEY_ZONE)
    return pattern(
        trip_id=trip_id,
        stops=calls(
            stations=('A', 'B'),
            start=begins,
            step=int(IDLE_FOR.total_seconds()) + 60,
            points={'A': CENTRE, 'B': EAST},
        ),
    )


def fetched(*, fetch: FetchFeed, now: datetime) -> Event:
    """
    Answer a fetch with a download made now.

    Parameters
    ----------
    fetch : FetchFeed
        The fetch.
    now : datetime
        The time of the download.

    Returns
    -------
    Event
        Vehicles heard `LAGS` ago for a positions feed; empty trip
        updates or alerts otherwise.
    """
    match fetch.kind:
        case FeedKind.POSITIONS:
            heard = int(now.timestamp())
            vehicles = tuple(
                vehicle(
                    timestamp=heard - lag,
                    trip_id=f'v{lag}',
                    point=(CENTRE[0] + 10.0 * lag, CENTRE[1]),
                )
                for lag in LAGS
            )
            return PositionsFetched(
                feed=fetch.feed,
                snapshot=snapshot(fetched_at=now, vehicles=vehicles),
            )
        case FeedKind.ALERTS:
            return AlertsFetched(
                feed=fetch.feed,
                snapshot=AlertsSnapshot(
                    feed=fetch.feed, fetched_at=now, alerts=(),
                ),
            )
    return FeedFetched(
        feed=fetch.feed,
        snapshot=RealtimeSnapshot(fetched_at=now, trips={}),
    )


def answer(*, effect: Effect, now: datetime) -> Event | None:
    """
    Answer a feed fetch or a pattern load.

    Parameters
    ----------
    effect : Effect
        An effect.
    now : datetime
        The time of the answer.

    Returns
    -------
    Event | None
        The answer, or None for an effect left unanswered.
    """
    if isinstance(effect, FetchFeed):
        return fetched(fetch=effect, now=now)
    if isinstance(effect, LoadPatterns):
        return PatternsLoaded(
            trip_ids=effect.trip_ids,
            patterns={
                trip_id: running(trip_id=trip_id)
                for trip_id in effect.trip_ids
            },
            shapes={},
        )
    return None


def stepped(*, state: State, event: Event) -> tuple[State, int]:
    """
    Apply an event and answer what it asks for, in turn.

    Parameters
    ----------
    state : State
        State.
    event : Event
        The event.

    Returns
    -------
    tuple[State, int]
        State after every answer, and the Renders made.
    """
    pending, renders = [event], 0
    while pending:
        state, effects = step(state, pending.pop(0))
        renders += sum(isinstance(effect, Render) for effect in effects)
        pending.extend(
            answered
            for effect in effects
            if (answered := answer(effect=effect, now=state.now))
        )
    return state, renders


@dataclass(frozen=True)
class Idle:
    """
    What an idle board drew.

    Attributes
    ----------
    renders : int
        The Renders made.
    cycles : int
        The poll cycles started.
    minutes : int
        The clock minutes seen.
    stray : tuple[datetime, ...]
        The ticks that drew though no cycle started and the minute had
        not turned.
    """

    renders: int
    cycles: int
    minutes: int
    stray: tuple[datetime, ...]


def idle(*, state: State) -> Idle:
    """
    Tick an untouched board every 5 s for 15 minutes.

    Parameters
    ----------
    state : State
        State at `START`.

    Returns
    -------
    Idle
        What it drew.
    """
    renders, cycles, minutes, stray = 0, set(), set(), []
    now = START + TICK
    while now <= START + IDLE_FOR:
        state, drawn = stepped(state=state, event=Tick(now=now))
        turned = now.replace(second=0) not in minutes
        if drawn and not turned and state.cycle.started != now:
            stray.append(now)
        renders += drawn
        cycles.add(state.cycle.started)
        minutes.add(now.replace(second=0))
        now += TICK
    return Idle(
        renders=renders,
        cycles=len(cycles),
        minutes=len(minutes),
        stray=tuple(stray),
    )


def test_idle_map_draws_only_on_a_cycle_or_the_minute() -> None:
    """Vehicles going stale and hollow markers move no frame of their own."""
    state, _ = stepped(state=board(), event=PRESS)
    drawn = idle(state=state)
    assert drawn.cycles > 1
    assert not drawn.stray
    assert drawn.renders <= drawn.cycles + drawn.minutes


def test_idle_stop_draws_only_on_a_cycle_or_the_minute() -> None:
    """Departures dropping off and the live line wait for the minute."""
    drawn = idle(state=board())
    assert drawn.cycles > 1
    assert not drawn.stray
    assert drawn.renders <= drawn.cycles + drawn.minutes
