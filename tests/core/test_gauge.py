from dataclasses import replace
from typing import Final

import pytest

from paperroute_testkit.core.factories import START, vehicle
from paperroute_transit.core.gauge import BusSeen, bus_seen, bus_words
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import VehiclePosition
from tests.core.test_stop_hero import RUN


def at(*, x: float) -> VehiclePosition:
    """
    A report of the trip's vehicle `x` metres east.

    Parameters
    ----------
    x : float
        Metres east of the origin.

    Returns
    -------
    VehiclePosition
        The report.
    """
    return vehicle(
        timestamp=int(START.timestamp()), trip_id='trip-0803', point=(x, 0.0),
    )


FIRST: Final[TripPattern] = replace(RUN, stops=RUN.stops[3:])


@pytest.mark.parametrize(('x', 'seen'), [
    (-375.0, BusSeen(left=0, away=2, waiting=False)),
    (-75.0, BusSeen(left=2, away=1, waiting=False)),
    (0.0, BusSeen(left=3, away=0, waiting=False)),
    (75.0, None),
])
def test_bus_seen_counts_the_calls_still_to_make(
    x: float, seen: BusSeen | None,
) -> None:
    """The calls left run up to and including the From."""
    assert bus_seen(
        pattern=RUN, start=3, vehicle=at(x=x), departed=True,
    ) == seen


def test_bus_seen_snaps_back_from_a_passing_stop() -> None:
    """A vehicle past a passing stop counts from the call before it."""
    assert bus_seen(
        pattern=RUN, start=3, vehicle=at(x=-225.0), departed=True,
    ) == BusSeen(left=0, away=2, waiting=False)


def test_bus_seen_waits_at_the_first_stop_until_it_departs() -> None:
    """Before departure the vehicle waits at the first stop."""
    assert bus_seen(
        pattern=RUN, start=3, vehicle=at(x=-450.0), departed=False,
    ) == BusSeen(left=0, away=2, waiting=True)
    assert bus_seen(
        pattern=RUN, start=3, vehicle=at(x=-450.0), departed=True,
    ) == BusSeen(left=0, away=2, waiting=False)


def test_bus_seen_waits_at_a_from_that_starts_the_trip() -> None:
    """A From at the start of the trip can be waited at, not passed."""
    assert bus_seen(
        pattern=FIRST, start=0, vehicle=at(x=0.0), departed=False,
    ) == BusSeen(left=0, away=0, waiting=True)
    assert bus_seen(
        pattern=FIRST, start=0, vehicle=at(x=0.0), departed=True,
    ) is None


@pytest.mark.parametrize(('bus', 'words'), [
    (BusSeen(left=0, away=2, waiting=False), '2 stops away'),
    (BusSeen(left=2, away=1, waiting=False), '1 stop away'),
    (BusSeen(left=3, away=0, waiting=False), 'at the stop now'),
    (BusSeen(left=0, away=3, waiting=True), '3 stops away, not left yet'),
    (BusSeen(left=0, away=0, waiting=True), 'waiting here'),
])
def test_bus_words_say_how_far_and_whether_it_has_left(
    bus: BusSeen, words: str,
) -> None:
    """The words give the distance and whether it has left."""
    assert bus_words(bus=bus) == words
