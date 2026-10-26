"""Tests for a connection pin's loaded legs, pairs and alert facts."""

from dataclasses import replace
from typing import Final

from paperroute.core.alert_match import tie_of
from paperroute.core.alert_model import AlertTie
from paperroute.core.connection import connection_facts, live_pairs, pair_legs
from paperroute.core.connection_model import Legs
from paperroute.core.messages import DeparturesLoaded
from paperroute.core.model import Change, Departure, Mode, Pin
from paperroute.core.saved import seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.stop_view import clock_text
from tests.core.alert_factories import alert
from tests.core.connection_factories import LEG1, LEG2, WORK, WORK_CHANGE, at
from tests.core.factories import departure, place
from tests.core.steps import wakeful

FERRY_CHANGE: Final[Change] = replace(
    WORK_CHANGE, board=place('10102027', name='Parramatta Wharf'),
)
FERRY_WORK: Final[Pin] = replace(WORK, change=FERRY_CHANGE)
FERRY_LEG: Final[tuple[Departure, ...]] = (
    departure(
        departs=at(7, 50), route='F3', mode=Mode.FERRY, trip_id='ferry-1',
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
        saved=seeded(pin=pin), now=at(7, 30), stored=True,
    ))
    state, _ = step(state, DeparturesLoaded(
        pin=pin,
        departures=first,
        until=at(10, 30),
        legs=Legs(second=second),
    ))
    return state


def test_feeds_union_of_legs() -> None:
    """Both legs' trip feeds are polled."""
    trains = tuple(replace(item, mode=Mode.TRAIN) for item in LEG2)
    state = loaded(second=trains)
    assert set(state.feeds) == {
        'v1/gtfs/realtime/buses',
        'v2/gtfs/realtime/sydneytrains',
    }


def test_live_pairs_from_board() -> None:
    """The loaded legs pair as Task 1's real times do."""
    state = loaded()
    pairs = live_pairs(state=state)
    assert [clock_text(moment=pair.arrives) for pair in pairs] == [
        '7:57', '8:12', '8:27', '8:45', '9:04',
    ]
    assert [len(pair_legs(pair=pair)) for pair in pairs] == [2] * 5
    plain = replace(WORK, change=None)
    assert live_pairs(state=loaded(pin=plain)) == ()


def test_connection_alert_matches_both_legs() -> None:
    """Alerts on the change stop, the From or a leg-2 route match."""
    facts = connection_facts(
        pin=WORK,
        departures=LEG1,
        legs=Legs(second=LEG2),
        between=frozenset(),
        families={},
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
        departures=LEG1,
        legs=Legs(second=FERRY_LEG),
        between=frozenset(),
        families={},
    )
    assert {'Parramatta', 'Westmead'} <= ferry.names
    assert ferry.wharf
    bus = connection_facts(
        pin=WORK,
        departures=LEG1,
        legs=Legs(second=LEG2),
        between=frozenset(),
        families={},
    )
    assert not bus.names


def test_direct_trips_matched() -> None:
    """The direct list's trips and routes are matched too."""
    direct = (departure(departs=at(8, 0), route='609', trip_id='d1'),)
    facts = connection_facts(
        pin=WORK,
        departures=(),
        legs=Legs(direct=direct),
        between=frozenset(),
        families={},
    )
    assert 'd1' in facts.trips
    assert '609' in facts.routes
