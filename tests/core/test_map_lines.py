"""Tests for the Map's route lines."""

from dataclasses import replace

from paperroute_testkit.core.factories import START, departure
from paperroute_transit.core.map_lines import first_per_line, map_line_key
from paperroute_transit.core.model import Departure, TripFacts


def rail(*, route: str, route_id: str) -> Departure:
    """A train departure on a feed route."""
    return replace(
        departure(departs=START, route=route),
        facts=TripFacts(route_id=route_id, road=False),
    )


def test_map_line_key_is_the_route_on_the_road() -> None:
    """A bus's line is its route."""
    assert map_line_key(departure=departure(departs=START)) == '606'


def test_map_line_key_is_the_feed_route_on_rail() -> None:
    """Two trips on one feed route share a line; another route does not."""
    norwich = rail(route='LE', route_id='rail:LE:Norwich')
    again = rail(route='LE', route_id='rail:LE:Norwich')
    ipswich = rail(route='LE', route_id='rail:LE:Ipswich')
    assert map_line_key(departure=norwich) == map_line_key(departure=again)
    assert map_line_key(departure=norwich) != map_line_key(
        departure=ipswich,
    )


def test_first_per_line_keeps_order() -> None:
    """The first departure of each line is kept, in order."""
    a = departure(departs=START, route='606', trip_id='a')
    b = departure(departs=START, route='610', trip_id='b')
    c = departure(departs=START, route='606', trip_id='c')
    assert first_per_line(departures=(a, b, c)) == (a, b)
