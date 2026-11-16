"""Tests for grouping stops into stations."""

import pytest

from paperroute_cambridge.build.stations import StationSpec, group_stations
from tests.build.timetable_rows import loaded_stops, parents, stop_row

DRUMMER = StationSpec(
    station_id='cambridge-drummer-street',
    name='Drummer Street',
    member_names=('Drummer St Bus Station', 'Drummer Street'),
    id_prefix='0500CCITY',
)


def test_group_stations_gives_named_stops_one_parent() -> None:
    """Group stations gives named stops one parent."""
    conn = loaded_stops(
        ('0500CCITY227', 'Drummer St Bus Station', 0.1240, 52.2050),
        ('0500CCITY486', 'Drummer Street', 0.1244, 52.2054),
        ('0500CCITY900', 'Emmanuel Street', 0.1230, 52.2040),
        ('390X1', 'Drummer Street', 0.4000, 52.1000),
    )
    assert group_stations(conn=conn, stations=(DRUMMER,)) == 2
    assert parents(conn) == {
        '0500CCITY227': 'cambridge-drummer-street',
        '0500CCITY486': 'cambridge-drummer-street',
        '0500CCITY900': None,
        '390X1': None,
        'cambridge-drummer-street': None,
    }
    name, lon, lat, platform = stop_row(conn, 'cambridge-drummer-street')
    assert (name, platform) == ('Drummer Street', '')
    assert (lon, lat) == pytest.approx((0.1242, 52.2052))


def test_group_stations_skips_a_station_with_no_member(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Group stations skips a station with no member."""
    conn = loaded_stops(('0500CCITY900', 'Emmanuel Street', 0.1230, 52.2040))
    assert group_stations(conn=conn, stations=(DRUMMER,)) == 0
    assert 'cambridge-drummer-street' not in parents(conn)
    assert 'Drummer Street' in caplog.text
