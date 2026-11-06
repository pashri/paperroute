"""Tests for polling alert feeds and matching them to the shown pin."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.alert_model import (
    Alert,
    AlertsFetched,
    AlertsSnapshot,
    AlertTie,
    BetweenLoaded,
    Informed,
    LoadBetween,
    Matched,
)
from paperroute.core.alert_state import alert_feeds, listed_ids, shown_alerts
from paperroute.core.feeds import FeedKind
from paperroute.core.map_view import MapView
from paperroute.core.messages import (
    DeparturesLoaded,
    Effect,
    FeedFetched,
    FetchFeed,
    Knob,
    KnobTurned,
    Render,
    Tick,
)
from paperroute.core.model import Departure, Mode, Pin, RouteKind
from paperroute.core.places import shown
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import LiveView, seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import RELOAD_BEFORE, State
from paperroute.core.state_views import current_view, live_stop_view
from paperroute.core.stop_view import DOT, HORIZON
from paperroute.core.strip_model import StripMapView, StripView
from tests.core.alert_factories import (
    ABBOTSFORD,
    F9_ONGOING,
    RHODES,
    ROSE_BAY_CLOSURE,
    ROSE_BAY_PEAK,
    ROSE_BAY_PIN,
    STRATHFIELD_UPDATE,
    WESTERN,
    XPLORER,
    station_pin,
    sydney,
)
from tests.core.connection_factories import LEG1, LEG2, WORK
from tests.core.connection_factories import at as at_connection
from tests.core.connection_factories import work_legs
from tests.core.factories import departure
from tests.core.steps import wakeful
from tests.core.test_quiet import asleep_at_one, at, evening, ticked

NOW: Final[datetime] = sydney(day=24, hour=9, minute=39)
TRAINS: Final[str] = 'v2/gtfs/alerts/sydneytrains'
BUSES: Final[str] = 'v2/gtfs/alerts/buses'
REGIONAL: Final[str] = 'v2/gtfs/alerts/nswtrains'
FERRIES: Final[str] = 'v2/gtfs/alerts/ferries'
PARRAMATTA_CENTRAL: Final[Pin] = station_pin(
    origin='2150',
    origin_name='Parramatta Station',
    destination='200060',
    destination_name='Central Station',
)
STRATHFIELD_TOWN_HALL: Final[Pin] = station_pin(
    origin='213510',
    origin_name='Strathfield Station',
    destination='2000',
    destination_name='Town Hall Station',
)
BUS_PIN: Final[Pin] = station_pin(
    origin='213460',
    origin_name='Rhodes Station, Walker St',
    kind=RouteKind.BUS,
)


def started(*, pins: tuple[Pin, ...] = (PARRAMATTA_CENTRAL,)) -> State:
    """
    An awake state showing the first of some pins, nothing loaded.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        The pins.

    Returns
    -------
    State
        The state at `NOW`.
    """
    saved = replace(seeded(pin=pins[0]), pins=pins)
    return wakeful(state=initial_state(saved=saved, now=NOW, stored=True))


def loaded_with(
    *,
    state: State,
    modes: tuple[Mode, ...] = (Mode.TRAIN,),
    route: str = 'T1',
) -> tuple[State, tuple[Effect, ...]]:
    """
    Load one departure of each mode for the shown pin.

    Parameters
    ----------
    state : State
        State.
    modes : tuple[Mode, ...]
        Each departure's mode.
    route : str
        Their route.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects after the load.
    """
    departures = tuple(
        departure(
            departs=NOW + timedelta(minutes=3 * (index + 1)),
            mode=mode,
            route=route,
            trip_id=f'trip-{index}',
        )
        for index, mode in enumerate(modes)
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=departures,
        until=NOW + HORIZON + RELOAD_BEFORE,
    )
    return step(state, event)


def alert_fetches(*, effects: tuple[Effect, ...]) -> list[FetchFeed]:
    """
    List the alert feed fetches.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[FetchFeed]
        Each FetchFeed of kind ALERTS.
    """
    return [
        effect
        for effect in effects
        if isinstance(effect, FetchFeed) and effect.kind == FeedKind.ALERTS
    ]


def fetched(*, feed: str, alerts: tuple[Alert, ...]) -> AlertsFetched:
    """
    An alert feed's download.

    Parameters
    ----------
    feed : str
        Feed path.
    alerts : tuple[Alert, ...]
        Its alerts.

    Returns
    -------
    AlertsFetched
        The event.
    """
    snapshot = AlertsSnapshot(feed=feed, fetched_at=NOW, alerts=alerts)
    return AlertsFetched(feed=feed, snapshot=snapshot)


def tick_at(
    *, state: State, after: timedelta,
) -> tuple[State, list[FetchFeed]]:
    """
    Tick some time after `NOW`.

    Parameters
    ----------
    state : State
        State.
    after : timedelta
        Time after `NOW`.

    Returns
    -------
    tuple[State, list[FetchFeed]]
        State after and the alert fetches.
    """
    state, effects = step(state, Tick(now=NOW + after))
    return state, alert_fetches(effects=effects)


def test_alerts_polled_for_pin_modes() -> None:
    """
    A train pin fetches the trains alerts on five-minute boundaries.

    Alerts go out with the first poll cycle on or after the boundary,
    which off-peak is two minutes apart.
    """
    wanted = [FetchFeed(feed=TRAINS, kind=FeedKind.ALERTS)]
    state, effects = loaded_with(state=started())
    assert alert_fetches(effects=effects) == wanted
    state, fetches = tick_at(state=state, after=timedelta(seconds=55))
    assert not fetches
    state, fetches = tick_at(state=state, after=timedelta(minutes=1))
    assert fetches == wanted
    state, fetches = tick_at(
        state=state, after=timedelta(minutes=5, seconds=55),
    )
    assert not fetches
    _, fetches = tick_at(state=state, after=timedelta(minutes=7))
    assert fetches == wanted


def test_two_modes_poll_each_feed() -> None:
    """Bus and train departures fetch each alerts feed once a poll."""
    state, effects = loaded_with(
        state=started(), modes=(Mode.BUS, Mode.TRAIN),
    )
    wanted = [
        FetchFeed(feed=BUSES, kind=FeedKind.ALERTS),
        FetchFeed(feed=TRAINS, kind=FeedKind.ALERTS),
    ]
    assert alert_fetches(effects=effects) == wanted
    state, fetches = tick_at(state=state, after=timedelta(seconds=30))
    assert not fetches
    _, fetches = tick_at(state=state, after=timedelta(minutes=1))
    assert fetches == wanted


def test_ferry_pin_polls_ferry_alerts() -> None:
    """A ferry pin fetches the ferry alerts feed."""
    pin = replace(PARRAMATTA_CENTRAL, kind=RouteKind.FERRY)
    _, effects = loaded_with(state=started(pins=(pin,)), modes=(Mode.FERRY,))
    assert alert_fetches(effects=effects) == [
        FetchFeed(feed=FERRIES, kind=FeedKind.ALERTS),
    ]


def test_no_departures_no_alert_polls() -> None:
    """A pin of any kind polls nothing until departures load."""
    pin = replace(PARRAMATTA_CENTRAL, kind=None)
    _, fetches = tick_at(state=started(pins=(pin,)), after=timedelta())
    assert not fetches


def test_no_alert_polls_while_asleep() -> None:
    """Ticks through quiet hours fetch no alerts."""
    state = evening()
    for minutes in range(0, 6 * 60 + 1, 5):
        moment = at(23) + timedelta(minutes=minutes)
        state, effects = step(state, Tick(now=moment))
        assert not alert_fetches(effects=effects)


def test_wake_fetches_alerts_at_once() -> None:
    """A touch at 01:00 while asleep fetches the alerts in that step."""
    state = asleep_at_one()
    state, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=1))
    assert not state.panel.asleep
    assert alert_fetches(effects=effects) == [
        FetchFeed(feed=BUSES, kind=FeedKind.ALERTS),
    ]


def test_alerts_never_wake() -> None:
    """Alerts arriving while asleep neither wake nor redraw."""
    state = asleep_at_one()
    state, effects = step(state, fetched(feed=BUSES, alerts=(RHODES,)))
    assert state.panel.asleep
    assert not [effect for effect in effects if isinstance(effect, Render)]


def test_alerts_fetched_rematched() -> None:
    """A trains download is matched to the shown train pin at once."""
    state, _ = loaded_with(state=started())
    state, _ = step(state, fetched(feed=TRAINS, alerts=(WESTERN,)))
    assert state.alerts.matched == (
        PARRAMATTA_CENTRAL,
        (Matched(alert=WESTERN, tie=AlertTie.ROUTE, names_place=True),),
    )
    assert [
        item.matched.alert for item in shown_alerts(state=state)
    ] == [WESTERN]


def test_pin_flip_uses_new_pin() -> None:
    """After a pin flip nothing is shown until the new departures load."""
    state, _ = loaded_with(state=started(pins=(PARRAMATTA_CENTRAL, BUS_PIN)))
    state, _ = step(state, fetched(feed=BUSES, alerts=(RHODES,)))
    state, _ = step(state, fetched(feed=TRAINS, alerts=(WESTERN,)))
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert shown(state=state) == BUS_PIN
    assert not shown_alerts(state=state)
    state, _ = loaded_with(state=state, modes=(Mode.BUS,), route='526')
    assert [
        item.matched.alert for item in shown_alerts(state=state)
    ] == [RHODES]


def test_between_requested_for_rail_pin() -> None:
    """A rail pin with a To asks once for the stations between."""
    state, effects = loaded_with(state=started(pins=(STRATHFIELD_TOWN_HALL,)))
    asks = [effect for effect in effects if isinstance(effect, LoadBetween)]
    assert asks == [
        LoadBetween(
            pin=STRATHFIELD_TOWN_HALL,
            trip_ids=frozenset({'trip-0'}),
            day=NOW.date(),
        ),
    ]
    _, effects = loaded_with(state=state)
    assert not [e for e in effects if isinstance(e, LoadBetween)]
    alone = replace(STRATHFIELD_TOWN_HALL, destination=None)
    for pin, mode in ((BUS_PIN, Mode.BUS), (alone, Mode.TRAIN)):
        _, effects = loaded_with(state=started(pins=(pin,)), modes=(mode,))
        assert [e for e in effects if isinstance(e, LoadBetween)] == [
            LoadBetween(pin=pin, trip_ids=frozenset(), day=NOW.date()),
        ]


def test_between_asked_again_once_trips_known() -> None:
    """A rail pin asked about before its trips is asked once more."""
    state, effects = loaded_with(
        state=started(pins=(STRATHFIELD_TOWN_HALL,)), modes=(),
    )
    assert [e for e in effects if isinstance(e, LoadBetween)] == [
        LoadBetween(
            pin=STRATHFIELD_TOWN_HALL, trip_ids=frozenset(), day=NOW.date(),
        ),
    ]
    state, effects = loaded_with(state=state, modes=())
    assert not [e for e in effects if isinstance(e, LoadBetween)]
    _, effects = loaded_with(state=state)
    assert [
        e.trip_ids for e in effects if isinstance(e, LoadBetween)
    ] == [frozenset({'trip-0'})]


def test_between_asked_again_on_a_new_service_day() -> None:
    """A pin reloaded past midnight is asked about for the new day."""
    state, _ = loaded_with(
        state=started(pins=(STRATHFIELD_TOWN_HALL,)), modes=(),
    )
    tomorrow = NOW + timedelta(days=1)
    _, effects = loaded_with(state=replace(state, now=tomorrow), modes=())
    assert [e.day for e in effects if isinstance(e, LoadBetween)] == [
        tomorrow.date(),
    ]


def test_between_stored_and_rematched() -> None:
    """Stations between are kept for the pin and used in matching."""
    state, _ = loaded_with(state=started(pins=(STRATHFIELD_TOWN_HALL,)))
    state, _ = step(state, fetched(feed=TRAINS, alerts=(WESTERN,)))
    assert state.alerts.matched is not None
    assert not state.alerts.matched[1][0].names_place
    names = frozenset({'Burwood', 'Redfern', 'Central'})
    event = BetweenLoaded(pin=STRATHFIELD_TOWN_HALL, names=names)
    state, _ = step(state, event)
    assert state.alerts.between == (STRATHFIELD_TOWN_HALL, names)
    assert state.alerts.matched is not None
    assert state.alerts.matched[1][0].names_place


def test_stale_between_dropped() -> None:
    """Stations between for another pin or timetable change nothing."""
    state, _ = loaded_with(state=started(pins=(STRATHFIELD_TOWN_HALL,)))
    names = frozenset({'Burwood'})
    for event in (
        BetweenLoaded(pin=PARRAMATTA_CENTRAL, names=names),
        BetweenLoaded(
            pin=STRATHFIELD_TOWN_HALL, names=names, timetable='other',
        ),
    ):
        assert step(state, event) == (state, ())


def test_asleep_clocks_cleared() -> None:
    """Quiet hours clear the alert clocks so waking polls at once."""
    state = ticked(state=evening(), times=(at(23),))
    assert state.panel.asleep
    assert not state.alerts.fetched_at


def fresh_with(
    *, state: State, alerts: tuple[Alert, ...], feed: str = TRAINS,
) -> State:
    """
    Give a loaded state fresh live data and some alerts.

    Parameters
    ----------
    state : State
        State with departures loaded.
    alerts : tuple[Alert, ...]
        Alerts for the feed.
    feed : str
        The alerts feed.

    Returns
    -------
    State
        The state at `NOW`.
    """
    for name in state.feeds:
        snapshot = RealtimeSnapshot(fetched_at=NOW, trips={})
        state, _ = step(state, FeedFetched(feed=name, snapshot=snapshot))
    state, _ = step(state, fetched(feed=feed, alerts=alerts))
    return state


def test_alert_banner_on_map_and_strip() -> None:
    """The Map and Strip views carry the Stop view's alert banner."""
    state, _ = loaded_with(state=started())
    state = fresh_with(state=state, alerts=(XPLORER,))
    stop = live_stop_view(state=state)
    assert stop.banner == XPLORER.header
    assert stop.warning
    views = {}
    for live in (LiveView.MAP, LiveView.STRIP, LiveView.STRIP_MAP):
        shown_state = replace(state, view=live)
        views[live] = current_view(state=shown_state, stop_view=stop)
    map_view = views[LiveView.MAP]
    strip = views[LiveView.STRIP]
    both = views[LiveView.STRIP_MAP]
    assert isinstance(map_view, MapView)
    assert isinstance(strip, StripView)
    assert isinstance(both, StripMapView)
    for carried in (map_view, strip, both.map):
        assert carried.banner == XPLORER.header
        assert carried.warning
        assert carried.more == 0
        assert not carried.planned


def test_list_only_alert_no_banner() -> None:
    """Route alerts not naming a place and open-ended ones stay listed."""
    pin = STRATHFIELD_TOWN_HALL
    state, _ = loaded_with(state=started(pins=(pin,)))
    state = fresh_with(state=state, alerts=(WESTERN, STRATHFIELD_UPDATE))
    assert len(shown_alerts(state=state)) == 2
    assert live_stop_view(state=state).banner is None


def test_alert_in_two_feeds_listed_once() -> None:
    """An alert in two feeds is listed, counted and passed once."""
    state, _ = loaded_with(
        state=started(), modes=(Mode.TRAIN, Mode.REGIONAL),
    )
    second = replace(XPLORER, alert_id='second', header='Second')
    state = fresh_with(state=state, alerts=(XPLORER, second))
    state, _ = step(state, fetched(feed=REGIONAL, alerts=(XPLORER,)))
    assert listed_ids(state=state) == [XPLORER.alert_id, 'second']
    assert live_stop_view(state=state).more == 1
    state, _ = step(
        replace(state, view=LiveView.ALERTS),
        KnobTurned(knob=Knob.TOP, steps=1),
    )
    assert state.alerts.selected == 'second'


def test_closure_listed_with_no_departures() -> None:
    """With every train replaced, a pin still gets its feeds' alerts."""
    pin = replace(PARRAMATTA_CENTRAL, only=('T1',))
    state, fetches = tick_at(
        state=started(pins=(pin,)), after=timedelta(),
    )
    assert not state.board.departures
    assert fetches == [FetchFeed(feed=TRAINS, kind=FeedKind.ALERTS)]
    state, _ = step(state, fetched(feed=TRAINS, alerts=(WESTERN, XPLORER)))
    assert listed_ids(state=state) == [XPLORER.alert_id, WESTERN.alert_id]


def test_platform_pin_matches_sibling_platform() -> None:
    """A platform pin is tied to an alert on another platform there."""
    pin = station_pin(origin='2150142', origin_name='Parramatta Platform 2')
    state, effects = loaded_with(state=started(pins=(pin,)))
    assert [e for e in effects if isinstance(e, LoadBetween)] == [
        LoadBetween(pin=pin, trip_ids=frozenset(), day=NOW.date()),
    ]
    sibling = replace(
        STRATHFIELD_UPDATE,
        informed=(Informed(stops=frozenset({'2150141', '2150'})),),
    )
    state, _ = step(state, fetched(feed=TRAINS, alerts=(sibling,)))
    assert not listed_ids(state=state)
    families = {'2150142': frozenset({'2150142', '2150'})}
    event = BetweenLoaded(pin=pin, names=frozenset(), families=families)
    state, _ = step(state, event)
    assert listed_ids(state=state) == [sibling.alert_id]


def test_kindless_pin_asked_again_once_trips_load() -> None:
    """A pin of any kind with a To is asked again once trips load."""
    pin = replace(STRATHFIELD_TOWN_HALL, kind=None)
    state, _ = loaded_with(state=started(pins=(pin,)), modes=())
    _, effects = loaded_with(state=state)
    assert [
        e.trip_ids for e in effects if isinstance(e, LoadBetween)
    ] == [frozenset({'trip-0'})]


def test_ferry_wharf_alert_takes_banner() -> None:
    """A wharf closure naming the ferry pin's wharf takes the banner."""
    state, _ = loaded_with(
        state=started(pins=(ROSE_BAY_PIN,)), modes=(Mode.FERRY,), route='F9',
    )
    state = fresh_with(
        state=state,
        alerts=(ROSE_BAY_CLOSURE, ROSE_BAY_PEAK, F9_ONGOING),
        feed=FERRIES,
    )
    listed = listed_ids(state=state)
    assert listed[0] == ROSE_BAY_CLOSURE.alert_id
    assert set(listed) == {
        ROSE_BAY_CLOSURE.alert_id,
        ROSE_BAY_PEAK.alert_id,
        F9_ONGOING.alert_id,
    }
    stop = live_stop_view(state=state)
    assert stop.banner == 'Rose Bay Wharf temporary closure'
    assert stop.warning
    assert stop.more == 1


def test_ferry_alert_naming_other_wharf_listed_only() -> None:
    """A closure of another wharf on the route is only listed."""
    pin = station_pin(
        origin='21501', origin_name='Parramatta Wharf', kind=RouteKind.FERRY,
    )
    state, _ = loaded_with(
        state=started(pins=(pin,)), modes=(Mode.FERRY,), route='F3',
    )
    state = fresh_with(state=state, alerts=(ABBOTSFORD,), feed=FERRIES)
    assert listed_ids(state=state) == [ABBOTSFORD.alert_id]
    assert live_stop_view(state=state).banner == (
        f'Timetable times{DOT}no live feed for this service'
    )


def trainlink_fetches(
    *, pin: Pin, mode: Mode, trainlink: bool,
) -> list[FetchFeed]:
    """
    Load a pin's departures and TrainLink answer, then tick to the
    next alerts boundary.

    Parameters
    ----------
    pin : Pin
        The shown pin.
    mode : Mode
        Its departures' mode.
    trainlink : bool
        The timetable's TrainLink answer.

    Returns
    -------
    list[FetchFeed]
        The alert fetches of that tick.
    """
    state, _ = loaded_with(state=started(pins=(pin,)), modes=(mode,))
    event = BetweenLoaded(pin=pin, names=frozenset(), trainlink=trainlink)
    state, _ = step(state, event)
    _, fetches = tick_at(state=state, after=timedelta(minutes=1))
    return fetches


def test_trainlink_answer_fetches_its_feed_at_once() -> None:
    """A TrainLink feed newly needed is fetched at once, and only it."""
    pin = PARRAMATTA_CENTRAL
    state, _ = loaded_with(state=started(pins=(pin,)), modes=(Mode.TRAIN,))
    event = BetweenLoaded(pin=pin, names=frozenset(), trainlink=True)
    state, effects = step(state, event)
    assert alert_fetches(effects=effects) == [
        FetchFeed(feed=REGIONAL, kind=FeedKind.ALERTS),
    ]
    _, fetches = tick_at(state=state, after=timedelta(seconds=30))
    assert not fetches


def test_trainlink_station_polls_trainlink() -> None:
    """A train pin TrainLink serves also polls the TrainLink feed."""
    assert trainlink_fetches(
        pin=PARRAMATTA_CENTRAL, mode=Mode.TRAIN, trainlink=True,
    ) == [
        FetchFeed(feed=REGIONAL, kind=FeedKind.ALERTS),
        FetchFeed(feed=TRAINS, kind=FeedKind.ALERTS),
    ]
    assert trainlink_fetches(
        pin=PARRAMATTA_CENTRAL, mode=Mode.TRAIN, trainlink=False,
    ) == [FetchFeed(feed=TRAINS, kind=FeedKind.ALERTS)]


def test_trainlink_answer_for_other_pin_ignored() -> None:
    """A TrainLink answer for another pin adds no feed."""
    pins = (PARRAMATTA_CENTRAL, STRATHFIELD_TOWN_HALL)
    state, _ = loaded_with(state=started(pins=pins))
    event = BetweenLoaded(
        pin=PARRAMATTA_CENTRAL, names=frozenset(), trainlink=True,
    )
    state, _ = step(state, event)
    state, _ = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert shown(state=state) == STRATHFIELD_TOWN_HALL
    state, _ = loaded_with(state=state)
    assert alert_feeds(state=state) == (TRAINS,)


def test_kindless_train_pin_polls_trainlink() -> None:
    """A kind-less pin with train departures polls TrainLink too."""
    assert trainlink_fetches(
        pin=replace(PARRAMATTA_CENTRAL, kind=None),
        mode=Mode.TRAIN,
        trainlink=True,
    ) == [
        FetchFeed(feed=REGIONAL, kind=FeedKind.ALERTS),
        FetchFeed(feed=TRAINS, kind=FeedKind.ALERTS),
    ]


def test_bus_pin_ignores_trainlink() -> None:
    """A bus pin never polls TrainLink from the timetable's answer."""
    assert trainlink_fetches(
        pin=BUS_PIN, mode=Mode.BUS, trainlink=True,
    ) == [FetchFeed(feed=BUSES, kind=FeedKind.ALERTS)]


def connection_asks(*, second: tuple[Departure, ...]) -> list[LoadBetween]:
    """
    Load WORK with some leg-2 departures and give what it asks.

    Parameters
    ----------
    second : tuple[Departure, ...]
        Leg-2 departures.

    Returns
    -------
    list[LoadBetween]
        The stations-between requests made.
    """
    now = at_connection(7, 30)
    state = wakeful(state=initial_state(
        saved=seeded(pin=WORK), now=now, stored=True,
    ))
    _, effects = step(state, DeparturesLoaded(
        pin=WORK,
        departures=(),
        until=now + timedelta(hours=3),
        legs=work_legs(second=second),
    ))
    return [effect for effect in effects if isinstance(effect, LoadBetween)]


def test_connection_asks_between_per_leg() -> None:
    """A bus connection asks no trips; a ferry leg 2 asks its own."""
    assert connection_asks(second=LEG2) == [
        LoadBetween(pin=WORK, trip_ids=frozenset(), day=LEG1[0].service_day),
    ]
    ferry = (
        departure(
            departs=at_connection(7, 50),
            mode=Mode.FERRY,
            route='F3',
            trip_id='ferry-1',
        ),
    )
    assert [ask.trip_ids for ask in connection_asks(second=ferry)] == [
        frozenset({'ferry-1'}),
    ]
