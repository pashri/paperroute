"""Tests for the route gauge's line ends and its buses."""

from datetime import date
from functools import partial

from paperroute_transit.core.gauge_buses import GaugeBus
from paperroute_transit.core.gauge_ends import placed, route_line, slot_of
from paperroute_transit.core.gauge_line import (
    BusSlot,
    GaugeBreak,
    GaugeLine,
    GaugeMark,
    MarkKind,
)
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.stop_under import GaugeCall


def bus(spot: float, *, right: bool = True, live: bool = False) -> GaugeBus:
    """Build a bus at a spot."""
    return GaugeBus(spot=spot, right=right, live=live, trip_id='t')


def broken(*, arrow: bool = False) -> GaugeLine:
    """Build a line with a break between slots 3 and 6."""
    marks = tuple(
        GaugeMark(slot=slot, kind=MarkKind.CALL, name=f'S{slot}')
        for slot in (0, 3, 6, 9)
    )
    return GaugeLine(
        marks=marks,
        ahead=GaugeBreak(first=3, last=6, count=7),
        stops=(0, 1, 9, 10),
        arrow=arrow,
    )


def test_placed_folds_two_buses_in_a_break_into_a_count_mid_break() -> None:
    """Two buses in a break, one each way, become one count."""
    found = placed(
        line=broken(),
        buses=[bus(3, right=True), bus(7, right=False)],
    )
    assert found == (BusSlot(slot=4.5, right=None, live=False, count=2),)


def test_placed_keeps_one_bus_in_a_break_as_its_badge() -> None:
    """A lone bus in a break stays a badge."""
    found = placed(line=broken(), buses=[bus(5, live=True)])
    assert found == (BusSlot(slot=4.5, right=True, live=True),)


def test_placed_keeps_a_shared_direction_on_a_count() -> None:
    """A count keeps what its buses share."""
    found = placed(line=broken(), buses=[bus(3), bus(7)])
    assert found[0].right is True


def test_placed_folds_buses_past_the_arrow_at_the_arrow() -> None:
    """Buses beyond the arrow fold into a count at the last mark."""
    found = placed(
        line=broken(arrow=True),
        buses=[bus(11), bus(12, live=True), bus(0)],
    )
    assert found == (
        BusSlot(slot=0.0, right=True, live=False),
        BusSlot(slot=9.0, right=True, live=None, count=2),
    )


def test_route_line_places_its_buses() -> None:
    """The line carries its buses at their slots."""
    pattern = TripPattern(
        trip_id='t',
        route='1',
        source='s',
        shape_id=None,
        stops=tuple(
            PatternStop(
                arrives=0,
                departs=0,
                point=None,
                station=f'S{n}',
                name=f'Stop {n}',
                passes=False,
            )
            for n in range(12)
        ),
        days=(date(2026, 11, 23),),
    )
    calls = tuple(
        GaugeCall(name=f'Stop {n}', minutes=':00') for n in range(1, 12)
    )
    lay = partial(
        route_line,
        pattern=pattern,
        start=0,
        end=None,
        calls=calls,
        budget=4,
        room=6,
        rules=NameRules(),
    )
    bare = lay(buses=())
    found = lay(buses=[bus(2, live=True)])
    assert found.buses == (
        BusSlot(slot=slot_of(line=bare, spot=2.0), right=True, live=True),
    )
