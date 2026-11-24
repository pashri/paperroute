"""Tests for catalogue: replacement buses."""

from contextlib import closing
from pathlib import Path

from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_transit.build.catalogue import load_catalogue, merged_routes
from tests.build.catalogue.helpers import (
    BY_NUMBER,
    RouteRow,
    chooser_rows,
    tiny_db,
)


def test_replacement_buses_go_under_their_line(tmp_path: Path) -> None:
    """Rail replacement buses keep their line code; others are dropped."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id=number, number=number, long_name=f'{number} bus',
                 route_type=714)
        for number in ('8T1', '42CN', 'EV1')
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        assert sorted(
            row[::3] for row in chooser_rows(conn=conn, kind='replacement')
        ) == [('42CN', 'CCN'), ('8T1', 'T1')]


def test_replacement_buses_are_named_by_their_ends(tmp_path: Path) -> None:
    """A replacement bus reads 'first - last' of its busiest pattern."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(
            route_id='91', number='91T1', long_name='Unknown',
            route_type=714,
            patterns=(
                ('P', ('Lidcombe Station', 'Mid', 'Parramatta Station'), 9),
                ('L', ('Parramatta Station', 'Mid', 'Lidcombe Station'), 5),
                ('L', ('Parramatta Station', 'Lidcombe Station'), 5),
            ),
        ),
        RouteRow(
            route_id='98', number='98T1',
            long_name='Wynyard, then all stations to Hornsby',
            route_type=714,
            patterns=(('H', ('Wynyard Station, York St', 'Hornsby'), 3),),
        ),
        RouteRow(
            route_id='7', number='7T1', long_name='Loop shuttle',
            route_type=714, patterns=(('L', ('Loop', 'Mid', 'Loop'), 3),),
        ),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        names = {
            row[0]: row[1]
            for row in chooser_rows(conn=conn, kind='replacement')
        }
    assert names == {
        '7T1': 'Loop shuttle',
        '91T1': 'Parramatta – Lidcombe',
        '98T1': 'Wynyard – Hornsby',
    }


def test_merged_replacement_takes_the_busiest_long_name(
    tmp_path: Path,
) -> None:
    """Both directions merge under the long name with the most trips."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(
            route_id='8a', number='8T6', long_name='Bankstown to Lidcombe',
            route_type=714,
            patterns=(('L', ('Bankstown Station', 'Lidcombe Station'), 9),),
        ),
        RouteRow(
            route_id='8b', number='8T6', long_name='Lidcombe to Bankstown',
            route_type=714,
            patterns=(('B', ('Lidcombe Station', 'Bankstown Station'), 7),),
        ),
    ])) as conn:
        (route,) = merged_routes(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            by_number=BY_NUMBER,
        )
    assert route.long_name == 'Bankstown to Lidcombe'


def test_replacement_bus_is_one_row_per_route(tmp_path: Path) -> None:
    """Both directions of a replacement bus make one row and two towards."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(
            route_id='8a', number='8T6', long_name='Bankstown to Lidcombe',
            route_type=714,
            patterns=(('L', ('Bankstown Station', 'Lidcombe Station'), 9),),
        ),
        RouteRow(
            route_id='8b', number='8T6', long_name='Lidcombe to Bankstown',
            route_type=714,
            patterns=(('B', ('Lidcombe Station', 'Bankstown Station'), 7),),
        ),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        rows = chooser_rows(conn=conn, kind='replacement')
        towards = conn.execute(
            'SELECT headsign, trips FROM chooser_towards ORDER BY headsign',
        ).fetchall()
    assert [row[:2] for row in rows] == [('8T6', 'Bankstown – Lidcombe')]
    assert towards == [('B', 7), ('L', 9)]
