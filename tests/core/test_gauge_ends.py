"""Tests for the route gauge line."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import START, pattern
from paperroute_transit.core.gauge import gauge_calls
from paperroute_transit.core.gauge_buses import GaugeBus
from paperroute_transit.core.gauge_ends import (
    break_of,
    finished_line,
    laid,
    left_end,
    left_named,
    route_line,
    slot_of,
)
from paperroute_transit.core.gauge_line import GaugeBreak, GaugeLine, MarkKind
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.stop_under import GaugeCall

NAMES: Final[dict[int, str]] = {0: 'Manor Farm Close', 12: 'Orchard Close'}
P20: Final[TripPattern] = pattern(
    stops=tuple(
        PatternStop(
            arrives=8 * 3600 + 60 * index,
            departs=8 * 3600 + 60 * index,
            point=(100.0 * index, 0.0),
            station=f's{index}',
            name=NAMES.get(index, f'Halt {chr(65 + index)}'),
        )
        for index in range(20)
    ),
)
RULES: Final[NameRules] = NameRules(centres=('Here',))


def calls_of(*, start: int, end: int | None) -> tuple[GaugeCall, ...]:
    """
    List P20's calls after a From.

    Parameters
    ----------
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.

    Returns
    -------
    tuple[GaugeCall, ...]
        The calls.
    """
    calls, _ = gauge_calls(
        pattern=P20,
        start=start,
        end=end,
        day=START.date(),
        delay=0,
        zone=SYDNEY_ZONE,
    )
    return calls


def line_of(
    *,
    start: int = 12,
    end: int | None = None,
    buses: Sequence[GaugeBus] = (),
    room: int = 6,
) -> GaugeLine:
    """
    Lay P20 out with a left budget of 10 slots.

    Parameters
    ----------
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.
    buses : Sequence[GaugeBus]
        Buses on the route.
    room : int
        Slots right of the From.

    Returns
    -------
    GaugeLine
        The line.
    """
    return route_line(
        pattern=P20,
        start=start,
        end=end,
        calls=calls_of(start=start, end=end),
        buses=buses,
        budget=10,
        room=room,
        rules=RULES,
    )


def bus(*, spot: float) -> GaugeBus:
    """
    A live bus running the hero's way.

    Parameters
    ----------
    spot : float
        Fractional pattern index.

    Returns
    -------
    GaugeBus
        The bus.
    """
    return GaugeBus(spot=spot, right=True, live=True, trip_id='t')


def cut_line() -> GaugeLine:
    """
    Lay P20 out with a left budget too small for stop 0.

    Returns
    -------
    GaugeLine
        The cut line.
    """
    return route_line(
        pattern=P20,
        start=12,
        end=None,
        calls=calls_of(start=12, end=None),
        buses=(bus(spot=4.5), bus(spot=10.5)),
        budget=7,
        room=6,
        rules=RULES,
    )


def test_laid_fills_short_gaps_and_breaks_long_ones() -> None:
    """A gap of three fills; a gap of four is a break."""
    assert laid(named=(0, 3, 7)) == (
        {0: 0, 1: 1, 2: 2, 3: 3, 7: 6},
        [GaugeBreak(first=3, last=6, count=3)],
    )


def test_left_named_without_buses_names_the_first_stop_and_the_from(
) -> None:
    """Stop 0, a break, the From."""
    assert left_named(start=12, behind=(), budget=10) == ((0, 12), False)


def test_left_named_names_the_stops_buses_last_left() -> None:
    """Each bus behind names the stop it last left."""
    named = left_named(start=12, behind=(9.4, 11.2), budget=10)
    assert named == ((0, 9, 10, 11, 12), False)


def test_left_named_ends_in_an_arrow_when_stop_0_needs_a_second_break(
) -> None:
    """Stop 0 is dropped when it needs a second break."""
    # 4 fits with one break (slots 0, 3, 4, 5); stop 0 would need a second
    named = left_named(start=12, behind=(4.5, 10.5), budget=7)
    assert named == ((4, 10, 11, 12), True)


def test_route_line_from_at_the_first_stop_has_no_left_break() -> None:
    """A From at stop 0 starts the line."""
    line = line_of(start=0, room=9)
    assert line.marks[0].kind is MarkKind.FROM
    assert (line.cut, line.back) == (False, None)


def test_route_line_without_destination_ends_break_then_last_stop(
) -> None:
    """Calls to the room, a break, the last stop."""
    line = line_of()
    assert line.stops == (0, 12, 13, 14, 15, 19)
    assert line.ahead == GaugeBreak(first=6, last=9, count=3)
    assert line.marks[-1].slot == 9


def test_route_line_with_destination_bolds_it_then_breaks_to_the_end(
) -> None:
    """The ride runs From to Destination, then a break to the end."""
    line = line_of(end=14)
    kinds = [mark.kind for mark in line.marks]
    assert kinds[1:] == [
        MarkKind.FROM,
        MarkKind.CALL,
        MarkKind.DESTINATION,
        MarkKind.AFTER,
    ]
    assert line.ride == (3, 5)
    assert line.ahead == GaugeBreak(first=5, last=8, count=4)


def test_route_line_with_a_far_destination_breaks_before_it() -> None:
    """A Destination past the room keeps the calls that fit."""
    line = line_of(end=18)
    assert line.stops[-6:] == (13, 14, 15, 16, 17, 18)
    assert line.marks[-1].kind is MarkKind.DESTINATION
    assert line.arrow


def test_route_line_orchard_close_reads_first_stop_break_from() -> None:
    """With no bus behind, stop 0, a break of 11, the From."""
    line = line_of()
    assert [mark.name for mark in line.marks[:2]] == [
        'Manor Farm Close',
        'Orchard Close',
    ]
    assert line.back == GaugeBreak(first=0, last=3, count=11)


def test_route_line_cut_moves_the_line_right_for_the_arrow() -> None:
    """An arrow takes slot 0."""
    line = cut_line()
    assert line.cut
    assert (line.marks[0].slot, line.stops[0]) == (1, 4)


def test_slot_of_places_a_bus_in_a_break_proportionally() -> None:
    """Halfway through a break's stops is halfway across it."""
    line = line_of()
    assert slot_of(line=line, spot=6.0) == 1.5
    assert break_of(line=line, slot=1.5) == line.back
    assert break_of(line=line, slot=4.0) is None


def test_slot_of_puts_a_bus_beyond_the_arrow_at_the_arrow() -> None:
    """A bus left of a cut line sits at the arrow."""
    line = cut_line()
    assert slot_of(line=line, spot=1.0) == 0.0
    assert slot_of(line=line, spot=19.5) == 12.0


def passing_at(*, index: int) -> TripPattern:
    """
    P20 with one stop the trip runs through.

    Parameters
    ----------
    index : int
        Pattern index of the passing stop.

    Returns
    -------
    TripPattern
        The pattern.
    """
    stops = list(P20.stops)
    stops[index] = replace(stops[index], passes=True)
    return replace(P20, stops=tuple(stops))


def test_route_line_neither_names_nor_counts_a_passing_stop_behind(
) -> None:
    """The left break counts calls, and the fill skips a passing stop."""
    trip = passing_at(index=10)
    line = route_line(
        pattern=trip,
        start=12,
        end=None,
        calls=calls_of(start=12, end=None),
        buses=(bus(spot=10.5),),
        budget=10,
        room=6,
        rules=RULES,
    )
    assert line.stops[:4] == (0, 9, 11, 12)
    assert line.back == GaugeBreak(first=0, last=3, count=8)


def test_route_line_left_break_counts_calls_only() -> None:
    """A passing stop inside the left break is not counted."""
    line = route_line(
        pattern=passing_at(index=5),
        start=12,
        end=None,
        calls=calls_of(start=12, end=None),
        buses=(),
        budget=10,
        room=6,
        rules=RULES,
    )
    assert line.back == GaugeBreak(first=0, last=3, count=10)


def test_route_line_tiny_room_ends_at_the_last_call_that_fits() -> None:
    """With no room for a break, the line stops at the room with an arrow."""
    line = line_of(room=2)
    assert line.stops[-3:] == (12, 13, 14)
    assert (line.ahead, line.arrow) == (None, True)
    assert line.marks[-1].slot == 3 + 2


def test_route_line_tiny_room_keeps_the_destination_in_the_last_slot(
) -> None:
    """A Destination that does not fit replaces the last call that fits."""
    line = line_of(room=2, end=18)
    assert line.marks[-1].kind is MarkKind.DESTINATION
    assert (line.stops[-1], line.marks[-1].slot) == (18, 5)
    assert (line.ride, line.arrow) == ((3, 5), True)


@pytest.mark.parametrize(
    ('start', 'end', 'budget', 'room', 'spots'),
    [
        (12, None, 10, 6, ()),
        (12, 17, 7, 6, (4.5, 10.5)),
        (3, 8, 10, 3, (1.5, 6.5)),
        (0, None, 2, 12, (14.0,)),
    ],
)
def test_finished_line_from_left_end_matches_route_line(
    *,
    start: int,
    end: int | None,
    budget: int,
    room: int,
    spots: tuple[float, ...],
) -> None:
    """Laying the left end first gives exactly the whole line."""
    buses = tuple(bus(spot=spot) for spot in spots)
    calls = calls_of(start=start, end=end)
    whole = route_line(
        pattern=P20,
        start=start,
        end=end,
        calls=calls,
        buses=buses,
        budget=budget,
        room=room,
        rules=RULES,
    )
    left = left_end(pattern=P20, start=start, buses=buses, budget=budget)
    assert finished_line(
        left=left,
        pattern=P20,
        start=start,
        end=end,
        calls=calls,
        buses=buses,
        room=room,
        rules=RULES,
    ) == whole
