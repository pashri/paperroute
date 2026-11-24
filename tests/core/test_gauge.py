"""Tests for where the hero's vehicle is and the calls after the From."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import START, vehicle
from paperroute_transit.core.gauge import (
    BusSeen,
    arrival_call,
    bus_seen,
    bus_words,
    gauge_calls,
)
from paperroute_transit.core.model import Departure
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.stop_under import GaugeCall
from tests.core.test_stop_hero import HERO, RUN, stop


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


def calls_of(
    *, end: int | None = None,
) -> tuple[tuple[GaugeCall, ...], int | None]:
    """
    The marks after the From of `RUN`, 2 minutes late.

    Parameters
    ----------
    end : int | None
        Pattern index of the Destination.

    Returns
    -------
    tuple[tuple[GaugeCall, ...], int | None]
        The marks and the Destination's index among them.
    """
    return gauge_calls(
        pattern=RUN, start=3, end=end, day=START.date(), delay=120,
        zone=SYDNEY_ZONE,
    )


def test_gauge_calls_skip_passes_and_repeats_with_minutes() -> None:
    """Calls skip repeated names and carry the delayed minutes."""
    assert calls_of()[0] == (
        GaugeCall(name='Harris Park', minutes=':08'),
        GaugeCall(name='Rosehill', minutes=':11'),
        GaugeCall(name='Parramatta', minutes=':14'),
    )


def test_arrival_call_finds_the_destination_by_its_arrival() -> None:
    """The Destination is the later call arriving at the stated time."""
    hero = replace(HERO, arrives=START + timedelta(minutes=9))
    assert arrival_call(
        pattern=RUN, departure=hero, start=3, zone=SYDNEY_ZONE,
    ) == 6
    assert calls_of(end=6)[1] == 1


def test_arrival_call_finds_no_destination_off_its_own_trip() -> None:
    """An arrival matching no call gives no Destination."""
    hero = replace(HERO, arrives=START + timedelta(minutes=40))
    end = arrival_call(pattern=RUN, departure=hero, start=3, zone=SYDNEY_ZONE)
    assert end is None
    assert calls_of(end=end)[1] is None


def calls_at(*, offsets: tuple[int, ...], stations: str) -> TripPattern:
    """
    A trip from the From, calling at `stations` after `offsets` seconds.

    Parameters
    ----------
    offsets : tuple[int, ...]
        Seconds after the From's call for each later call.
    stations : str
        One station ID letter per later call.

    Returns
    -------
    TripPattern
        The trip, whose first call is the From.
    """
    first = stop(at=0, x=0.0, station='F', name='From St')
    rest = tuple(
        stop(at=at, x=100.0 * (i + 1), station=code, name=f'Stop {code}')
        for i, (at, code) in enumerate(zip(offsets, stations))
    )
    return replace(RUN, stops=(first, *rest))


def arrives_at(*, seconds: int) -> Departure:
    """
    The hero arriving `seconds` after eight o'clock.

    Parameters
    ----------
    seconds : int
        Seconds after 8:00.

    Returns
    -------
    Departure
        The departure.
    """
    return replace(HERO, arrives=START + timedelta(seconds=seconds))


def test_arrival_call_prefers_the_destination_station_over_a_tied_time(
) -> None:
    """A call sharing the arrival time at another station is skipped."""
    trip = calls_at(offsets=(60, 120, 120), stations='ABC')
    assert arrival_call(
        pattern=trip,
        departure=arrives_at(seconds=120),
        start=0,
        station='C',
        zone=SYDNEY_ZONE,
    ) == 3


def test_arrival_call_ignores_a_passing_stop_with_the_same_time() -> None:
    """A stop the bus passes never ends the ride."""
    trip = calls_at(offsets=(60, 120, 120), stations='ABC')
    trip = replace(trip, stops=(
        *trip.stops[:2], replace(trip.stops[2], passes=True), trip.stops[3],
    ))
    assert arrival_call(
        pattern=trip,
        departure=arrives_at(seconds=120),
        start=0,
        station='Z',
        zone=SYDNEY_ZONE,
    ) == 3


def test_arrival_call_picks_the_loop_call_whose_arrival_matches() -> None:
    """Two calls at the Destination's station: the arrival chooses."""
    trip = calls_at(offsets=(60, 120, 240), stations='CAC')
    assert arrival_call(
        pattern=trip,
        departure=arrives_at(seconds=240),
        start=0,
        station='C',
        zone=SYDNEY_ZONE,
    ) == 3


def test_arrival_call_falls_back_to_the_time_without_a_station_match(
) -> None:
    """A station no call has leaves the match to the arrival time."""
    trip = calls_at(offsets=(60, 120, 120), stations='ABC')
    assert arrival_call(
        pattern=trip,
        departure=arrives_at(seconds=60),
        start=0,
        station='Z',
        zone=SYDNEY_ZONE,
    ) == 1


def test_gauge_calls_keep_a_passing_destination_with_a_tied_neighbour(
) -> None:
    """A passing Destination stays when named by its index."""
    trip = calls_at(offsets=(60, 120, 120), stations='ABC')
    trip = replace(trip, stops=(
        *trip.stops[:3], replace(trip.stops[3], passes=True),
    ))
    calls, place = gauge_calls(
        pattern=trip, start=0, end=3, day=START.date(), delay=0,
        zone=SYDNEY_ZONE,
    )
    assert place == 2
    assert calls[-1].name == 'Stop C'
