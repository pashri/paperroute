"""Tests for a connection pin's loaded legs, pairs and alert facts."""

from dataclasses import replace
from typing import Final

from paperroute.city import SYDNEY, SYDNEY_ZONE, SydneyFeed
from paperroute.core.alert_match import tie_of
from paperroute.core.connection import (
    chain_legs,
    connection_facts,
    live_chains,
)
from paperroute.core.feeds import ALERT_FEEDS
from paperroute.core.messages import DeparturesLoaded
from paperroute.core.saved import seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import Board, State
from paperroute_device.core.text import clock_text
from paperroute_testkit.core.alert_factories import alert
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
from paperroute_testkit.core.steps import wakeful
from paperroute_transit.core.alert_model import (
    Alert,
    AlertsFetched,
    AlertsSnapshot,
    AlertTie,
    BetweenLoaded,
    Informed,
)
from paperroute_transit.core.model import Change, Departure, Pin, Way

FERRY_CHANGE: Final[Change] = replace(
    WORK_CHANGE, board=place('10102027', name='Parramatta Wharf'),
)
FERRY_WORK: Final[Pin] = replace(WORK, ways=(Way(changes=(FERRY_CHANGE,)),))
FERRY_LEG: Final[tuple[Departure, ...]] = (
    departure(
        departs=at(7, 50), route='F3', mode=SydneyFeed.FERRY, trip_id='ferry-1',
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
        saved=seeded(pin=pin, city=SYDNEY), now=at(7, 30), stored=True,
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
    assert [clock_text(
        moment=chain.arrives,
        zone=SYDNEY_ZONE,
    ) for chain in chains] == [
        '7:57', '8:12', '8:27', '8:45', '9:04',
    ]
    assert [len(chain_legs(chain=chain)) for chain in chains] == [2] * 5
    plain = replace(WORK, ways=())
    assert live_chains(state=loaded(pin=plain)) == ()


def test_direct_list_competes() -> None:
    """A direct trip in the board's own list is a one-leg chain."""
    direct = (
        departure(departs=at(7, 40), ride=10, route='609', trip_id='d1'),
    )
    state = wakeful(state=initial_state(
        saved=seeded(pin=WORK, city=SYDNEY), now=at(7, 30), stored=True,
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
