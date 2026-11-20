"""The two directions a train leaves a station, and a train for each."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import hypot

from paperroute_transit.core.geo import Point
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.strip_times import (
    TripInputs,
    started_pattern,
    stops_after,
)
from paperroute_transit.core.train_network import (
    TrainNetwork,
    expanded,
    from_origin,
    hop_of,
)


@dataclass(frozen=True)
class Heading:
    """
    A first hop as a vector from the From.

    Attributes
    ----------
    station : str
        The first station after the From.
    trains : int
        Weekday trains that leave that way.
    vector : Point
        Metres east and north from the From to the station.
    """

    station: str
    trains: int
    vector: Point


def first_hops(*, network: TrainNetwork, origin: str) -> Counter[str]:
    """
    Count the trains leaving the From by their first station.

    Parameters
    ----------
    network : TrainNetwork
        The hops and weekday runs.
    origin : str
        The From station ID.

    Returns
    -------
    Counter[str]
        Weekday trains by the station after the From on each run's
        expanded path, passed stations included.
    """
    found: Counter[str] = Counter()
    for run in network.runs:
        after, _ = from_origin(stations=run.stations, origin=origin)
        if after:
            path = expanded(network=network, stations=(origin, *after))
            found[path[1][0]] += run.trains
    return found


def headings(
    *,
    hops: Counter[str],
    origin: str,
    places: Mapping[str, StopPlace],
) -> list[Heading]:
    """
    Turn first hops into vectors from the From.

    Parameters
    ----------
    hops : Counter[str]
        Weekday trains by first station.
    origin : str
        The From station ID.
    places : Mapping[str, StopPlace]
        Stations by ID.

    Returns
    -------
    list[Heading]
        The hops whose station and the From both have a point and are
        apart, busiest first, ties by station ID.
    """
    here = places[origin].point if origin in places else None
    found: list[Heading] = []
    for station, trains in hops.items():
        there = places[station].point if station in places else None
        if here is None or there is None:
            continue
        vector = (there[0] - here[0], there[1] - here[1])
        if hypot(*vector) > 0:
            found.append(
                Heading(station=station, trains=trains, vector=vector),
            )
    return sorted(found, key=lambda head: (-head.trains, head.station))


def north_hops(
    *,
    network: TrainNetwork,
    origin: str,
    places: Mapping[str, StopPlace],
) -> tuple[frozenset[str], frozenset[str]]:
    """
    Split the first stations after the From into two sides.

    Parameters
    ----------
    network : TrainNetwork
        The hops and weekday runs.
    origin : str
        The From station ID.
    places : Mapping[str, StopPlace]
        Stations by ID, with points in metres.

    Returns
    -------
    tuple[frozenset[str], frozenset[str]]
        The first stations above and below. The axis is the busiest hop;
        a hop is on its side when the dot product with it is positive
        and on the other when negative. The side whose trains head
        further north, by the sum of ``trains * dy / |v|``, is above; a
        tie puts the axis side below. With one side, that side is below
        and nothing is above. A hop with no point, or at right angles to
        the axis, is on neither side. With no axis every first station
        is below.
    """
    hops = first_hops(network=network, origin=origin)
    found = headings(hops=hops, origin=origin, places=places)
    if not found:
        return frozenset(), frozenset(hops)
    axis = found[0].vector
    same = [head for head in found if dot(head.vector, axis) > 0]
    other = [head for head in found if dot(head.vector, axis) < 0]
    if not other:
        return frozenset(), frozenset(head.station for head in same)
    if heads_north(heads=other) > heads_north(heads=same):
        return stations_of(heads=other), stations_of(heads=same)
    return stations_of(heads=same), stations_of(heads=other)


def dot(a: Point, b: Point) -> float:
    """
    Take the dot product of two vectors.

    Parameters
    ----------
    a : Point
        The first vector.
    b : Point
        The second vector.

    Returns
    -------
    float
        The dot product.
    """
    return a[0] * b[0] + a[1] * b[1]


def heads_north(*, heads: Sequence[Heading]) -> float:
    """
    Sum how far north a side's trains head.

    Parameters
    ----------
    heads : Sequence[Heading]
        The hops on one side.

    Returns
    -------
    float
        The sum of ``trains * dy / |v|``.
    """
    return sum(
        head.trains * head.vector[1] / hypot(*head.vector) for head in heads
    )


def stations_of(*, heads: Sequence[Heading]) -> frozenset[str]:
    """
    Collect the stations of some hops.

    Parameters
    ----------
    heads : Sequence[Heading]
        The hops.

    Returns
    -------
    frozenset[str]
        Their stations.
    """
    return frozenset(head.station for head in heads)


def first_station(
    *,
    item: LiveDeparture,
    trips: TripInputs,
    origin: str,
    network: TrainNetwork,
) -> str | None:
    """
    Find the first station a train reaches after the From.

    Parameters
    ----------
    item : LiveDeparture
        The departure.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.
    network : TrainNetwork
        The hops, for stations passed before the first call.

    Returns
    -------
    str | None
        The first station passed or called at; None when the train has
        no call after the From.
    """
    call = next(
        (
            station
            for station, _, _ in stops_after(
                item=item, trips=trips, origin=origin,
            )
        ),
        None,
    )
    if call is None:
        return None
    passed = hop_of(network=network, a=origin, b=call).passed
    return passed[0] if passed else call


def side_trains(
    *,
    items: Sequence[LiveDeparture],
    trips: TripInputs,
    picked: int | None,
    sides: tuple[frozenset[str], frozenset[str]],
    origin: str,
    network: TrainNetwork,
) -> tuple[LiveDeparture | None, LiveDeparture | None]:
    """
    Choose the next train each way.

    Parameters
    ----------
    items : Sequence[LiveDeparture]
        The listed departures, soonest first.
    trips : TripInputs
        The strip's loaded patterns and station map.
    picked : int | None
        Index of the picked item, if any.
    sides : tuple[frozenset[str], frozenset[str]]
        First stations above and below, from `north_hops`.
    origin : str
        The From station ID.
    network : TrainNetwork
        The hops, to find a train's first station on its expanded path.

    Returns
    -------
    tuple[LiveDeparture | None, LiveDeparture | None]
        The trains above and below. Each is the first item that is not
        cancelled and whose pattern is loaded and calls at the From. A
        train whose first station is in neither set is below. A picked
        item replaces its side's, as it is, even when cancelled.
    """
    found: list[LiveDeparture | None] = [None, None]
    for item in items:
        if item.cancelled or not started_pattern(
            item=item, trips=trips, origin=origin,
        ):
            continue
        side = side_of(
            item=item, trips=trips, origin=origin, network=network,
            above=sides[0],
        )
        if found[side] is None:
            found[side] = item
    if picked is not None and 0 <= picked < len(items):
        side = side_of(
            item=items[picked], trips=trips, origin=origin, network=network,
            above=sides[0],
        )
        found[side] = items[picked]
    return found[0], found[1]


def side_of(
    *,
    item: LiveDeparture,
    trips: TripInputs,
    origin: str,
    network: TrainNetwork,
    above: frozenset[str],
) -> int:
    """
    Say which side a train leaves by.

    Parameters
    ----------
    item : LiveDeparture
        The departure.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.
    network : TrainNetwork
        The hops, for stations passed before the first call.
    above : frozenset[str]
        First stations on the side above.

    Returns
    -------
    int
        0 for above; 1 for below, including a train whose first station
        is on neither side.
    """
    station = first_station(
        item=item, trips=trips, origin=origin, network=network,
    )
    return 0 if station in above else 1
