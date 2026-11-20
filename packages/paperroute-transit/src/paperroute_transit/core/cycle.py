"""Clock-aligned timing of the poll cycles."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute_transit.core.alert_state import ALERT_EVERY, alert_feeds
from paperroute_transit.core.banners import feeds_held
from paperroute_transit.core.chains import all_departures
from paperroute_transit.core.chooser_steps import (
    departures_chooser,
    trip_feeds,
)
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.map_requests import (
    map_feeds,
    shows_vehicles,
    stop_feeds,
)
from paperroute_transit.core.map_view import pin_trips
from paperroute_transit.core.messages import Effect, Estimating, FetchFeed
from paperroute_transit.core.rhythm import poll_every, stale_after
from paperroute_transit.core.state_model import (
    Cycle,
    State,
    clock_waiting,
    elapsed,
)
from paperroute_transit.core.stop_view import StopView

CYCLE_WAIT: Final[timedelta] = timedelta(seconds=8)


def boundary(*, now: datetime, every: timedelta) -> datetime:
    """
    Find the latest clock-aligned boundary at or before a time.

    Boundaries are measured in real seconds from local midnight's
    offset, so a repeated hour at a clock change still gives
    boundaries a whole period apart.

    Parameters
    ----------
    now : datetime
        A timezone-aware time.
    every : timedelta
        The period between boundaries.

    Returns
    -------
    datetime
        The boundary, in the same timezone as ``now``.
    """
    ts = now.timestamp()
    off = (now.utcoffset() or timedelta()).total_seconds()
    start = ts - ((ts + off) % every.total_seconds())
    return datetime.fromtimestamp(start, tz=now.tzinfo)


def stale(
    *,
    last: datetime | None,
    now: datetime,
    every: timedelta,
) -> bool:
    """
    Say whether a last time is missing, in the future or before a boundary.

    Times are compared as real instants, because datetimes sharing a zone
    compare by wall clock and the repeated fall-back hour would misorder.

    Parameters
    ----------
    last : datetime | None
        When it last happened.
    now : datetime
        The current time.
    every : timedelta
        The period between boundaries.

    Returns
    -------
    bool
        True when it is due again.
    """
    if last is None or last.timestamp() > now.timestamp():
        return True
    return last.timestamp() < boundary(now=now, every=every).timestamp()


def cycle_due(
    *,
    cycle: Cycle,
    now: datetime,
    every: timedelta,
) -> bool:
    """
    Say whether a new cycle should start.

    Parameters
    ----------
    cycle : Cycle
        The cycle last started.
    now : datetime
        The current time.
    every : timedelta
        The period between cycles.

    Returns
    -------
    bool
        True when none has started, the clock stepped back, or a
        boundary has passed since it started.
    """
    return stale(last=cycle.started, now=now, every=every)


def alerts_due(*, cycle: Cycle, now: datetime) -> bool:
    """
    Say whether the alerts should be asked for again.

    Parameters
    ----------
    cycle : Cycle
        The cycle last started.
    now : datetime
        The current time.

    Returns
    -------
    bool
        True on the same rule as ``cycle_due``, every ``ALERT_EVERY``.
    """
    return stale(last=cycle.alerts_at, now=now, every=ALERT_EVERY)


def vehicle_feeds(
    *, state: State, stop_view: StopView
) -> tuple[str, ...]:
    """
    List the positions feeds the screen polls.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    tuple[str, ...]
        None while asleep; the map's positions feeds on a screen that
        draws vehicles; otherwise `stop_feeds`.
    """
    if state.panel.asleep:
        return ()
    if shows_vehicles(state=state):
        return map_feeds(state=state)
    return stop_feeds(state=state, stop_view=stop_view)


def estimate_trips(*, state: State) -> frozenset[str]:
    """
    Collect the trips the board and the open chooser hold.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    frozenset[str]
        The trip IDs and aliases of every departure the board holds, and
        of the chooser's departures while it holds some.
    """
    board = state.board
    held = all_departures(departures=board.departures, legs=board.legs)
    chooser = departures_chooser(state=state)
    extra = () if chooser is None else chooser.departures
    return pin_trips(departures=(*held, *extra))


def fetched_trip_feeds(*, state: State) -> tuple[str, ...]:
    """
    List the trip-updates feeds that are downloaded.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        The trip-updates feeds less those worked out from positions.
    """
    return tuple(
        feed
        for feed in trip_feeds(state=state)
        if state.city.feeds.estimated_from(feed=feed) is None
    )


def estimating(*, state: State) -> dict[str, Estimating]:
    """
    Make the estimating request for each derived feed in use.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    dict[str, Estimating]
        By positions feed, what its fetch also works out.
    """
    plan, asks = state.city.feeds, {}
    for feed in trip_feeds(state=state):
        source = plan.estimated_from(feed=feed)
        if source is not None:
            asks[source] = Estimating(
                feed=feed,
                trip_ids=estimate_trips(state=state),
                stale_after=stale_after(state=state),
            )
    return asks


def positions_due(
    *,
    state: State,
    stop_view: StopView,
    asked: frozenset[str],
    asks: Mapping[str, Estimating],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Choose the positions feeds to fetch now.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.
    asked : frozenset[str]
        Feeds already asked for this cycle.
    asks : Mapping[str, Estimating]
        The estimating requests, by positions feed.

    Returns
    -------
    tuple[tuple[str, ...], tuple[str, ...]]
        The positions feeds: the screen's unasked ones, then the source of
        each derived feed not yet asked, each once; and those derived
        feeds.
    """
    positions = unasked(
        feeds=vehicle_feeds(state=state, stop_view=stop_view), asked=asked,
    )
    derived = tuple(
        ask.feed for ask in asks.values() if ask.feed not in asked
    )
    sources = tuple(
        feed for feed, ask in asks.items() if ask.feed not in asked
    )
    return tuple(dict.fromkeys((*positions, *sources))), derived


def needed_feeds(
    *, state: State, stop_view: StopView
) -> tuple[str, ...]:
    """
    List every trip-updates and positions feed the screen polls.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    tuple[str, ...]
        The fetched trip-updates feeds, then the positions feeds of a
        screen that draws vehicles or of the Stop view of a pin with no
        Destination, then the positions feeds that
        estimates are worked out from, each once.
    """
    return tuple(
        dict.fromkeys(
            (
                *fetched_trip_feeds(state=state),
                *vehicle_feeds(state=state, stop_view=stop_view),
                *estimating(state=state),
            ),
        ),
    )


def alert_asked(*, state: State, feed: str) -> bool:
    """
    Say whether an alert feed was asked for since the alerts last were.

    Parameters
    ----------
    state : State
        State.
    feed : str
        The alert feed.

    Returns
    -------
    bool
        True when the feed was fetched at or after the cycle's
        `alerts_at`.
    """
    since = state.cycle.alerts_at
    at = state.alerts.fetched_at.get(feed)
    if since is None or at is None:
        return False
    return at.timestamp() >= since.timestamp()


def alerts_sent(*, state: State, full: bool) -> tuple[str, ...]:
    """
    Choose the alert feeds to fetch now.

    Parameters
    ----------
    state : State
        State.
    full : bool
        Whether a cycle starts with the alerts due.

    Returns
    -------
    tuple[str, ...]
        Every alert feed when `full`; otherwise only those not asked for
        since the alerts last were.
    """
    return tuple(
        feed
        for feed in alert_feeds(state=state)
        if full or not alert_asked(state=state, feed=feed)
    )


def recorded(
    *,
    state: State,
    trips: tuple[str, ...],
    positions: tuple[str, ...],
    alerts: tuple[str, ...],
) -> State:
    """
    Note when each kind of feed was last fetched.

    Parameters
    ----------
    state : State
        State.
    trips : tuple[str, ...]
        The trip-updates feeds fetched now.
    positions : tuple[str, ...]
        The positions feeds fetched now.
    alerts : tuple[str, ...]
        The alert feeds fetched now.

    Returns
    -------
    State
        State with `last_fetch` set when trip-updates feeds went out, and
        each positions and alert feed's fetch time set to now.
    """
    now = state.now
    map_state = replace(
        state.map,
        fetched_at={**state.map.fetched_at, **dict.fromkeys(positions, now)},
    )
    alert_state = replace(
        state.alerts,
        fetched_at={**state.alerts.fetched_at, **dict.fromkeys(alerts, now)},
    )
    return replace(
        state,
        last_fetch=now if trips else state.last_fetch,
        map=map_state,
        alerts=alert_state,
    )


def joined(
    *,
    state: State,
    due: bool,
    full: bool,
    sent: tuple[str, ...],
    derived: tuple[str, ...] = (),
) -> Cycle:
    """
    Add the feeds fetched now to the cycle, or start a new one with them.

    Parameters
    ----------
    state : State
        State.
    due : bool
        Whether a new cycle starts.
    full : bool
        Whether every alert feed is asked for again.
    sent : tuple[str, ...]
        The feeds fetched now.
    derived : tuple[str, ...]
        The derived feeds asked for through a positions fetch.

    Returns
    -------
    Cycle
        A new cycle started now asking for `sent` and `derived` and
        waiting on `sent`, or the cycle under way with them added. The alerts'
        time moves to now when they all went out, or when none had
        before.
    """
    cycle, now, names = state.cycle, state.now, frozenset(sent)
    every = names | frozenset(derived)
    if full or cycle.alerts_at is None:
        alerts = names & frozenset(alert_feeds(state=state))
        cycle = replace(
            cycle, alerts_at=now if alerts else cycle.alerts_at,
        )
    if due:
        return replace(cycle, started=now, asked=every, waiting=names)
    return replace(
        cycle, asked=cycle.asked | every, waiting=cycle.waiting | names,
    )


def unasked(
    *, feeds: tuple[str, ...], asked: frozenset[str]
) -> tuple[str, ...]:
    """
    Keep the feeds not asked for yet.

    Parameters
    ----------
    feeds : tuple[str, ...]
        Feeds.
    asked : frozenset[str]
        Feeds already asked for.

    Returns
    -------
    tuple[str, ...]
        The feeds not in `asked`, in order.
    """
    return tuple(feed for feed in feeds if feed not in asked)


def cycle_effects(
    *, state: State, stop_view: StopView
) -> tuple[State, tuple[Effect, ...]]:
    """
    Fetch the feeds due: all together on a boundary, new ones at once.

    Alerts join a cycle only every `ALERT_EVERY`, apart from alert feeds
    newly needed.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the cycle and fetch times recorded, and a FetchFeed for
        each feed sent; unchanged while the feeds are held, while the
        clock is awaited, or when no feed is to be fetched.
    """
    if feeds_held(state=state) or clock_waiting(state=state):
        return state, ()
    cycle, now = state.cycle, state.now
    due = cycle_due(cycle=cycle, now=now, every=poll_every(state=state))
    asked = frozenset() if due else cycle.asked
    full = due and alerts_due(cycle=cycle, now=now)
    asks = estimating(state=state)
    trips = unasked(feeds=fetched_trip_feeds(state=state), asked=asked)
    positions, derived = positions_due(
        state=state, stop_view=stop_view, asked=asked, asks=asks,
    )
    alerts = alerts_sent(state=state, full=full)
    if not (trips or positions or alerts):
        return state, ()
    cycle = joined(
        state=state,
        due=due,
        full=full,
        sent=(*trips, *positions, *alerts),
        derived=derived,
    )
    state = recorded(
        state=state, trips=trips, positions=positions, alerts=alerts,
    )
    fetches = fetches_of(
        trips=trips, positions=positions, alerts=alerts, asks=asks,
    )
    return replace(state, cycle=cycle), fetches


def fetches_of(
    *,
    trips: tuple[str, ...],
    positions: tuple[str, ...],
    alerts: tuple[str, ...],
    asks: Mapping[str, Estimating] | None = None,
) -> tuple[Effect, ...]:
    """
    Make a FetchFeed for each feed.

    Parameters
    ----------
    trips : tuple[str, ...]
        The trip-updates feeds.
    positions : tuple[str, ...]
        The positions feeds.
    alerts : tuple[str, ...]
        The alert feeds.
    asks : Mapping[str, Estimating] | None
        The estimating requests, by positions feed.

    Returns
    -------
    tuple[Effect, ...]
        The fetches, in that order; a positions fetch carries its
        estimating request, if any.
    """
    asks = asks or {}
    return (
        *(FetchFeed(feed=feed) for feed in trips),
        *(
            FetchFeed(
                feed=feed,
                kind=FeedKind.POSITIONS,
                estimating=asks.get(feed),
            )
            for feed in positions
        ),
        *(FetchFeed(feed=feed, kind=FeedKind.ALERTS) for feed in alerts),
    )


def answered(*, state: State, feed: str) -> State:
    """
    Note that a feed asked for has answered.

    Parameters
    ----------
    state : State
        State.
    feed : str
        The feed that answered, with data or a failure.

    Returns
    -------
    State
        State whose cycle no longer waits on the feed.
    """
    if feed not in state.cycle.waiting:
        return state
    cycle = replace(state.cycle, waiting=state.cycle.waiting - {feed})
    return replace(state, cycle=cycle)


def cycle_held(*, state: State) -> bool:
    """
    Say whether the cycle's frame waits for its feeds to answer.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True while a feed asked for has not answered, `CYCLE_WAIT` has not
        passed since the cycle started, and no person's action freed the
        frame.
    """
    cycle = state.cycle
    if cycle.free or not cycle.waiting:
        return False
    return not elapsed(since=cycle.started, now=state.now, span=CYCLE_WAIT)


def answered_late(*, state: State, feed: str) -> bool:
    """
    Say whether a feed's answer comes after its cycle stopped waiting.

    Parameters
    ----------
    state : State
        State before the answer is noted.
    feed : str
        The feed that answered.

    Returns
    -------
    bool
        True when the cycle does not wait on the feed, or its frame is no
        longer held, so the answer is drawn by the next frame.
    """
    return feed not in state.cycle.waiting or not cycle_held(state=state)
