"""Tests for laying a strip's loop and spur rows below its From."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import (
    inputs_of,
    line_pattern,
    live,
    train,
)
from paperroute_transit.core.geo import Point
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import (
    ReachInput,
    RowKind,
    RunTail,
    build_reach,
)
from paperroute_transit.core.strip_detours import DetourKind, DetourPart
from paperroute_transit.core.strip_follow import followed_reach, origin_of
from paperroute_transit.core.strip_model import StripInputs
from paperroute_transit.core.strip_view import build_strip_view, trips_of

type Call = tuple[str, str, Point]

LOOP: Final = DetourKind.LOOP
SPUR: Final = DetourKind.SPUR
FROM: Final[Call] = ('C', 'Central', (0.0, -800.0))
QVB: Final[list[Call]] = [
    ('th', 'Town Hall', (0.0, 0.0)),
    ('qvb-d', 'Queen Victoria Building, Stand D', (0.0, 500.0)),
    ('x', 'Park St', (300.0, 800.0)),
    ('y', 'Market St', (600.0, 500.0)),
    ('qvb-b', 'Queen Victoria Building, Stand B', (300.0, 200.0)),
    ('wy', 'Wynyard', (0.0, 1500.0)),
]
MANOR: Final[list[Call]] = [
    ('in', 'Hills Rd', (0.0, -400.0)),
    ('j1', 'Manor Close, Stop A', (0.0, 0.0)),
    ('a1', 'Elm Way', (0.0, 300.0)),
    ('b1', 'Oak Way', (0.0, 600.0)),
    ('turn', 'Manor Close End', (0.0, 900.0)),
    ('b2', 'Oak Lane', (20.0, 600.0)),
    ('a2', 'Museum', (20.0, 300.0)),
    ('j2', 'Manor Close, Stop B', (20.0, 0.0)),
    ('out', 'Mill Rd', (800.0, 0.0)),
]


def bus_inputs(*, calls: list[Call], loaded: bool = True) -> StripInputs:
    """
    Make inputs for a strip from Central with one bus along some calls.

    Parameters
    ----------
    calls : list[Call]
        Station, name and point of each call after Central.
    loaded : bool
        False to leave the bus's pattern unloaded.

    Returns
    -------
    StripInputs
        A bus 'a' leaving Central at 8:01, calling every 3 minutes, its
        rows laid out below the From.
    """
    every = [FROM, *calls]
    places = {
        station: StopPlace(stop_id=station, name=name, point=point)
        for station, name, point in every
    }
    stations = tuple(station for station, _, _ in every)
    reach = build_reach(data=ReachInput(
        origin=places['C'],
        tails=(RunTail(before=(), after=stations[1:], trips=4),),
        places=places,
        station_of={station: station for station in stations},
    ))
    line = line_pattern(trip_id='a', stations=stations, minutes=1)
    stops = tuple(
        replace(stop, name=name, point=point)
        for stop, (_, name, point) in zip(line.stops, every)
    )
    inputs = inputs_of(
        reach=reach,
        departures=[live(item=train(minutes=1, trip_id='a'))],
        patterns={'a': replace(line, stops=stops)} if loaded else {},
    )
    laid = followed_reach(
        reach=reach, trips=trips_of(inputs=inputs), target=None,
    )
    return replace(inputs, reach=laid)


def shown(*, inputs: StripInputs) -> list[tuple[str, str, str]]:
    """
    Read the rows below the From as the strip draws them.

    Parameters
    ----------
    inputs : StripInputs
        The inputs, rows laid out.

    Returns
    -------
    list[tuple[str, str, str]]
        Station, name and time of each row below the From.
    """
    view = build_strip_view(inputs=inputs)
    rows = [row for row in view.rows if row.kind is not RowKind.EXIT]
    start = next(k for k, row in enumerate(rows) if row.station == 'C')
    return [(row.station, row.name, row.time) for row in rows[start + 1:]]


def parts(*, inputs: StripInputs) -> list[DetourPart | None]:
    """
    Read the detour parts of the rows below the From.

    Parameters
    ----------
    inputs : StripInputs
        The inputs, rows laid out.

    Returns
    -------
    list[DetourPart | None]
        Each row's part, in row order.
    """
    reach = inputs.reach
    assert reach is not None
    origin = origin_of(reach=reach)
    assert origin is not None
    return [row.detour for row in reach.rows[origin + 1:]]


def test_followed_reach_keeps_both_stands_of_a_ring() -> None:
    """The QVB stands bracket the ring, its inside reversed."""
    inputs = bus_inputs(calls=QVB)
    assert [(station, time) for station, _, time in shown(
        inputs=inputs,
    )] == [
        ('th', '8:04'), ('qvb-d', '8:07'), ('y', '8:13'), ('x', '8:10'),
        ('qvb-b', '8:16'), ('wy', '8:19'),
    ]
    assert parts(inputs=inputs) == [
        None,
        None,
        DetourPart(kind=LOOP, group=0, slot=1, size=2),
        DetourPart(kind=LOOP, group=0, slot=0, size=2),
        None,
        None,
    ]


def test_followed_reach_folds_a_spur_into_paired_rows() -> None:
    """Each out stop shares a row with its back stop, both timed."""
    inputs = bus_inputs(calls=MANOR)
    assert shown(inputs=inputs) == [
        ('in', 'Hills Rd', '8:04'),
        ('j1', 'Manor Close, Stop', '8:07 / 8:25'),
        ('a1', 'Elm Way / Museum', '8:10 / 8:22'),
        ('b1', 'Oak', '8:13 / 8:19'),
        ('turn', 'Manor Close End', '8:16'),
        ('out', 'Mill Rd', '8:28'),
    ]
    assert parts(inputs=inputs)[1:5] == [
        DetourPart(kind=SPUR, group=0, slot=0, size=3, back='j2'),
        DetourPart(kind=SPUR, group=0, slot=1, size=3, back='a2'),
        DetourPart(kind=SPUR, group=0, slot=2, size=3, back='b2'),
        DetourPart(kind=SPUR, group=0, slot=3, size=3),
    ]


def test_followed_reach_leaves_a_detour_off_a_line_strip() -> None:
    """A strip with no followed trip keeps its fixed rows."""
    inputs = bus_inputs(calls=QVB, loaded=False)
    reach = inputs.reach
    assert reach is not None
    assert reach.trip is None
    assert not any(row.detour for row in reach.rows)
