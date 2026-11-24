"""Tests for network db: choosers."""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.chooser import Choice, ChooserPath, Column, Level
from tests.adapters.network_db.helpers import (
    T1,
    TRAIN,
    add_routes,
    add_towards,
    opened,
)


def test_bus_path_goes_group_route_towards(copy: Path) -> None:
    """Bus opens hundreds groups, then routes, then towards rows."""
    add_routes(path=copy, rows=[
        ('bus:606:x', 'bus', '600s', 600, '606', 1, 'Parramatta', 'Parra',
         ''),
    ])
    bus: ChooserPath = ((Level.KIND, 'bus'),)
    group = (*bus, (Level.GROUP, '800s'))
    with opened(path=copy) as repo:
        groups = repo.choices(path=bus)
        six = repo.choices(path=(*bus, (Level.GROUP, '600s')))
        routes = repo.choices(path=group)
        towards = repo.choices(
            path=(*group, (Level.ROUTE, 'bus:811:Line 811')),
        )
    assert groups == Column(level=Level.GROUP, items=(
        Choice(key='600s', label='600s', detail='1'),
        Choice(key='800s', label='800s', detail='2'),
    ))
    assert six == Column(level=Level.ROUTE, items=(
        Choice(key='bus:606:x', label='Parramatta', badge='606',
               tag='Parra'),
    ))
    assert routes is not None
    assert [(c.key, c.badge) for c in routes.items] == [
        ('bus:809:Line 809', '809'), ('bus:811:Line 811', '811'),
    ]
    assert towards is not None
    assert towards.level is Level.TOWARDS
    assert [(c.label, c.detail) for c in towards.items] == [
        ('Parramatta Stn', '1'),
    ]


@pytest.fixture(name='sydney')
def _sydney(network_db: Path) -> Iterator[SqliteNetworkRepository]:
    """
    A repository over the network timetable for Sydney's own modes.

    Parameters
    ----------
    network_db : Path
        Timetable file.

    Yields
    ------
    SqliteNetworkRepository
        Repository, closed after the test.
    """
    with closing(SqliteNetworkRepository(
        path=network_db,
        city=SYDNEY,
    )) as repo:
        yield repo


def test_the_train_chooser_goes_kind_station_to(
    sydney: SqliteNetworkRepository,
) -> None:
    """A train opens its stations by name, then the To from a station."""
    stations = sydney.choices(path=TRAIN)
    assert stations is not None
    assert stations.level is Level.STATION
    names = [c.label for c in stations.items]
    assert names == sorted(names)
    assert {'C Stop', 'W2 Stop', 'R3 Stop'} <= set(names)
    assert not any(c.heading for c in stations.items)
    assert all(c.place is not None for c in stations.items)
    to = sydney.choices(path=(*TRAIN, (Level.STATION, 'W2')))
    assert to is not None
    assert to.level is Level.TO
    assert (to.items[0].key, to.items[0].tag) == ('W2', 'ALL')
    rest = [c.key for c in to.items[1:]]
    assert 'W2' not in rest
    assert 'C' in rest
    assert {c.key for c in stations.items} >= set(rest)
    assert [c.label for c in to.items[1:]] == sorted(
        c.label for c in to.items[1:]
    )


def test_the_metro_chooser_keeps_its_line_step(
    copy: Path,
) -> None:
    """A mode chosen by line without end points still asks for its line."""
    add_routes(path=copy, rows=[
        ('metro:M1', 'metro', '', 0, 'M1', 1, 'Metro', '', ''),
    ])
    metro: ChooserPath = ((Level.KIND, 'metro'),)
    with closing(SqliteNetworkRepository(
        path=copy,
        city=SYDNEY,
    )) as repo:
        lines = repo.choices(path=metro)
        stations = repo.next_column(path=(*metro, (Level.LINE, 'metro:M1')))
    assert lines is not None
    assert lines.level is Level.LINE
    assert stations is not None
    assert stations.level is Level.STATION


def test_train_lines_rule_off_intercity(copy: Path) -> None:
    """Lines list in order; a rule sits above the first intercity line."""
    add_routes(path=copy, rows=[
        ('train:BMT', 'train', '', 0, 'BMT', 101, 'Blue Mountains', '', ''),
        ('train:CCN', 'train', '', 0, 'CCN', 102, 'Central Coast', '', ''),
        ('ferry:F1', 'ferry', '', 0, 'F1', 1, 'Manly', '', ''),
    ])
    with opened(path=copy) as repo:
        lines = repo.choices(path=TRAIN)
        ferries = repo.choices(path=((Level.KIND, 'ferry'),))
    assert lines is not None
    assert lines.level is Level.LINE
    assert [(c.badge, c.label, c.rule_above) for c in lines.items] == [
        ('T1', 'Line T1', False), ('T2', 'Line T2', False),
        ('BMT', 'Blue Mountains', True), ('CCN', 'Central Coast', False),
    ]
    assert ferries == Column(level=Level.LINE, items=(
        Choice(key='ferry:F1', label='Manly', badge='F1'),
    ))


def test_towards_busiest_first(copy: Path) -> None:
    """A route's towards rows run from the most trips to the fewest."""
    for headsign, trips in (('Few', 1), ('Most', 9), ('Some', 4)):
        add_towards(
            path=copy, route_key='bus:811:Line 811', headsign=headsign,
            trips=trips,
        )
    with opened(path=copy) as repo:
        column = repo.choices(path=(
            (Level.KIND, 'bus'), (Level.GROUP, '800s'),
            (Level.ROUTE, 'bus:811:Line 811'),
        ))
    assert column is not None
    assert column.level is Level.TOWARDS
    assert [(c.label, c.detail) for c in column.items] == [
        ('Most', '9'), ('Some', '4'), ('Parramatta Stn', '1'), ('Few', '1'),
    ]


def test_line_column_for_train_has_no_towards(
    network: SqliteNetworkRepository,
) -> None:
    """A train line opens its stations in blocks, R3 first."""
    column = network.choices(path=T1)
    assert column is not None
    assert column.level is Level.STATION
    first, station = column.items[:2]
    assert first == Choice(key='', label='To R3 Stop', heading=True)
    assert (station.key, station.label) == ('R3', 'R3 Stop')
    assert station.place is not None
    assert station.place.point is not None
    rows = [(c.key, c.label, c.heading) for c in column.items]
    assert rows == [
        ('', 'To R3 Stop', True), ('R3', 'R3 Stop', False),
        ('R2', 'R2 Stop', False), ('R1', 'R1 Stop', False),
        ('J', 'J Stop', False), ('', 'To E2 Stop', True),
        ('C', 'C Stop', False), ('E1', 'E1 Stop', False),
        ('E2', 'E2 Stop', False), ('', 'To W2 Stop', True),
        ('W1', 'W1 Stop', False), ('W2', 'W2 Stop', False),
    ]
