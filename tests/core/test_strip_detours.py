"""Tests for finding short loops and stacked spurs in a trip."""

import math
from datetime import date
from typing import Final

from paperroute_transit.core.geo import Point
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.strip_detours import (
    DETOURS,
    Detour,
    DetourKind,
    detours_of,
    is_there_and_back,
    place_key,
    spur_levels,
)

type Call = tuple[str, str, Point | None]

LOOP: Final = DetourKind.LOOP
SPUR: Final = DetourKind.SPUR
QVB: Final[list[Call]] = [
    ('th', 'Town Hall', (0.0, 0.0)),
    ('qvb-d', 'Queen Victoria Building, Stand D', (0.0, 500.0)),
    ('x', 'Park St', (300.0, 800.0)),
    ('y', 'Market St', (600.0, 500.0)),
    ('qvb-b', 'Queen Victoria Building, Stand B', (300.0, 200.0)),
    ('wy', 'Wynyard', (0.0, 1500.0)),
]
MANOR: Final[list[Call]] = [
    ('in', 'Hills Rd', (0.0, -800.0)),
    ('j1', 'Manor Close, Stop A', (0.0, 0.0)),
    ('a1', 'Elm Way', (0.0, 300.0)),
    ('b1', 'Oak Way', (0.0, 600.0)),
    ('turn', 'Manor Close End', (0.0, 900.0)),
    ('b2', 'Oak Lane', (20.0, 600.0)),
    ('a2', 'Elm Lane', (20.0, 300.0)),
    ('j2', 'Manor Close, Stop B', (20.0, 0.0)),
    ('out', 'Mill Rd', (800.0, 0.0)),
]


def pattern(calls: list[Call]) -> TripPattern:
    """
    Build a trip from its calls.

    Parameters
    ----------
    calls : list[Call]
        Station, name and point of each call.

    Returns
    -------
    TripPattern
        The trip.
    """
    return TripPattern(
        trip_id='t',
        route='441',
        source='bus',
        shape_id=None,
        stops=tuple(
            PatternStop(
                arrives=60 * index,
                departs=60 * index,
                point=point,
                station=station,
                name=name,
            )
            for index, (station, name, point) in enumerate(calls)
        ),
        days=(date(2026, 11, 24),),
    )


def ring(*, count: int, centre: Point, radius: float) -> list[Call]:
    """
    Give calls spaced round a circle.

    Parameters
    ----------
    count : int
        Calls.
    centre : Point
        Circle centre in metres.
    radius : float
        Circle radius in metres.

    Returns
    -------
    list[Call]
        The calls, named by their order.
    """
    return [
        (
            f'r{centre[0]:.0f}-{index}',
            f'Ring {centre[0]:.0f} {index}',
            (
                centre[0] + radius * math.cos(index * 6.28 / count),
                centre[1] + radius * math.sin(index * 6.28 / count),
            ),
        )
        for index in range(count)
    ]


def test_place_key_uses_the_name_head() -> None:
    """Two stands of one place share a key."""
    stops = pattern(QVB).stops
    assert place_key(stop=stops[1]) == place_key(stop=stops[4])


def test_place_key_uses_the_station_for_a_short_head() -> None:
    """A head of two characters or fewer falls back to the station."""
    stop = PatternStop(
        arrives=0, departs=0, point=None, station='s1', name='QV, Stand D',
    )
    assert place_key(stop=stop) == 's1'


def test_detours_of_rings_a_short_mid_trip_loop() -> None:
    """The QVB city loop rings."""
    assert detours_of(pattern=pattern(QVB)) == (Detour(LOOP, 1, 4),)


def test_detours_of_leaves_a_loop_at_the_trip_start_as_rows() -> None:
    """A loop from the first stop is rows."""
    assert not detours_of(pattern=pattern(QVB[1:]))


def test_detours_of_leaves_a_loop_at_the_trip_end_as_rows() -> None:
    """A loop back at the last stop is rows."""
    assert not detours_of(pattern=pattern(QVB[:-1]))


def test_detours_of_leaves_a_seven_stop_loop_as_rows() -> None:
    """Seven stops between the visits is too long to ring."""
    calls = (
        QVB[:2]
        + ring(count=7, centre=(1500.0, 1500.0), radius=1000.0)
        + QVB[4:]
    )
    assert not detours_of(pattern=pattern(calls))


def test_detours_of_rings_only_the_innermost_loop() -> None:
    """A loop holding a smaller loop is rows; the inner one rings."""
    calls = (
        QVB[:2]
        + [('k', 'Kent St, Stand 1', (3000.0, 0.0))]
        + ring(count=3, centre=(3000.0, 1000.0), radius=600.0)
        + [('k2', 'Kent St, Stand 2', (3100.0, 0.0))]
        + QVB[4:]
    )
    assert detours_of(pattern=pattern(calls)) == (Detour(LOOP, 2, 6),)


def test_detours_of_leaves_a_tight_one_block_loop_as_rows() -> None:
    """Every return stop within 200 m of an out stop makes rows."""
    calls = [
        QVB[0],
        QVB[1],
        ('x', 'Park St', (150.0, 550.0)),
        ('m', 'York St', (250.0, 650.0)),
        ('y', 'Market St', (250.0, 500.0)),
        ('qvb-b', 'Queen Victoria Building, Stand B', (220.0, 450.0)),
        QVB[5],
    ]
    assert not detours_of(pattern=pattern(calls))


def test_detours_of_finds_a_two_level_spur() -> None:
    """A stacked there-and-back is a spur."""
    assert detours_of(pattern=pattern(MANOR)) == (Detour(SPUR, 1, 7),)


def test_detours_of_leaves_a_one_level_there_and_back_as_rows() -> None:
    """A turn with no paired stop past the junction is rows."""
    calls = MANOR[:2] + [MANOR[3], MANOR[5]] + MANOR[7:]
    assert not detours_of(pattern=pattern(calls))


def test_detours_of_counts_a_loop_on_a_stick_as_a_spur() -> None:
    """A small loop at a spur's far end still stacks."""
    calls = [
        *MANOR[:4],
        ('c1', 'Loop Rd', (150.0, 600.0)),
        *MANOR[6:],
    ]
    assert detours_of(pattern=pattern(calls)) == (Detour(SPUR, 1, 6),)


def test_detours_of_skips_a_detour_with_an_unplaced_stop() -> None:
    """A candidate with a stop lacking coordinates is rows."""
    calls = [*QVB]
    calls[2] = ('x', 'Park St', None)
    assert not detours_of(pattern=pattern(calls))


def test_detours_of_remembers_the_pattern() -> None:
    """The same pattern gives the very same result."""
    trip = pattern(QVB)
    assert detours_of(pattern=trip) is detours_of(pattern=trip)
    assert DETOURS.held


def test_is_there_and_back_skips_the_middle_of_an_odd_loop() -> None:
    """The turn stop of an odd loop belongs to neither leg."""
    stops = pattern(MANOR).stops[1:8]
    assert is_there_and_back(stops=stops)


def test_spur_levels_is_zero_when_the_ends_are_apart() -> None:
    """A junction whose two visits are far apart does not stack."""
    assert spur_levels(stops=pattern(QVB).stops[1:5]) == 0
