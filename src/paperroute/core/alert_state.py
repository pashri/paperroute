"""Polling the shown pin's alert feeds and tying their alerts to it."""

from dataclasses import replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Final

from paperroute.city import SydneyFeed
from paperroute.core.alert_match import Listed, listed_alerts, matched_alerts
from paperroute.core.chains import all_departures
from paperroute.core.connection import between_lists, board_facts, name_trips
from paperroute.core.feeds import KIND_MODES, FeedKind, alert_feeds_for
from paperroute.core.messages import Effect, FetchFeed
from paperroute.core.places import shown
from paperroute.core.saved import LOOKAHEAD, LiveView
from paperroute.core.state_model import State, elapsed
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobPressed,
    KnobTurned,
)
from paperroute_transit.core.alert_model import (
    Alert,
    AlertsFetched,
    BetweenLoaded,
    LoadBetween,
)
from paperroute_transit.core.model import Pin, same_place

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
        Feed paths of the pin's kind's modes and the modes of every leg's
        loaded departures, sorted, with NSW TrainLink's for a train pin
        it serves.
    """
    pin = shown(state=state)
    kind_modes = frozenset() if pin.kind is None else KIND_MODES[pin.kind]
    loaded = all_departures(
        departures=state.board.departures, legs=state.board.legs,
    )
    modes = {*kind_modes, *(item.source for item in loaded)}
    if SydneyFeed.TRAIN in modes and trainlink_known(state=state, pin=pin):
        modes.add(SydneyFeed.REGIONAL)
    return alert_feeds_for(modes=modes)


def trainlink_known(*, state: State, pin: Pin) -> bool:
    """
    Say whether the timetable said NSW TrainLink serves a pin.

    Parameters
    ----------
    state : State
        State.
    pin : Pin
        The pin.

    Returns
    -------
    bool
        True when the stored answer is for the pin's place and is yes.
    """
    between = state.alerts.between
    return (
        between is not None
        and same_place(first=between[0], second=pin)
        and state.alerts.agency_alerts
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
    Ask once per pin, timetable and day for its stop families and stations.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State noting the question, and a LoadBetween. Its trips are the
        first `BETWEEN_TRIPS` departures' of each leg with a To matched by
        name (a plain pin is its own one leg), else none. A pin asked
        about before its trips were known is asked again once they are,
        as is a pin with a kind-less leg that has a To, whose need for
        them shows only then; otherwise nothing is asked twice.
    """
    pin = shown(state=state)
    lists = between_lists(pin=pin, board=state.board)
    departures = [item for _, items in lists for item in items]
    wants, trip_ids = name_trips(
        pin=pin, board=state.board, city=state.city,
    )
    unsure = any(
        leg.kind is None and leg.destination is not None for leg, _ in lists
    )
    known = bool(departures) or not (wants or unsure)
    day = departures[0].service_day if departures else service_date(
        moment=state.now, zone=state.city.zone,
    )
    if was_asked(state=state, pin=pin, known=known, day=day):
        return state, ()
    load = LoadBetween(
        pin=pin,
        trip_ids=trip_ids,
        day=day,
        timetable=state.saving.timetable,
    )
    asked = (pin, state.saving.timetable, known, day)
    return replace(state, alerts=replace(state.alerts, asked=asked)), (load,)


def service_date(*, moment: datetime, zone: tzinfo) -> date:
    """
    The local calendar date of a moment.

    Parameters
    ----------
    moment : datetime
        The moment.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    date
        Its local date.
    """
    return moment.astimezone(zone).date()


def was_asked(*, state: State, pin: Pin, known: bool, day: date) -> bool:
    """
    Say whether a pin was asked about of this timetable already.

    Parameters
    ----------
    state : State
        State.
    pin : Pin
        The pin.
    known : bool
        True when its trips are known now, or not needed.
    day : date
        The service day the lines are wanted for.

    Returns
    -------
    bool
        True when it was, and asking now would add nothing.
    """
    asked = state.alerts.asked
    return (
        asked is not None
        and asked[1] == state.saving.timetable
        and asked[3] == day
        and same_place(first=asked[0], second=pin)
        and (asked[2] or not known)
    )


def feed_alerts(*, state: State) -> tuple[Alert, ...]:
    """
    The alerts of the shown pin's feeds, one per alert ID.

    Feeds share alerts, so the first copy of each ID is kept.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Alert, ...]
        The alerts, by feed then feed order.
    """
    snapshots = state.alerts.snapshots
    unique = {
        alert.alert_id: alert
        for feed in reversed(alert_feeds(state=state))
        if feed in snapshots
        for alert in reversed(snapshots[feed].alerts)
    }
    return tuple(reversed(unique.values()))


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
        State with the ties stored for the shown pin; with no departures
        loaded, alerts are tied by stop and route only. A connection's
        legs also tie by every line the timetable runs along them.
    """
    pin = shown(state=state)
    alerts = state.alerts
    between = alerts.between
    known = between is not None and same_place(first=between[0], second=pin)
    facts = board_facts(
        pin=pin,
        board=state.board,
        between=between[1] if between is not None and known else frozenset(),
        families=alerts.families if known else {},
        city=state.city,
    )
    if known:
        facts = replace(facts, lines=facts.lines | alerts.lines)
    found = matched_alerts(alerts=feed_alerts(state=state), facts=facts)
    return replace(state, alerts=replace(alerts, matched=(pin, found)))


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
        alerts = replace(
            alerts,
            between=(event.pin, event.names),
            families=event.families,
            agency_alerts=event.agency_alerts,
            lines=event.lines,
        )
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
        lookahead=LOOKAHEAD[state.settings.board.alert_lookahead],
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


def has_alerts(*, state: State) -> bool:
    """
    Say whether the shown pin has alerts listed.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True when at least one is listed.
    """
    return bool(shown_alerts(state=state))


def listed_ids(*, state: State) -> list[str]:
    """
    The IDs of the shown pin's listed alerts, in order.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    list[str]
        Alert IDs.
    """
    return [item.matched.alert.alert_id for item in shown_alerts(state=state)]


def selection_moved(*, state: State, steps: int) -> State:
    """
    Move the selection through the listed alerts, clamped.

    Parameters
    ----------
    state : State
        State.
    steps : int
        Detents; clockwise (positive) moves down the list.

    Returns
    -------
    State
        State with the new selection on page 0.
    """
    ids = listed_ids(state=state)
    if not ids:
        return state
    selected = state.alerts.selected
    index = ids.index(selected) if selected in ids else 0
    index = min(max(index + steps, 0), len(ids) - 1)
    alerts = replace(state.alerts, selected=ids[index], page=0)
    return replace(state, alerts=alerts)


def alerts_knob(*, state: State, event: KnobEvent) -> State | None:
    """
    Apply a knob event on the Alerts view.

    The top turn selects an alert and the top press pages its text; the
    bottom turn does nothing.

    Parameters
    ----------
    state : State
        State.
    event : KnobEvent
        Knob event.

    Returns
    -------
    State | None
        New state, the same state for a bottom turn, or None when the
        event is handled as on the other Live views, the Alerts view
        isn't shown or the chooser is open.
    """
    if state.view is not LiveView.ALERTS or state.chooser is not None:
        return None
    match event:
        case KnobTurned(knob=Knob.TOP, steps=steps):
            return selection_moved(state=state, steps=steps)
        case KnobPressed(knob=Knob.TOP):
            page = state.alerts.page + 1
            return replace(state, alerts=replace(state.alerts, page=page))
        case KnobTurned(knob=Knob.BOTTOM):
            return state
    return None


def alerts_left(*, state: State) -> State:
    """
    Leave the Alerts view when its list empties, and keep the selection.

    Parameters
    ----------
    state : State
        State after an event.

    Returns
    -------
    State
        On the Alerts view with nothing listed, the Stop view. Otherwise
        a selection no longer listed becomes the first listed alert, on
        page 0; with none selected yet, the first is, on its page.
    """
    ids = listed_ids(state=state)
    if not ids:
        if state.view is LiveView.ALERTS:
            return replace(state, view=LiveView.STOP)
        return state
    return selection_kept(state=state, ids=ids)


def selection_kept(*, state: State, ids: list[str]) -> State:
    """
    Select the first listed alert when the selection isn't listed.

    Parameters
    ----------
    state : State
        State.
    ids : list[str]
        Listed alert IDs; not empty.

    Returns
    -------
    State
        State with a listed selection.
    """
    selected = state.alerts.selected
    if selected in ids:
        return state
    page = state.alerts.page if selected is None else 0
    alerts = replace(state.alerts, selected=ids[0], page=page)
    return replace(state, alerts=alerts)
