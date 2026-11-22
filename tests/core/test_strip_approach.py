"""Tests for buses drawn approaching a both-sides From."""

from paperroute_testkit.core.factories import place
from paperroute_testkit.core.reach_factories import tail
from paperroute_transit.core.reach import ReachInput
from paperroute_transit.core.sides import KerbPair
from paperroute_transit.core.strip_approach import approach_stations, facing_of


def test_approach_stations_are_the_stops_before_the_from() -> None:
    """Both kerbs' stops before the From are asked for."""
    data = ReachInput(
        origin=place('C'), tails=(tail('B1', 6, before='P2 P1'),),
        places={}, station_of={},
        across_tails=(tail('A1', 6, before='Q1'),),
    )
    assert approach_stations(data=data) == {'P2', 'P1', 'Q1'}


def test_facing_of_names_each_stops_partner() -> None:
    """Each pair gives the station across the road."""
    pair = KerbPair(station='P1', other=place('A1'), busy=False)
    assert facing_of(pairs={'P1': pair}) == {'P1': 'A1'}
