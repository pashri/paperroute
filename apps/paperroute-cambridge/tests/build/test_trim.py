"""Tests for the Cambridge timetable trim."""

import sqlite3

import pytest

from paperroute_cambridge.area import (
    CAMBRIDGE_PROJECTION,
    COACH_BOX,
    TRIM_RADIUS,
)
from paperroute_cambridge.build.trim import (
    CoachTest,
    TrimReport,
    kept_calls,
    trim_timetable,
)
from tests.build.timetable_rows import (
    BEDFORD,
    HAVERHILL_EDGE,
    MILDENHALL,
    NEAR,
    NORWICH,
    PARKSIDE,
    STANSTED,
    TRUMPINGTON,
    calls_of,
    coach_trip,
    loaded,
    route_ids,
    stop_ids,
)


def no_route(*, route_type: int, agency_id: str | None) -> bool:
    """Answer that no route is a coach."""
    del route_type, agency_id
    return False


def every_route(*, route_type: int, agency_id: str | None) -> bool:
    """Answer that every route is a coach."""
    del route_type, agency_id
    return True


def trim(
    conn: sqlite3.Connection,
    coach: CoachTest = no_route,
) -> TrimReport:
    """Trim with the Cambridge area."""
    return trim_timetable(
        conn=conn,
        projection=CAMBRIDGE_PROJECTION,
        radius=TRIM_RADIUS,
        box=COACH_BOX,
        coach=coach,
    )


@pytest.mark.parametrize(
    ('inside', 'kept'),
    [
        ((False, True, True, False, False), (1, 2, 3)),
        ((True, True), (0, 1)),
        ((False, True, False, True, False), (1, 3, 4)),
        ((False, False), ()),
        ((True,), (0,)),
    ],
)
def test_kept_calls_are_those_inside_and_the_one_after_the_last(
    inside: tuple[bool, ...],
    kept: tuple[int, ...],
) -> None:
    """Kept calls are those inside and the one after the last."""
    assert kept_calls(inside=inside) == kept


def test_trim_timetable_keeps_whole_routes_with_a_stop_within_the_radius() -> (
    None
):
    """Trim timetable keeps whole routes with a stop within the radius."""
    conn = loaded(
        stops=(NEAR, BEDFORD, NORWICH),
        routes=(('bods:1', 3, 'OP1', '1'), ('bods:2', 3, 'OP1', '2')),
        trips=(('t1', 'bods:1'), ('t2', 'bods:2')),
        calls=(
            ('t1', 'near'),
            ('t1', 'bedford'),
            ('t2', 'norwich'),
            ('t2', 'norwich'),
        ),
    )
    report = trim(conn)
    assert route_ids(conn) == {'bods:1'}
    assert calls_of(conn=conn, trip='t1') == ['near', 'bedford']
    assert calls_of(conn=conn, trip='t2') == []
    assert stop_ids(conn) == {'near', 'bedford'}
    assert report == TrimReport(routes=1, dropped=1, cut=0)


def test_trim_timetable_cuts_a_coach_one_call_past_the_box() -> None:
    """Trim timetable cuts a coach one call past the box."""
    conn = coach_trip(STANSTED, TRUMPINGTON, PARKSIDE, MILDENHALL, NORWICH)
    report = trim(conn, coach=every_route)
    assert calls_of(conn=conn, trip='c1') == [
        'trumpington',
        'parkside',
        'mildenhall',
    ]
    assert report.cut == 1
    assert report.dropped == 0


def test_trim_timetable_drops_stops_only_cut_calls_used() -> None:
    """Trim timetable drops stops only cut calls used."""
    conn = coach_trip(STANSTED, TRUMPINGTON, PARKSIDE, MILDENHALL, NORWICH)
    trim(conn, coach=every_route)
    assert stop_ids(conn) == {'trumpington', 'parkside', 'mildenhall'}


def test_trim_timetable_keeps_a_stop_whose_child_is_called_at() -> None:
    """Trim timetable keeps a stop whose child is called at."""
    conn = loaded(
        stops=(
            NEAR,
            BEDFORD,
            ('station', 0.1300, 52.2060),
            ('empty', 0.1300, 52.2060),
        ),
        routes=(('bods:1', 3, 'OP1', '1'),),
        trips=(('t1', 'bods:1'),),
        calls=(('t1', 'near'), ('t1', 'bedford')),
        parent_of={'near': 'station'},
    )
    trim(conn)
    assert stop_ids(conn) == {'near', 'bedford', 'station'}


def test_trim_timetable_skips_a_stop_with_no_coordinates() -> None:
    """Trim timetable skips a stop with no coordinates."""
    conn = loaded(
        stops=(NEAR, BEDFORD, ('nowhere', None, None)),
        routes=(('bods:1', 3, 'OP1', '1'), ('bods:2', 3, 'OP1', '2')),
        trips=(('t1', 'bods:1'), ('t2', 'bods:2')),
        calls=(
            ('t1', 'near'),
            ('t1', 'bedford'),
            ('t2', 'nowhere'),
            ('t2', 'nowhere'),
        ),
    )
    assert trim(conn).routes == 1
    assert route_ids(conn) == {'bods:1'}


def test_trim_timetable_drops_a_coach_trip_left_with_one_call() -> None:
    """Trim timetable drops a coach trip left with one call."""
    conn = coach_trip(STANSTED, PARKSIDE)
    report = trim(conn, coach=every_route)
    assert calls_of(conn=conn, trip='c1') == []
    assert route_ids(conn) == set()
    assert report.dropped == 1
    assert report.cut == 0


def test_trim_timetable_leaves_a_local_trip_outside_the_box_whole() -> None:
    """Trim timetable leaves a local trip outside the box whole."""
    conn = loaded(
        stops=(NEAR, HAVERHILL_EDGE),
        routes=(('bods:1', 3, 'OP1', '1'),),
        trips=(('t1', 'bods:1'),),
        calls=(('t1', 'near'), ('t1', 'edge')),
    )
    assert trim(conn).cut == 0
    assert calls_of(conn=conn, trip='t1') == ['near', 'edge']


def test_trim_timetable_refuses_a_feed_with_no_route_near() -> None:
    """Trim timetable refuses a feed with no route near."""
    conn = loaded(
        stops=(NORWICH,),
        routes=(('bods:2', 3, 'OP1', '2'),),
        trips=(('t2', 'bods:2'),),
        calls=(('t2', 'norwich'), ('t2', 'norwich')),
    )
    with pytest.raises(
        ValueError,
        match='no route within 5000 m of the centre',
    ):
        trim(conn)
    assert route_ids(conn) == {'bods:2'}


def test_trim_timetable_keeps_every_route_of_a_kept_source() -> None:
    """A kept source's routes stay whatever their distance."""
    conn = loaded(
        stops=(NEAR, BEDFORD, NORWICH),
        routes=(('bods:1', 3, 'OP1', '1'), ('bods:2', 3, 'OP1', '2')),
        trips=(('t1', 'bods:1'), ('t2', 'bods:2')),
        calls=(('t1', 'near'), ('t1', 'bedford'), ('t2', 'norwich')),
    )
    report = trim_timetable(
        conn=conn, projection=CAMBRIDGE_PROJECTION, radius=TRIM_RADIUS,
        box=COACH_BOX, coach=no_route, kept_sources=frozenset({'bods'}),
    )
    assert route_ids(conn) == {'bods:1', 'bods:2'}
    assert report == TrimReport(routes=2, dropped=0, cut=0)
    conn.close()
