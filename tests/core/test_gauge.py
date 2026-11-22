from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import START, pattern, vehicle
from paperroute_transit.core.gauge import (
    BusSeen,
    arrival_call,
    bus_seen,
    bus_words,
    gauge_calls,
    stop_gauge,
)
from paperroute_transit.core.model import Departure
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.stop_under import GaugeCall, StopGauge
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


LONG: Final[TripPattern] = pattern(
    trip_id='trip-0803',
    stops=(
        *(
            stop(
                at=-120 * (8 - i), x=-150.0 * (8 - i), station=f'S{i}',
                name=f'Stop {i}',
            )
            for i in range(8)
        ),
        stop(at=0, x=0.0, station='F', name='Hollis St'),
        *(
            stop(at=120 * k, x=150.0 * k, station=f'C{k}', name=f'Call {k}')
            for k in range(1, 4)
        ),
    ),
)


def gauge_of(
    *, pattern: TripPattern = RUN, start: int = 3, x: float | None = -75.0,
    departed: bool = True, end: int | None = None, delay: int = 120,
) -> StopGauge:
    """
    The gauge for a vehicle `x` metres east.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : int
        Pattern index of the From.
    x : float | None
        Metres east of the origin, or None for no vehicle.
    departed : bool
        Whether the trip has left its first stop.
    end : int | None
        Pattern index of the Destination.
    delay : int
        Seconds late.

    Returns
    -------
    StopGauge
        The gauge.
    """
    bus = None if x is None else bus_seen(
        pattern=pattern, start=start, vehicle=at(x=x), departed=departed,
    )
    return stop_gauge(
        pattern=pattern, start=start, end=end, bus=bus, day=START.date(),
        delay=delay, zone=SYDNEY_ZONE,
    )


def test_stop_gauge_names_every_stop_back_to_a_near_bus() -> None:
    """A near bus has every stop behind it named."""
    near = gauge_of(x=-375.0)
    assert (near.behind, near.hidden, near.bus, near.starts) == (
        ('Westmead', 'Mays Hill'), 0, 0.5, True,
    )
    one = gauge_of()
    assert (one.behind, one.bus, one.starts, one.words) == (
        ('Mays Hill',), 0.5, False, '1 stop away',
    )


def test_stop_gauge_shows_no_previous_stop_for_a_bus_at_the_from() -> None:
    """A bus at the From has no previous stop."""
    here = gauge_of(x=0.0)
    assert (here.behind, here.bus, here.words) == ((), 0.0, 'at the stop now')


def test_stop_gauge_names_only_the_stop_a_far_bus_left() -> None:
    """A far bus names one stop and hides the rest."""
    far = gauge_of(pattern=LONG, start=8, x=-825.0)
    assert (far.behind, far.hidden, far.bus, far.words) == (
        ('Stop 2',), 5, 0.5, '6 stops away',
    )


def test_stop_gauge_puts_a_waiting_bus_at_the_first_stop() -> None:
    """A bus yet to leave sits at the first mark."""
    waiting = gauge_of(x=-450.0, departed=False)
    assert (waiting.behind, waiting.bus, waiting.starts, waiting.words) == (
        ('Westmead', 'Mays Hill'), 0.0, True, '2 stops away, not left yet',
    )


def test_stop_gauge_waits_here_at_a_from_that_starts_the_trip() -> None:
    """A bus waiting at a first-stop From has nothing behind."""
    first = gauge_of(pattern=FIRST, start=0, x=0.0, departed=False)
    assert (first.behind, first.bus, first.starts, first.words) == (
        (), 0.0, True, 'waiting here',
    )


def test_stop_gauge_without_a_bus_shows_no_previous_stops() -> None:
    """With no vehicle there is nothing behind and no badge."""
    alone = gauge_of(x=None)
    assert (alone.behind, alone.bus, alone.words) == ((), None, '')
    assert alone.origin == 'Hollis St'


def test_stop_gauge_calls_skip_passes_and_repeats_with_minutes() -> None:
    """Calls skip repeated names and carry the delayed minutes."""
    assert gauge_of().calls == (
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
    assert gauge_of(end=6).end == 1


def test_stop_gauge_has_no_destination_off_its_own_trip() -> None:
    """An arrival matching no call gives no Destination."""
    hero = replace(HERO, arrives=START + timedelta(minutes=40))
    end = arrival_call(pattern=RUN, departure=hero, start=3, zone=SYDNEY_ZONE)
    assert end is None
    assert gauge_of(end=end).end is None


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
