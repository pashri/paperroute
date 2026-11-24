"""Tests for a strip's ring when the From is on a loop."""

from paperroute_testkit.core.factories import EIGHT, pattern
from paperroute_transit.core.patterns import PatternStop
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_detours import DetourKind
from paperroute_transit.core.strip_from_loop import from_loop
from paperroute_transit.core.strip_model import ROW_H, STRIP_TOP, StripRow
from paperroute_transit.core.strip_rings import ring_of

CALLS = (
    ('SX', 'Sussex St After King St', (0.0, 0.0)),
    ('QD', 'QVB, York St, Stand D', (245.0, -166.0)),
    ('TH', 'Town Hall, Park St, Stand H', (374.0, -361.0)),
    ('HP', 'Hyde Park, Park St, Stand B', (682.0, -395.0)),
    ('AR', 'Art Gallery Rd Opp Art Gallery Of NSW', (1307.0, 308.0)),
    ('PL', 'Andrew (Boy) Charlton Pool, Mrs Macquaries Rd', (1605.0, 684.0)),
    ('AG', 'Art Gallery Of NSW, Art Gallery Rd', (1193.0, 134.0)),
    ('QB', 'QVB, York St, Stand B', (252.0, -275.0)),
    ('RC', 'Robert St At Crescent St', (-2603.0, 374.0)),
)  # Sydney 441, pinned at Hyde Park


def rows_441() -> list[StripRow]:
    """
    Lay the 441 out in calling order, the From at Hyde Park.

    Returns
    -------
    list[StripRow]
        One row a call, top down.
    """
    return [
        StripRow(
            name=name, depth=0,
            kind=RowKind.ORIGIN if station == 'HP' else RowKind.MAIN,
            y=STRIP_TOP + ROW_H * (index + 0.5), parent_y=None,
            parent_depth=0, tag='', time=f'8:{index:02}', ride=False,
            cursor=False, bold=station == 'HP', station=station,
        )
        for index, (station, name, _) in enumerate(CALLS)
    ]


def test_from_loop_rings_a_loop_round_the_from() -> None:
    """The From inside the loop gives one ring group over both sides."""
    trip = pattern(stops=tuple(
        PatternStop(
            arrives=EIGHT + 60 * index, departs=EIGHT + 60 * index,
            point=point, station=station, name=name,
        )
        for index, (station, name, point) in enumerate(CALLS)
    ))
    rows = from_loop(rows=rows_441(), pattern=trip, station_of={})
    order = [row.station for row in rows]
    assert order == ['SX', 'QD', 'AG', 'PL', 'AR', 'HP', 'TH', 'QB', 'RC']
    groups = {
        row.detour.group
        for row in rows
        if row.detour is not None and row.detour.kind is DetourKind.LOOP
    }
    assert len(groups) == 1
    (group,) = groups
    assert ring_of(rows=rows, group=group, track_x=30) is not None
    assert [row.y for row in rows] == [row.y for row in rows_441()]
    assert rows[5].bold and rows[5].time == '8:03'


def test_from_loop_leaves_rows_without_a_loop() -> None:
    """With no loop round the From the rows are unchanged."""
    trip = pattern(stops=tuple(
        PatternStop(
            arrives=EIGHT, departs=EIGHT, point=point, station=station,
            name=name,
        )
        for station, name, point in CALLS[:4]
    ))
    rows = rows_441()
    assert from_loop(rows=rows, pattern=trip, station_of={}) == tuple(rows)
