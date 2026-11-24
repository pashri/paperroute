"""Tests for the two sides of a train Strip with no Destination."""

from datetime import timedelta

from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    live,
    pattern,
)
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.strip_times import TripInputs
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun
from paperroute_transit.core.train_sides import north_hops, side_trains
from tests.core.builders import trip_inputs

NORTH = frozenset({'N'})
SOUTH = frozenset({'S'})


def place_of(*, stop_id: str, point: tuple[float, float] | None) -> StopPlace:
    """
    Make a station at a point in metres.

    Parameters
    ----------
    stop_id : str
        Station ID, also used as the name.
    point : tuple[float, float] | None
        Metres east and north, or None.

    Returns
    -------
    StopPlace
        The station.
    """
    return StopPlace(stop_id=stop_id, name=stop_id, point=point)


def places_of(
    *, points: dict[str, tuple[float, float] | None],
) -> dict[str, StopPlace]:
    """
    Make stations for a From at the origin and others at points.

    Parameters
    ----------
    points : dict[str, tuple[float, float] | None]
        Points by station ID.

    Returns
    -------
    dict[str, StopPlace]
        The From at C (0, 0) and the stations given.
    """
    found = {'C': place_of(stop_id='C', point=(0.0, 0.0))}
    found.update({
        key: place_of(stop_id=key, point=point)
        for key, point in points.items()
    })
    return found


def run_of(*, stations: tuple[str, ...], trains: int) -> WeekdayRun:
    """
    Make a weekday run.

    Parameters
    ----------
    stations : tuple[str, ...]
        Station IDs called at.
    trains : int
        Trains a weekday.

    Returns
    -------
    WeekdayRun
        The run.
    """
    return WeekdayRun(route='R', stations=stations, trains=trains)


def hops_of(
    *,
    runs: tuple[WeekdayRun, ...],
    hops: dict[tuple[str, str], Hop] | None = None,
    points: dict[str, tuple[float, float] | None],
) -> tuple[frozenset[str], frozenset[str]]:
    """
    Work out the sides of a From at C.

    Parameters
    ----------
    runs : tuple[WeekdayRun, ...]
        The runs through C.
    hops : dict[tuple[str, str], Hop] | None
        Hops by their two stations.
    points : dict[str, tuple[float, float] | None]
        Points by station ID.

    Returns
    -------
    tuple[frozenset[str], frozenset[str]]
        First-hop stations above and below.
    """
    return north_hops(
        network=TrainNetwork(hops=hops or {}, runs=runs),
        origin='C',
        places=places_of(points=points),
    )


def test_the_side_heading_north_goes_above() -> None:
    """The north hop is above although the south one is busier."""
    found = hops_of(
        runs=(
            run_of(stations=('C', 'N'), trains=3),
            run_of(stations=('C', 'S'), trains=9),
        ),
        points={'N': (0.0, 1000.0), 'S': (0.0, -1000.0)},
    )
    assert found == (NORTH, SOUTH)


def test_a_tie_in_the_north_sum_puts_the_busiest_side_below() -> None:
    """Hops due east and due west: the busier one is below."""
    found = hops_of(
        runs=(
            run_of(stations=('C', 'W'), trains=3),
            run_of(stations=('C', 'E'), trains=9),
        ),
        points={'E': (1000.0, 0.0), 'W': (-1000.0, 0.0)},
    )
    assert found == (frozenset({'W'}), frozenset({'E'}))


def test_a_terminus_has_one_side_below() -> None:
    """With hops on one side only, nothing is above."""
    found = hops_of(
        runs=(
            run_of(stations=('C', 'N'), trains=3),
            run_of(stations=('C', 'M'), trains=1),
        ),
        points={'N': (0.0, 1000.0), 'M': (100.0, 900.0)},
    )
    assert found == (frozenset(), frozenset({'N', 'M'}))


def test_a_hop_with_no_point_takes_no_side() -> None:
    """A first hop whose place has no point is on neither side."""
    found = hops_of(
        runs=(
            run_of(stations=('C', 'N'), trains=3),
            run_of(stations=('C', 'S'), trains=2),
            run_of(stations=('C', 'X'), trains=1),
        ),
        points={'N': (0.0, 1000.0), 'S': (0.0, -1000.0), 'X': None},
    )
    assert found == (NORTH, SOUTH)


def test_no_point_for_the_from_gives_one_side_below() -> None:
    """With the From unplaced there is no axis and everything is below."""
    network = TrainNetwork(
        hops={}, runs=(run_of(stations=('C', 'N'), trains=1),),
    )
    found = north_hops(
        network=network,
        origin='C',
        places={'N': place_of(stop_id='N', point=(0.0, 5.0))},
    )
    assert found == (frozenset(), frozenset({'N'}))


def test_the_busiest_hop_tie_is_broken_by_station_id() -> None:
    """Equal busiest hops give the same sides whatever the run order."""
    points: dict[str, tuple[float, float] | None] = {
        'N': (0.0, 1000.0), 'S': (0.0, -1000.0),
    }
    one = hops_of(
        runs=(
            run_of(stations=('C', 'S'), trains=4),
            run_of(stations=('C', 'N'), trains=4),
        ),
        points=points,
    )
    two = hops_of(
        runs=(
            run_of(stations=('C', 'N'), trains=4),
            run_of(stations=('C', 'S'), trains=4),
        ),
        points=points,
    )
    assert one == two == (NORTH, SOUTH)


def test_a_passed_first_station_is_the_first_hop() -> None:
    """The station after the From on the expanded path is the hop."""
    found = hops_of(
        runs=(
            run_of(stations=('C', 'F'), trains=2),
            run_of(stations=('C', 'S'), trains=1),
        ),
        hops={('C', 'F'): Hop(passed=('N',), inside=True)},
        points={'N': (0.0, 500.0), 'F': (0.0, 2000.0), 'S': (0.0, -500.0)},
    )
    assert found == (NORTH, SOUTH)


def trips_of(*, trips: dict[str, tuple[str, ...]]) -> TripInputs:
    """
    Make inputs with one departure per trip, each calling at C at 8:10.

    Parameters
    ----------
    trips : dict[str, tuple[str, ...]]
        Station IDs called at, by trip ID, in order.

    Returns
    -------
    TripInputs
        The inputs, at 8:00, with departures in the order given.
    """
    patterns: dict[str, TripPattern | None] = {
        trip_id: pattern(
            trip_id=trip_id,
            stops=calls(
                stations=stations,
                start=EIGHT + 600 - 300 * stations.index('C'),
                step=300,
            ),
        )
        for trip_id, stations in trips.items()
    }
    return trip_inputs(
        departures=tuple(
            live(item=departure(
                departs=START + timedelta(minutes=10), trip_id=trip_id,
            ))
            for trip_id in trips
        ),
        patterns=patterns,
    )


def sides_of(
    *,
    trips: TripInputs,
    picked: int | None = None,
    hops: dict[tuple[str, str], Hop] | None = None,
) -> tuple[LiveDeparture | None, LiveDeparture | None]:
    """
    Choose the next train each way for sides N above and S below.

    Parameters
    ----------
    trips : TripInputs
        The inputs; their departures are the listed items.
    picked : int | None
        Index of the picked item.
    hops : dict[tuple[str, str], Hop] | None
        Hops by their two stations.

    Returns
    -------
    tuple[LiveDeparture | None, LiveDeparture | None]
        The trains above and below.
    """
    return side_trains(
        items=trips.departures,
        trips=trips,
        picked=picked,
        sides=(NORTH, SOUTH),
        origin='C',
        network=TrainNetwork(hops=hops or {}, runs=()),
    )


def test_the_next_train_each_way_is_found() -> None:
    """The first train on each side is chosen."""
    trips = sides_trips()
    above, below = sides_of(trips=trips)
    assert above is trips.departures[1]
    assert below is trips.departures[0]


def sides_trips() -> TripInputs:
    """
    Make trains south, north, south.

    Returns
    -------
    TripInputs
        Three departures with trip IDs ``s1``, ``n1`` and ``s2``.
    """
    return trips_of(trips={
        's1': ('C', 'S'), 'n1': ('C', 'N'), 's2': ('C', 'S'),
    })


def test_a_picked_train_replaces_its_own_side() -> None:
    """Picking the later south train leaves the north train as it was."""
    trips = sides_trips()
    above, below = sides_of(trips=trips, picked=2)
    assert above is trips.departures[1]
    assert below is trips.departures[2]


def test_a_picked_train_finds_its_side_from_a_passed_station() -> None:
    """A train whose first call is past a passed station goes by it."""
    trips = trips_of(trips={'s1': ('C', 'S'), 'f1': ('C', 'F')})
    above, below = sides_of(
        trips=trips,
        picked=1,
        hops={('C', 'F'): Hop(passed=('N',), inside=True)},
    )
    assert above is trips.departures[1]
    assert below is trips.departures[0]


def test_a_train_on_neither_side_falls_below() -> None:
    """A train heading to an unplaced station is the one below."""
    trips = trips_of(trips={'x1': ('C', 'X'), 'n1': ('C', 'N')})
    above, below = sides_of(trips=trips)
    assert above is trips.departures[1]
    assert below is trips.departures[0]


def test_a_cancelled_or_unloaded_train_is_skipped() -> None:
    """Only a live train with a loaded pattern is chosen."""
    trips = trips_of(trips={'a': ('C', 'N'), 'b': ('C', 'N')})
    items = [
        live(item=trips.departures[0].departure, cancelled=True),
        trips.departures[1],
    ]
    found = side_trains(
        items=items,
        trips=trips,
        picked=None,
        sides=(NORTH, SOUTH),
        origin='C',
        network=TrainNetwork(hops={}, runs=()),
    )
    assert found == (items[1], None)
