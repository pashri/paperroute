"""Tests for a connection pin's loaded legs, pairs and alert facts."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute_device.core.kit_events import Tick
from paperroute_sydney.city import SYDNEY, SydneyFeed
from paperroute_sydney.sydney_feeds import ALERT_FEEDS
from paperroute_testkit.core.connection_factories import (
    CAMPBELLTOWN_WAY,
    LEG1,
    LEG2,
    PARRAMATTA,
    TWO_WAY,
    WORK,
    WORK_CHANGE,
    at,
    work_legs,
)
from paperroute_testkit.core.factories import departure, place
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import wakeful
from paperroute_transit.core import connection
from paperroute_transit.core.alert_match import tie_of
from paperroute_transit.core.alert_model import (
    Alert,
    AlertsFetched,
    AlertsSnapshot,
    AlertTie,
    BetweenLoaded,
    Informed,
)
from paperroute_transit.core.chains import Chain, Missed, Offers, chains_of
from paperroute_transit.core.connection import (
    chain_legs,
    connection_facts,
    live_chains,
    with_offers,
)
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    FeedFailed,
    FeedFetched,
    SettingsChanged,
    TimetableSeen,
)
from paperroute_transit.core.model import Change, Departure, Pin, Way
from paperroute_transit.core.places import with_place
from paperroute_transit.core.realtime import EVERY_STOP, RealtimeSnapshot
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import seeded
from paperroute_transit.core.state_model import Board, State
from tests.core.alert_factories import alert
from tests.core.chains.helpers import arrival_clocks
from tests.helpers import delayed_trip

FERRY_CHANGE: Final[Change] = replace(
    WORK_CHANGE, board=place('10102027', name='Parramatta Wharf'),
)
FERRY_WORK: Final[Pin] = replace(WORK, ways=(Way(changes=(FERRY_CHANGE,)),))
FERRY_LEG: Final[tuple[Departure, ...]] = (
    departure(
        departs=at(7, 50),
        route='F3',
        mode=SydneyFeed.FERRY,
        trip_id='ferry-1',
    ),
)


def loaded(
    *,
    pin: Pin = WORK,
    first: tuple[Departure, ...] = LEG1,
    second: tuple[Departure, ...] = LEG2,
) -> State:
    """
    Show a connection pin at 7:30 and load its legs.

    Parameters
    ----------
    pin : Pin
        The pin shown.
    first : tuple[Departure, ...]
        Leg-1 departures.
    second : tuple[Departure, ...]
        Leg-2 departures.

    Returns
    -------
    State
        State after the load.
    """
    state = wakeful(state=initial_state(
        saved=seeded(pin=pin, city=SYDNEY), now=at(7, 30),
        city=SYDNEY,
    ))
    state, _ = step(state, DeparturesLoaded(
        pin=pin,
        departures=(),
        until=at(10, 30),
        legs=work_legs(first=first, second=second),
    ))
    return state


def board_of(
    *,
    pin: Pin = WORK,
    departures: tuple[Departure, ...] = (),
    second: tuple[Departure, ...] = LEG2,
    direct: tuple[Departure, ...] = (),
) -> Board:
    """
    Load a one-change pin's legs onto a board.

    Parameters
    ----------
    pin : Pin
        A pin with one one-change way.
    departures : tuple[Departure, ...]
        Leg-1 departures.
    second : tuple[Departure, ...]
        Leg-2 departures.
    direct : tuple[Departure, ...]
        Direct departures.

    Returns
    -------
    Board
        The board.
    """
    return Board(
        departures=direct,
        loaded_until=at(10, 30),
        legs=work_legs(pin=pin, first=departures, second=second),
    )


def test_feeds_union_of_legs() -> None:
    """Both legs' trip feeds are polled."""
    trains = tuple(replace(item, source=SydneyFeed.TRAIN) for item in LEG2)
    state = loaded(second=trains)
    assert set(state.feeds) == {
        'v1/gtfs/realtime/buses',
        'v2/gtfs/realtime/sydneytrains',
    }


def test_live_chains_from_board() -> None:
    """The loaded legs chain as the real times do."""
    state = loaded()
    chains = live_chains(state=state)
    assert arrival_clocks(chains=chains) == [
        '7:57', '8:12', '8:27', '8:45', '9:04',
    ]
    assert [len(chain_legs(chain=chain)) for chain in chains] == [2] * 5
    plain = replace(WORK, ways=())
    assert not live_chains(state=loaded(pin=plain))


def test_direct_list_competes() -> None:
    """A direct trip in the board's own list is a one-leg chain."""
    direct = (
        departure(departs=at(7, 40), ride=10, route='609', trip_id='d1'),
    )
    state = wakeful(state=initial_state(
        saved=seeded(pin=WORK, city=SYDNEY), now=at(7, 30),
        city=SYDNEY,
    ))
    state, _ = step(state, DeparturesLoaded(
        pin=WORK, departures=direct, until=at(10, 30), legs=work_legs(),
    ))
    alone = [
        chain for chain in live_chains(state=state) if chain.way is None
    ]
    assert [chain_legs(chain=chain) for chain in alone] == [direct]


def test_connection_alert_matches_both_legs() -> None:
    """Alerts on the change stop, the From or a leg-2 route match."""
    facts = connection_facts(
        pin=WORK,
        board=board_of(departures=LEG1, second=LEG2),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    change = alert(
        alert_id='change', header='x', stops=frozenset({'2145648'}),
    )
    origin = alert(
        alert_id='from', header='x', stops=frozenset({'G2145615'}),
    )
    route = alert(alert_id='route', header='x', route='665')
    assert tie_of(alert=change, facts=facts) is AlertTie.STOP
    assert tie_of(alert=origin, facts=facts) is AlertTie.STOP
    assert tie_of(alert=route, facts=facts) is AlertTie.ROUTE


def test_connection_names_per_leg() -> None:
    """Names come from a leg whose mode is matched by name, only."""
    ferry = connection_facts(
        pin=FERRY_WORK,
        board=board_of(pin=FERRY_WORK, departures=LEG1, second=FERRY_LEG),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    assert {'Parramatta', 'Westmead'} <= ferry.names
    assert ferry.wharf
    bus = connection_facts(
        pin=WORK,
        board=board_of(departures=LEG1, second=LEG2),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    assert not bus.names


def test_direct_trips_matched() -> None:
    """The direct list's trips and routes are matched too."""
    direct = (departure(departs=at(8, 0), route='609', trip_id='d1'),)
    facts = connection_facts(
        pin=WORK,
        board=board_of(departures=(), direct=direct),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    assert 'd1' in facts.trips
    assert '609' in facts.routes


def test_open_leg_alert_by_loaded_route() -> None:
    """A route alert matches only once that route is in a loaded leg."""
    route = alert(alert_id='route', header='x', route='664')
    without = tuple(item for item in LEG2 if item.route != '664')
    before = connection_facts(
        pin=WORK,
        board=board_of(departures=LEG1, second=without),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    after = connection_facts(
        pin=WORK,
        board=board_of(departures=LEG1, second=LEG2),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    assert tie_of(alert=route, facts=before) is None
    assert tie_of(alert=route, facts=after) is AlertTie.ROUTE


def test_second_change_stop_alert() -> None:
    """An alert on a second change's stop matches a two-change pin."""
    pin = replace(WORK, destination=PARRAMATTA, ways=(TWO_WAY,))
    facts = connection_facts(
        pin=pin, board=Board(), between=frozenset(), families={},
        city=SYDNEY,
    )
    found = alert(alert_id='change', header='x', stops=frozenset({'214510'}))
    assert tie_of(alert=found, facts=facts) is AlertTie.STOP


def test_every_ticked_way_matched() -> None:
    """The legs of a second ticked way are matched too."""
    pin = replace(WORK, ways=(*WORK.ways, CAMPBELLTOWN_WAY))
    facts = connection_facts(
        pin=pin, board=Board(), between=frozenset(), families={},
        city=SYDNEY,
    )
    assert '2560' in facts.stops


def test_connection_facts_merge_lines_and_loose() -> None:
    """Every leg's route IDs are merged, and any loose leg makes it loose."""
    first = tuple(
        replace(item, facts=replace(item.facts, route_id='bundle:606'))
        for item in LEG1
    )
    second = tuple(
        replace(
            item,
            source=SydneyFeed.REGIONAL,
            facts=replace(item.facts, route_id='sydneytrains:WST_2c'),
        )
        for item in LEG2
    )
    facts = connection_facts(
        pin=WORK,
        board=board_of(departures=first, second=second),
        between=frozenset(),
        families={},
        city=SYDNEY,
    )
    assert facts.lines == {'bundle:606', 'sydneytrains:WST_2c'}
    assert facts.loose


def test_leg_lines_from_the_timetable_tie_alerts() -> None:
    """A line the timetable says serves a leg ties its alerts."""
    state = loaded()
    on_line = Alert(
        alert_id='line',
        header='x',
        description='',
        periods=(),
        informed=(Informed(route='T1', line='sydneytrains:WST_2c'),),
    )
    feed = ALERT_FEEDS[SydneyFeed.BUS]
    snapshot = AlertsSnapshot(
        feed=feed, fetched_at=at(7, 30), alerts=(on_line,),
    )
    state, _ = step(state, AlertsFetched(feed=feed, snapshot=snapshot))
    assert state.alerts.matched is not None
    assert not state.alerts.matched[1]
    lines = frozenset({'sydneytrains:WST_2c'})
    state, _ = step(
        state, BetweenLoaded(pin=WORK, names=frozenset(), lines=lines),
    )
    assert state.alerts.matched is not None
    assert [item.tie for item in state.alerts.matched[1]] == [AlertTie.ROUTE]


def late_legs(*, state: State, lates: Mapping[str, int]) -> State:
    """
    Answer every feed with some trips running late.

    Parameters
    ----------
    state : State
        A state with legs loaded.
    lates : Mapping[str, int]
        Minutes late, by trip ID.

    Returns
    -------
    State
        The state after each feed delivers a snapshot fetched now.
    """
    trips = {
        trip_id: delayed_trip(
            trip_id=trip_id, sequence=EVERY_STOP, seconds=60 * late,
        )
        for trip_id, late in lates.items()
    }
    snapshot = RealtimeSnapshot(fetched_at=state.now, trips=trips)
    for feed in state.feeds:
        state, _ = step(state, FeedFetched(feed=feed, snapshot=snapshot))
    return state


def answered() -> State:
    """Work at 7:30 with its legs loaded and its feed answered on time."""
    return late_legs(state=loaded(), lates={})


def first_trips(state: State) -> list[str]:
    """The trip IDs of the first chain listed."""
    return [
        item.trip_id for item in chain_legs(chain=live_chains(state=state)[0])
    ]


def test_offers_wait_for_the_feeds_to_answer() -> None:
    """Offers wait for the feeds to answer."""
    assert loaded().board.offers.kept == {}
    offers = answered().board.offers
    assert offers.pin == WORK and len(offers.kept) == 5


def test_a_remembered_change_is_held_below_its_allowance() -> None:
    """A remembered change is held below its allowance."""
    state = late_legs(state=answered(), lates={'a0733': 4})
    assert first_trips(state) == ['a0733', 'b0746']
    assert live_chains(state=state)[0].missed is None


def test_a_remembered_change_is_missed_when_the_bus_runs_later() -> None:
    """A remembered change is missed when the bus runs later."""
    state = late_legs(state=answered(), lates={'a0733': 6})
    assert first_trips(state) == ['a0733', 'b0801a']
    assert live_chains(state=state)[0].missed == Missed(
        leg=1, arrives=at(7, 57), later=True,
    )


def test_a_board_that_first_hears_late_offers_what_works() -> None:
    """Live data before any offer: the late 7:33 takes the 664, no miss."""
    state = late_legs(state=loaded(first=LEG1[:1]), lates={'a0733': 6})
    assert first_trips(state) == ['a0733', 'b0801a']
    assert live_chains(state=state)[0].missed is None


def test_offers_survive_a_reload() -> None:
    """Offers survive a reload."""
    state = late_legs(state=answered(), lates={'a0733': 4})
    state, _ = step(state, DeparturesLoaded(
        pin=WORK, departures=(), until=at(10, 30), legs=work_legs(),
    ))
    assert first_trips(state) == ['a0733', 'b0746']


def test_offers_are_forgotten_when_the_timetable_is_rebuilt() -> None:
    """Renamed onward trips are no change missed, only a new timetable."""
    state = late_legs(state=answered(), lates={})
    state, _ = step(state, TimetableSeen(version='new'))
    renamed = tuple(
        replace(item, trip_id=f'{item.trip_id}v2') for item in LEG2
    )
    state, _ = step(state, DeparturesLoaded(
        pin=WORK, departures=(), until=at(10, 30), timetable='new',
        legs=work_legs(second=renamed),
    ))
    state = late_legs(state=state, lates={})
    assert all(
        chain.missed is None for chain in live_chains(state=state)
    )


def test_offers_forget_a_bus_that_has_left() -> None:
    """Offers forget a bus that has left."""
    state = with_offers(state=replace(answered(), now=at(7, 34)))
    assert ('a0733', LEG1[0].service_day) not in state.board.offers.kept
    assert len(state.board.offers.kept) == 4


def test_offers_start_again_for_a_new_place() -> None:
    """Offers start again for a new place."""
    assert with_place(state=answered(), pin=None).board.offers == Offers()


def test_an_edited_pin_is_searched_afresh() -> None:
    """Allow 6 now: the late 7:33 takes the 662, nothing held."""
    state = late_legs(state=answered(), lates={'a0733': 4})
    edited = replace(
        WORK, ways=(Way(changes=(replace(WORK_CHANGE, allow=6),)),),
    )
    pins = list(state.places.pins)
    pins[state.places.current] = edited
    state = replace(state, places=replace(state.places, pins=tuple(pins)))
    assert first_trips(state) == ['a0733', 'b0751']


def test_a_remembered_change_outlives_a_dropped_feed() -> None:
    """The feed forgets the 7:33: timetable times, the same change, kept."""
    state = late_legs(state=answered(), lates={'a0733': 6})
    state = late_legs(state=state, lates={})
    chain = live_chains(state=state)[0]
    assert first_trips(state) == ['a0733', 'b0746']
    assert chain.missed is None and not chain.legs[0].live
    assert ('a0733', LEG1[0].service_day) in state.board.offers.kept


def test_offers_wait_while_a_feed_never_answers() -> None:
    """Trains down: nothing is remembered and a late 606 re-plans."""
    trains = tuple(replace(item, source=SydneyFeed.TRAIN) for item in LEG2)
    state = loaded(first=LEG1[:1], second=trains)
    buses, rail = sorted(state.feeds)
    late = delayed_trip(trip_id='a0733', sequence=EVERY_STOP, seconds=6 * 60)
    for snapshot in (
        RealtimeSnapshot(fetched_at=state.now, trips={}),
        RealtimeSnapshot(fetched_at=state.now, trips={'a0733': late}),
    ):
        state, _ = step(state, FeedFetched(feed=buses, snapshot=snapshot))
        state, _ = step(
            state, FeedFailed(feed=rail, at=state.now, reason='HTTP 500'),
        )
    assert state.board.offers.kept == {}
    assert first_trips(state) == ['a0733', 'b0801a']
    assert live_chains(state=state)[0].missed is None


def test_live_chains_switch_a_late_held_change_to_a_sooner_route_from_the_same_bus(
) -> None:
    """The 665 runs 20 late: the 7:33 takes the 662, arriving 8:02."""
    state = late_legs(state=answered(), lates={'b0746': 20})
    assert first_trips(state) == ['a0733', 'b0751']
    assert live_chains(state=state)[0].missed is None


def test_school_setting_change_keeps_held_changes() -> None:
    """Turning school buses on keeps the changes the board remembers."""
    state = late_legs(state=answered(), lates={'a0733': 4})
    offers = state.board.offers
    state, _ = step(state, SettingsChanged(skip_school_buses=False))
    state, _ = step(state, DeparturesLoaded(
        pin=WORK, departures=(), until=at(10, 30), legs=work_legs(),
        skip_school=False,
    ))
    assert state.board.offers == offers
    assert first_trips(state) == ['a0733', 'b0746']


@pytest.fixture(name='searches')
def _searches(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """
    Count the chain searches `live_chains` makes.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patcher.

    Returns
    -------
    list[int]
        One entry per search.
    """
    counted: list[int] = []
    def counting(**fields: object) -> tuple[Chain, ...]:
        counted.append(1)
        return chains_of(**fields)  # type: ignore[arg-type]

    monkeypatch.setattr(connection, 'chains_of', counting)
    return counted


def test_live_chains_searched_once_per_event(searches: list[int]) -> None:
    """A tick's reduce and its drawn connection view share one search."""
    state = late_legs(state=answered(), lates={'a0733': 4})
    searches.clear()
    step(state, Tick(now=state.now + timedelta(minutes=1)))
    assert len(searches) == 1


def test_live_chains_searched_again_for_a_new_snapshot(
    searches: list[int],
) -> None:
    """A new snapshot misses the memo; the same state hits it."""
    state = late_legs(state=answered(), lates={'a0733': 4})
    searches.clear()
    live_chains(state=state)
    assert not searches
    late_legs(state=state, lates={'a0733': 5})
    assert searches
