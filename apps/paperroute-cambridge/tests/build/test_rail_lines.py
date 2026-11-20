"""Tests for the lines Cambridge's trains are split into."""

from paperroute_cambridge.build.rail_lines import (
    RAIL_LINES,
    assign_rail_lines,
    line_for,
    rail_line_groups,
    rail_line_sort,
)
from tests.build.timetable_rows import loaded, route_ids


def test_a_trip_takes_its_operators_line_with_most_marks() -> None:
    """GA to Norwich via Stansted is Stansted; to Norwich alone, Norwich."""
    assert line_for(
        badge='GA', stations={'rail:SSD', 'rail:CBG', 'rail:NRW'},
        lines=RAIL_LINES,
    ) == RAIL_LINES[5]
    assert line_for(
        badge='GA', stations={'rail:CBG', 'rail:ELY', 'rail:NRW'},
        lines=RAIL_LINES,
    ) == RAIL_LINES[3]


def test_a_trip_reaching_no_mark_takes_its_operators_first_line() -> None:
    """A Cambridge North shuttle is on Liverpool Street."""
    assert line_for(
        badge='GA', stations={'rail:CBG', 'rail:CMB'}, lines=RAIL_LINES,
    ) == RAIL_LINES[2]


def test_an_undeclared_operator_keeps_its_route() -> None:
    """A badge with no declared line gets None."""
    assert line_for(
        badge='LO', stations={'rail:CBG'}, lines=RAIL_LINES,
    ) is None


def test_assign_rail_lines_splits_a_route_by_line() -> None:
    """Each trip moves to its line's route; an undeclared route stays."""
    conn = loaded(
        stops=[
            ('rail:CBG', 0.1, 52.2),
            ('rail:NRW', 1.3, 52.6),
            ('rail:SSD', 0.2, 51.9),
            ('rail:CBG1', 0.1, 52.2),
        ],
        routes=[('rail:LE', 2, 'LE', 'GA'), ('rail:LO', 2, 'LO', 'LO')],
        trips=[('t1', 'rail:LE'), ('t2', 'rail:LE'), ('t3', 'rail:LO')],
        calls=[
            ('t1', 'rail:CBG1'), ('t1', 'rail:NRW'),
            ('t2', 'rail:SSD'), ('t2', 'rail:CBG1'), ('t2', 'rail:NRW'),
            ('t3', 'rail:CBG1'), ('t3', 'rail:SSD'),
        ],
        parent_of={'rail:CBG1': 'rail:CBG'},
    )
    count = assign_rail_lines(conn=conn, lines=RAIL_LINES, source='bods')
    assert count == 2
    assert conn.execute(
        'SELECT t.trip_id, r.route_id, r.short_name, r.long_name, r.type'
        ' FROM trips t JOIN routes r USING (route_id) ORDER BY t.trip_id',
    ).fetchall() == [
        ('t1', 'rail:LE:Norwich', 'GA', 'Norwich', 2),
        ('t2', 'rail:LE:Stansted Airport', 'GA', 'Stansted Airport', 2),
        ('t3', 'rail:LO', 'LO', '', 2),
    ]
    assert route_ids(conn) == {
        'rail:LE:Norwich', 'rail:LE:Stansted Airport', 'rail:LO',
    }


def test_assign_rail_lines_marks_a_platform_by_its_station() -> None:
    """A call at a platform counts as a call at its parent station."""
    conn = loaded(
        stops=[('rail:IPS', 1.1, 52.0), ('rail:IPS1', 1.1, 52.0)],
        routes=[('rail:LE', 2, 'LE', 'GA')],
        trips=[('t1', 'rail:LE')],
        calls=[('t1', 'rail:IPS1')],
        parent_of={'rail:IPS1': 'rail:IPS'},
    )
    assert assign_rail_lines(conn=conn, lines=RAIL_LINES, source='bods') == 1
    assert route_ids(conn) == {'rail:LE:Ipswich'}


def test_rail_line_sort_and_groups() -> None:
    """Lines rank from 1 in chooser order; badges group by first line."""
    sort = rail_line_sort(lines=RAIL_LINES)
    assert sort['GA:Norwich'] == 4
    assert sort['GA:Ipswich & Peterborough'] == 9
    assert rail_line_groups(lines=RAIL_LINES) == {
        'GN': 0, 'TL': 1, 'GA': 2, 'XC': 3, 'EM': 4,
    }
