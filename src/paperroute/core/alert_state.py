"""Polling the shown pin's alert feeds and tying their alerts to it."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.alert_match import (
    BETWEEN_TRIPS,
    Listed,
    is_name_pin,
    listed_alerts,
    matched_alerts,
    pin_facts,
)
from paperroute.core.alert_model import (
    AlertsFetched,
    BetweenLoaded,
    LoadBetween,
)
from paperroute.core.feeds import FeedKind, alert_feeds_for
from paperroute.core.messages import Effect, FetchFeed
from paperroute.core.model import Pin, same_place
from paperroute.core.places import shown
from paperroute.core.saved import LOOKAHEAD
from paperroute.core.state_model import State, elapsed

ALERT_EVERY: Final[timedelta] = timedelta(minutes=5)


def alert_feeds(*, state: State) -> tuple[str, ...]:
    """
    List the alert feeds of the shown pin's modes.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        Feed paths of the loaded departures' modes, sorted; empty before
        departures load.
    """
    return alert_feeds_for(
        modes=(item.mode for item in state.board.departures),
    )


def alert_polls(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Fetch each alert feed whose clock has run out.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the due feeds' clocks set to now, and a FetchFeed for
        each; a feed never fetched is due at once.
    """
    alerts = state.alerts
    due = tuple(
        feed
        for feed in alert_feeds(state=state)
        if elapsed(
            since=alerts.fetched_at.get(feed),
            now=state.now,
            span=ALERT_EVERY,
        )
    )
    if not due:
        return state, ()
    fetched_at = {**alerts.fetched_at, **dict.fromkeys(due, state.now)}
    state = replace(state, alerts=replace(alerts, fetched_at=fetched_at))
    return state, tuple(
        FetchFeed(feed=feed, kind=FeedKind.ALERTS) for feed in due
    )


def between_requests(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask once per pin and timetable for the stations between From and To.

    Parameters
    ----------
    state : State
        State with the shown pin's departures loaded.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State noting the question, and a LoadBetween for the first
        `BETWEEN_TRIPS` departures' trips; unchanged for a pin with no
        To, one not matched by name, or one already asked about.
    """
    pin = shown(state=state)
    departures = state.board.departures[:BETWEEN_TRIPS]
    timetable = state.saving.timetable
    if (
        pin.destination is None
        or not departures
        or not is_name_pin(pin=pin, departures=departures)
        or was_asked(state=state, pin=pin)
    ):
        return state, ()
    load = LoadBetween(
        pin=pin,
        trip_ids=frozenset(item.trip_id for item in departures),
        day=departures[0].service_day,
        timetable=timetable,
    )
    alerts = replace(state.alerts, asked=(pin, timetable))
    return replace(state, alerts=alerts), (load,)


def was_asked(*, state: State, pin: Pin) -> bool:
    """
    Say whether a pin's stations between were asked of this timetable.

    Parameters
    ----------
    state : State
        State.
    pin : Pin
        The pin.

    Returns
    -------
    bool
        True when they were.
    """
    asked = state.alerts.asked
    return (
        asked is not None
        and asked[1] == state.saving.timetable
        and same_place(first=asked[0], second=pin)
    )


def rematched(*, state: State) -> State:
    """
    Tie the shown pin's feeds' alerts to it.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with the ties stored for the shown pin; none are stored
        before its departures load.
    """
    pin = shown(state=state)
    departures = state.board.departures
    if not departures:
        return replace(state, alerts=replace(state.alerts, matched=None))
    between = state.alerts.between
    names = frozenset[str]()
    if between is not None and same_place(first=between[0], second=pin):
        names = between[1]
    facts = pin_facts(pin=pin, departures=departures, between=names)
    snapshots = state.alerts.snapshots
    alerts = (
        alert
        for feed in alert_feeds(state=state)
        if feed in snapshots
        for alert in snapshots[feed].alerts
    )
    found = matched_alerts(alerts=alerts, facts=facts)
    return replace(state, alerts=replace(state.alerts, matched=(pin, found)))


def on_alerts(*, state: State, event: AlertsFetched | BetweenLoaded) -> State:
    """
    Store an alert download or the stations between, then rematch.

    Parameters
    ----------
    state : State
        State.
    event : AlertsFetched | BetweenLoaded
        What arrived.

    Returns
    -------
    State
        State with it stored and the ties renewed.
    """
    alerts = state.alerts
    if isinstance(event, AlertsFetched):
        snapshots = {**alerts.snapshots, event.feed: event.snapshot}
        alerts = replace(alerts, snapshots=snapshots)
    else:
        alerts = replace(alerts, between=(event.pin, event.names))
    return rematched(state=replace(state, alerts=alerts))


def shown_alerts(*, state: State) -> tuple[Listed, ...]:
    """
    The shown pin's alerts to list, ranked.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Listed, ...]
        The listed alerts; empty when the stored ties are for another
        place.
    """
    matched = state.alerts.matched
    if matched is None or not same_place(
        first=matched[0], second=shown(state=state),
    ):
        return ()
    return listed_alerts(
        matched=matched[1],
        now=state.now,
        lookahead=LOOKAHEAD[state.settings.alert_lookahead],
    )


def alert_clocks_cleared(*, state: State) -> State:
    """
    Clear the alert feeds' clocks, so each is due at once.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with no alert fetch times.
    """
    return replace(state, alerts=replace(state.alerts, fetched_at={}))
