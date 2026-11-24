"""Tests for catalogue: towards rows."""

from contextlib import closing
from pathlib import Path

from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_transit.build.catalogue import load_catalogue
from tests.build.catalogue.helpers import RouteRow, tiny_db


def test_towards_rows_sum_merged_routes(tmp_path: Path) -> None:
    """A headsign's trips add up, and its busiest sequence is kept."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='9', long_name='A to B', patterns=(
            ('B', ('A', 'B'), 5), ('B', ('A', 'X', 'B'), 2),
            ('A', ('B', 'A'), 1),
        )),
        RouteRow(route_id='r2', number='9', long_name='A to B', agency='X2',
                 patterns=(('B', ('A', 'X', 'B'), 4),)),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        rows = conn.execute(
            'SELECT route_key, headsign, trips, stations FROM chooser_towards '
            'ORDER BY headsign',
        ).fetchall()
    assert rows == [
        ('bus:9:A to B', 'A', 1, '["B", "A"]'),
        ('bus:9:A to B', 'B', 11, '["A", "X", "B"]'),
    ]


def test_towards_rows_merge_headsigns_differing_in_case(
    tmp_path: Path,
) -> None:
    """'B via X' and 'B Via X' make one row under the busier spelling."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='9', long_name='A to B', patterns=(
            ('B Via X', ('A', 'X', 'B'), 3), ('B via X', ('A', 'X', 'B'), 2),
            ('B via X', ('A', 'Y', 'X', 'B'), 2), ('B', ('A', 'B'), 1),
            ('Bz', ('A', 'B'), 1),
        )),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        rows = conn.execute(
            'SELECT headsign, trips, stations FROM chooser_towards '
            'ORDER BY headsign',
        ).fetchall()
    assert rows == [
        ('B', 1, '["A", "B"]'),
        ('B via X', 7, '["A", "X", "B"]'),
        ('Bz', 1, '["A", "B"]'),
    ]


def test_replacement_towards_drop_stand_suffixes(tmp_path: Path) -> None:
    """'Lidcombe Station: Stand C' reads 'Lidcombe Station', merged."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(
            route_id='8a', number='8T6', long_name='Bankstown to Lidcombe',
            route_type=714,
            patterns=(
                ('Lidcombe Station: Stand C', ('Bankstown', 'Lidcombe'), 5),
                ('Lidcombe Station: The Crescent', ('Bankstown', 'L2'), 2),
            ),
        ),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        rows = conn.execute(
            'SELECT headsign, trips FROM chooser_towards',
        ).fetchall()
    assert rows == [('Lidcombe Station', 7)]


def test_bus_towards_drop_stand_suffixes(tmp_path: Path) -> None:
    """A bus's 'Bankstown Central: Stand D' reads 'Bankstown Central'."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='923', long_name='A to B', patterns=(
            ('Bankstown Central: Stand D', ('A', 'B'), 4),
            (': Stand E', ('B', 'A'), 2),
        )),
    ])) as conn:
        load_catalogue(
            conn=conn, projection=SYDNEY_PROJECTION, classify=SYDNEY.classify,
            modes=SYDNEY.modes, rules=SYDNEY.catalogue,
        )
        rows = conn.execute(
            'SELECT headsign, trips FROM chooser_towards ORDER BY headsign',
        ).fetchall()
    assert rows == [(': Stand E', 2), ('Bankstown Central', 4)]
