"""Polling the shown pin's alert feeds and tying their alerts to it."""

from dataclasses import replace
from datetime import date, datetime, timedelta
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
    Alert,
    AlertsFetched,
    BetweenLoaded,
    LoadBetween,
)
from paperroute.core.feeds import KIND_MODES, FeedKind, alert_feeds_for
from paperroute.core.messages import (
    Effect,
    FetchFeed,
    Knob,
    KnobEvent,
    KnobPressed,
    KnobTurned,
)
from paperroute.core.model import SYDNEY, Mode, Pin, same_place
from paperroute.core.places import shown
from paperroute.core.saved import LOOKAHEAD, LiveView
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
        Feed paths of the pin's kind's modes and its loaded departures'
        modes, sorted, with NSW TrainLink's for a train pin it serves.
    """
    pin = shown(state=state)
    kind_modes = frozenset() if pin.kind is None else KIND_MODES[pin.kind]
    modes = {*kind_modes, *(item.mode for item in state.board.departures)}
    if Mode.TRAIN in modes and trainlink_known(state=state, pin=pin):
        modes.add(Mode.REGIONAL)
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
        and state.alerts.trainlink
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
    Ask once per pin and timetable for its stop families and stations.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State noting the question, and a LoadBetween. Its trips are the
        first `BETWEEN_TRIPS` departures' for a pin with a To matched by
        name, else none. A pin asked about before its trips were known
        is asked again once they are, as is a kind-less pin with a To,
        whose need for them shows only then; otherwise nothing is asked
        twice.
    """
    pin = shown(state=state)
    departures = state.board.departures[:BETWEEN_TRIPS]
    wants = pin.destination is not None and is_name_pin(
        pin=pin, departures=departures,
    )
    unsure = pin.kind is None and pin.destination is not None
    known = bool(departures) or not (wants or unsure)
    if was_asked(state=state, pin=pin, known=known):
        return state, ()
    load = LoadBetween(
        pin=pin,
        trip_ids=frozenset(item.trip_id for item in departures if wants),
        day=departures[0].service_day if departures else service_date(
            moment=state.now,
        ),
        timetable=state.saving.timetable,
    )
    asked = (pin, state.saving.timetable, known)
    return replace(state, alerts=replace(state.alerts, asked=asked)), (load,)


def service_date(*, moment: datetime) -> date:
    """
    The Sydney calendar date of a moment.

    Parameters
    ----------
    moment : datetime
        The moment.

    Returns
    -------
    date
        Its date in Sydney.
    """
    return moment.astimezone(SYDNEY).date()


def was_asked(*, state: State, pin: Pin, known: bool) -> bool:
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

    Returns
    -------
    bool
        True when it was, and asking now would add nothing.
    """
    asked = state.alerts.asked
    return (
        asked is not None
        and asked[1] == state.saving.timetable
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
        loaded, alerts are tied by stop and route only.
    """
    pin = shown(state=state)
    alerts = state.alerts
    between = alerts.between
    known = between is not None and same_place(first=between[0], second=pin)
    facts = pin_facts(
        pin=pin,
        departures=state.board.departures,
        between=between[1] if between is not None and known else frozenset(),
        families=alerts.families if known else {},
    )
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
            trainlink=event.trainlink,
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
