"""Tests for the route chooser catalogue."""

import json
import logging
import sqlite3
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute.build.catalogue import (
    CATALOGUE_SCHEMA,
    Entry,
    bucket_of,
    bucket_order,
    kind_rows,
    line_name,
    load_catalogue,
    merged_routes,
    route_order,
)
from paperroute.build.gtfs_files import Source
from paperroute.build.route_tags import (
    CENTRES,
    TAG_MAX,
    CatalogueRoute,
    agency_towns,
    short_tag,
    tags_of,
)
from paperroute.build.runs import RUN_SCHEMA
from paperroute.build.timetable import Window, build_timetable, create_schema
from paperroute.core.geo import Point, to_metres
from paperroute.core.model import RouteKind
from tests.build.network_feed import EAST_WEST, network_feed

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'
LOGGER: Final[str] = 'paperroute.build.route_tags'
FAR: Final[Point] = to_metres(lon=146.0, lat=-34.3)
PLACES: Final[tuple[str, ...]] = (
    'Alpha', 'Bravo', 'Charlie', 'Delta Heights', 'Campbelltown North',
    'Campbelltown South', 'City Martin Place', 'City Wynyard',
)


@dataclass(frozen=True)
class RouteRow:
    """One routes row and its route_patterns rows for a tiny timetable."""

    route_id: str
    number: str
    long_name: str
    route_type: int = 700
    agency: str | None = 'A1'
    source: str = 'bundle'
    patterns: tuple[tuple[str, tuple[str, ...], int], ...] = (
        ('B', ('S1', 'S2'), 1),
    )


def centre(*, label: str) -> Point:
    """
    Give the point of one of the CENTRES.

    Parameters
    ----------
    label : str
        Centre label.

    Returns
    -------
    Point
        Metres east and north.
    """
    lon, lat = next(
        (lon, lat) for name, lon, lat, _ in CENTRES if name == label
    )
    return to_metres(lon=lon, lat=lat)


def bus(
    *, number: str, long_name: str, middle: Point | None = FAR,
    agency: str = 'A1',
) -> CatalogueRoute:
    """
    Build a merged bus route.

    Parameters
    ----------
    number : str
        Route number.
    long_name : str
        Long name.
    middle : Point | None
        Median station point.
    agency : str
        Operator.

    Returns
    -------
    CatalogueRoute
        Route.
    """
    return CatalogueRoute(
        route_key=f'bus:{number}:{long_name}', kind=RouteKind.BUS,
        number=number, long_name=long_name, agency=agency, middle=middle,
    )


def tiny_db(*, path: Path, routes: Sequence[RouteRow]) -> sqlite3.Connection:
    """
    Write a timetable holding only stops, routes and route_patterns.

    Parameters
    ----------
    path : Path
        Database file.
    routes : Sequence[RouteRow]
        Routes and their patterns; every station named sits near Sydney.

    Returns
    -------
    sqlite3.Connection
        Open connection.
    """
    conn = sqlite3.connect(path)
    create_schema(conn=conn)
    conn.executescript(RUN_SCHEMA)
    conn.executescript(CATALOGUE_SCHEMA)
    stations = {
        station
        for route in routes
        for _, sequence, _ in route.patterns
        for station in sequence
    }
    conn.executemany(
        "INSERT INTO stops VALUES (?, ?, NULL, -33.87, 151.2, '')",
        ((station, station) for station in sorted(stations)),
    )
    conn.executemany(
        'INSERT INTO routes VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)',
        (
            (r.route_id, r.number, r.long_name, r.route_type, r.agency,
             r.source)
            for r in routes
        ),
    )
    conn.executemany(
        'INSERT INTO route_patterns VALUES (?, ?, ?, ?)',
        (
            (r.route_id, headsign, json.dumps(sequence), trips)
            for r in routes
            for headsign, sequence, trips in r.patterns
        ),
    )
    return conn


def chooser_rows(*, conn: sqlite3.Connection, kind: str
                 ) -> list[tuple[str, ...]]:
    """
    Read one kind's chooser rows in sort order.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the catalogue.
    kind : str
        Route kind.

    Returns
    -------
    list[tuple[str, ...]]
        Number, name, tag and line of each row.
    """
    return conn.execute(
        'SELECT number, name, tag, line FROM chooser_routes '
        'WHERE kind = ? ORDER BY sort', (kind,),
    ).fetchall()


class Collected(logging.Handler):
    """A log handler that keeps each message."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.found: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """
        Keep one record's message.

        Parameters
        ----------
        record : logging.LogRecord
            Record.
        """
        self.found.append(record.getMessage())


@contextmanager
def warnings_logged() -> Iterator[list[str]]:
    """
    Collect the catalogue's warnings.

    Yields
    ------
    list[str]
        Messages, filled as they are logged.
    """
    handler = Collected()
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    try:
        yield handler.found
    finally:
        logger.removeHandler(handler)


def test_bucket_of() -> None:
    """Numbers fall in hundreds; lettered routes go by their letters."""
    assert bucket_of(number='7') == '1\u201399'
    assert bucket_of(number='606') == '600s'
    assert bucket_of(number='5300') == '5300s'
    assert bucket_of(number='N10') == 'N'
    assert bucket_of(number='BN1') == 'BN'


def test_bucket_order_puts_numbers_before_letters() -> None:
    """Hundreds come in number order, then lettered groups by letter."""
    buckets = ['N', '5300s', 'BN', '100s', 'B', '1\u201399', '600s']
    assert sorted(buckets, key=lambda name: bucket_order(bucket=name)) == [
        '1\u201399', '100s', '600s', '5300s', 'B', 'BN', 'N',
    ]


def test_route_order_is_number_order() -> None:
    """Numbered names sort by number, then the rest in natural order."""
    names = ['N100', '10', '607X', '1', 'N20', '606', 'S1', 'N10', '100']
    assert sorted(names, key=lambda name: route_order(number=name)) == [
        '1', '10', '100', '606', '607X', 'N10', 'N20', 'N100', 'S1',
    ]


def test_line_name() -> None:
    """The code, 'Line' and 'and' are tidied away."""
    assert line_name(
        long_name='T1 North Shore and Western Line', code='T1',
    ) == 'North Shore & Western'
    assert line_name(long_name='Hunter Line', code='HUN') == 'Hunter'


def test_repeated_numbers_get_place_tags() -> None:
    """Two route 10s in different centres are tagged with the centres."""
    south = bus(number='10', long_name='Fairy Meadow to Wollongong',
                middle=centre(label='Wollongong'))
    north = bus(number='10', long_name='Wyong to Tuggerah',
                middle=centre(label='C Coast'))
    assert tags_of(routes=[south, north], towns={}) == {
        south.route_key: 'Wollongong', north.route_key: 'C Coast',
    }


def test_clashing_tags_fall_back_to_first_place() -> None:
    """Two 600s with the same operator town take their first places."""
    one = bus(number='600', long_name='Leeton to Griffith')
    two = bus(number='600', long_name='Narrandera to Griffith')
    tags = tags_of(routes=[one, two], towns={'A1': 'Griffith'})
    assert tags == {one.route_key: 'Leeton', two.route_key: 'Narrandera'}


def test_unknown_town_is_never_a_tag() -> None:
    """A route with no centre and no operator town takes its first place."""
    known = bus(number='70', long_name='Coledale to Wollongong',
                middle=centre(label='Wollongong'))
    unknown = bus(number='70', long_name='Leeton to Griffith', agency='Z9')
    tags = tags_of(routes=[known, unknown], towns={})
    assert tags == {known.route_key: 'Wollongong', unknown.route_key: 'Leeton'}


def test_lone_leftover_takes_its_number_without_a_warning() -> None:
    """One route with no usable value or word is tagged with its number."""
    known = bus(number='70', long_name='Coledale to Wollongong',
                middle=centre(label='Wollongong'))
    blank = bus(number='70', long_name='', agency='Z9')
    with warnings_logged() as found:
        tags = tags_of(routes=[known, blank], towns={})
    assert tags == {known.route_key: 'Wollongong', blank.route_key: '70'}
    assert not found


def test_agency_ties_go_to_the_lowest_id(tmp_path: Path) -> None:
    """Operators running equal trips of a route give way to the lowest ID."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='78', long_name='A to B',
                 agency='X2', patterns=(('B', ('S1', 'S2'), 4),)),
        RouteRow(route_id='r2', number='78', long_name='A to B',
                 agency='X1', patterns=(('B', ('S1', 'S3'), 4),)),
    ])) as conn:
        (route,) = merged_routes(conn=conn)
    assert route.agency == 'X1'


def test_identical_long_names_merge_into_one_untagged_row(
    tmp_path: Path,
) -> None:
    """Two operators' route 78 of one long name make one untagged row."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='r1', number='78', long_name='A to B',
                 agency='X1', patterns=(('B', ('S1', 'S2'), 3),)),
        RouteRow(route_id='r2', number='78', long_name='A to B',
                 agency='X2', patterns=(('B', ('S1', 'S3'), 5),)),
    ])) as conn:
        (route,) = merged_routes(conn=conn)
        assert route.agency == 'X2'
        assert route.route_key == 'bus:78:A to B'
        assert load_catalogue(conn=conn) == 1
        assert chooser_rows(conn=conn, kind='bus') == [
            ('78', 'A to B', '', ''),
        ]


def test_merged_route_middle_is_the_median_station(tmp_path: Path) -> None:
    """The middle is the median of every merged route's stations."""
    routes = [
        RouteRow(route_id='r1', number='5', long_name='A to B',
                 patterns=(('B', ('S1', 'S2'), 1),)),
        RouteRow(route_id='r2', number='5', long_name='A to B',
                 patterns=(('B', ('S2', 'S3'), 1),)),
    ]
    with closing(tiny_db(path=tmp_path / 't.db', routes=routes)) as conn:
        conn.executemany(
            'UPDATE stops SET lon = ?, lat = ? WHERE stop_id = ?',
            [(150.0, -33.0, 'S1'), (151.0, -34.0, 'S2'),
             (152.5, -35.0, 'S3')],
        )
        (route,) = merged_routes(conn=conn)
    assert route.middle == pytest.approx(to_metres(lon=151.0, lat=-34.0))


def test_tags_fall_through_to_last_place() -> None:
    """Routes with one region and first place take their last places."""
    one = bus(number='21', long_name='Lake Haven to Tuggerah',
              middle=centre(label='C Coast'))
    two = bus(number='21', long_name='Lake Haven to Wyong',
              middle=centre(label='C Coast'))
    tags = tags_of(routes=[one, two], towns={})
    assert tags == {one.route_key: 'Tuggerah', two.route_key: 'Wyong'}


def test_tags_fall_through_to_via_place() -> None:
    """Routes with one region and both ends take their via places."""
    one = bus(number='30', long_name='A to B via C')
    two = bus(number='30', long_name='A to B via D')
    tags = tags_of(routes=[one, two], towns={'A1': 'A'})
    assert tags == {one.route_key: 'C', two.route_key: 'D'}


def test_short_tag_keeps_cut_names_distinct() -> None:
    """Cut names that would read the same keep their ends."""
    north = short_tag(place='Campbelltown North', other='Campbelltown South')
    south = short_tag(place='Campbelltown South', other='Campbelltown North')
    assert (north, south) == ('Camp.North', 'Camp.South')
    assert max(len(north), len(south)) <= TAG_MAX
    assert short_tag(place='City Town Hall') == 'City Town.'
    assert short_tag(place='Campbelltown') == 'Campbelltn'


def test_surviving_clash_falls_back_to_first_differing_word() -> None:
    """Routes alike at every level take the word that tells them apart."""
    one = bus(number='40', long_name='A to B via C (Express)')
    two = bus(number='40', long_name='A to B via C (Limited)')
    with warnings_logged() as found:
        tags = tags_of(routes=[one, two], towns={'A1': 'A'})
    assert tags == {one.route_key: 'Express', two.route_key: 'Limited'}
    assert len(found) == 1
    assert one.route_key in found[0]
    assert two.route_key in found[0]


def test_indistinguishable_routes_share_a_tag_with_a_warning() -> None:
    """Long names differing only in spacing share a tag and a warning."""
    one = bus(number='50', long_name='A to  B')
    two = bus(number='50', long_name='A to B')
    with warnings_logged() as found:
        tags = tags_of(routes=[one, two], towns={'A1': 'A'})
    assert tags[one.route_key] == tags[two.route_key] != ''
    assert len(found) == 1


def test_warning_names_every_route_sharing_a_tag() -> None:
    """A word tag that falls back onto a level's tag names both routes."""
    routes = [
        bus(number='10', long_name=name)
        for name in (
            'Alpha to Alpha', 'Alpha to Bravo', 'Alpha to Charlie',
            'Bravo to Alpha', 'Charlie to Alpha',
        )
    ]
    with warnings_logged() as found:
        tags = tags_of(routes=routes, towns={'A1': 'Alpha'})
    shared = [
        key
        for key, tag in tags.items()
        if Counter(tags.values())[tag] > 1
    ]
    assert shared
    assert len(found) == 1
    assert all(key in found[0] for key in shared)


@st.composite
def route_groups(draw: st.DrawFn) -> list[CatalogueRoute]:
    """
    Draw route 10s with distinct long names built from place names.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    list[CatalogueRoute]
        Routes sharing a number.
    """
    places = st.sampled_from(PLACES)
    names = draw(st.lists(
        st.builds(
            lambda first, last, via: (
                f'{first} to {last}' + (f' via {via}' if via else '')
            ),
            places, places, st.none() | places,
        ),
        min_size=2, max_size=6, unique=True,
    ))
    points = st.none() | st.sampled_from([
        FAR, centre(label='Wollongong'), centre(label='C Coast'),
    ])
    return [
        bus(number='10', long_name=name, middle=draw(points),
            agency=draw(st.sampled_from(['A1', 'A2', 'Unknown'])))
        for name in names
    ]


@given(routes=route_groups())
def test_tags_always_distinct(routes: list[CatalogueRoute]) -> None:
    """Tags are short and distinct, and any shared tag is warned about."""
    towns = agency_towns(routes=[
        route for route in routes if route.agency != 'Unknown'
    ])
    with warnings_logged() as found:
        tags = tags_of(routes=routes, towns=towns)
    assert set(tags) == {route.route_key for route in routes}
    for tag in tags.values():
        assert 0 < len(tag) <= TAG_MAX
        assert not tag[-1].isdigit()
    shared = [key for key, tag in tags.items()
              if Counter(tags.values())[tag] > 1]
    assert all(any(key in line for line in found) for key in shared)
    assert len(found) <= 1


@pytest.mark.skipif(not REAL_DB.exists(), reason='no real timetable')
def test_real_catalogue_tags_are_distinct() -> None:
    """Every repeated Bus and School number has distinct, non-empty tags."""
    with closing(sqlite3.connect(REAL_DB)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'chooser_routes'",
        ).fetchone():
            pytest.skip('real timetable has no catalogue yet')
        rows = conn.execute(
            "SELECT kind, number, tag FROM chooser_routes "
            "WHERE kind IN ('bus', 'school')",
        ).fetchall()
    groups: dict[tuple[str, str], list[str]] = {}
    for kind, number, tag in rows:
        groups.setdefault((kind, number), []).append(tag)
    repeated = [tags for tags in groups.values() if len(tags) > 1]
    assert repeated
    for tags in repeated:
        assert all(tags), tags
        assert len(set(tags)) == len(tags), tags


def test_replacement_buses_go_under_their_line(tmp_path: Path) -> None:
    """Rail replacement buses keep their line code; others are dropped."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id=number, number=number, long_name=f'{number} bus',
                 route_type=714)
        for number in ('8T1', '42CN', 'EV1')
    ])) as conn:
        load_catalogue(conn=conn)
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
        load_catalogue(conn=conn)
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
        (route,) = merged_routes(conn=conn)
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
        load_catalogue(conn=conn)
        rows = chooser_rows(conn=conn, kind='replacement')
        towards = conn.execute(
            'SELECT headsign, trips FROM chooser_towards ORDER BY headsign',
        ).fetchall()
    assert [row[:2] for row in rows] == [('8T6', 'Bankstown – Lidcombe')]
    assert towards == [('B', 7), ('L', 9)]


def test_train_lines_in_decision_order(tmp_path: Path) -> None:
    """Sydney lines go T1 to T9, then the intercity lines in order."""
    codes = ['HUN', 'T9', 'SHL', 'T1', 'CCN', 'T5', 'SCO', 'T2', 'BMT']
    routes = [
        RouteRow(route_id=f'st{code}', number=code,
                 long_name=f'City to {code}', route_type=2,
                 agency='NSWTrains', source='sydneytrains')
        for code in codes
    ]
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        *routes,
        RouteRow(route_id='stT1b', number='T1', long_name='Emu Plains to City',
                 route_type=2, source='sydneytrains'),
        RouteRow(route_id='bT1', number='T1',
                 long_name='T1 North Shore and Western Line', route_type=2,
                 patterns=()),
        RouteRow(route_id='nr', number='', long_name='Non Revenue',
                 route_type=2),
    ])) as conn:
        load_catalogue(conn=conn)
        rows = chooser_rows(conn=conn, kind='train')
        keys = [key for key, in conn.execute(
            'SELECT route_key FROM chooser_routes ORDER BY sort',
        )]
    assert [row[0] for row in rows] == [
        'T1', 'T2', 'T5', 'T9', 'BMT', 'CCN', 'SCO', 'SHL', 'HUN',
    ]
    assert rows[0][1] == 'North Shore & Western'
    assert rows[1][1] == 'City to T2'
    assert 'train:T1' in keys


def test_unlisted_train_lines_sort_after_the_listed_ones() -> None:
    """A train line outside T1-T9 and the intercity lines sorts last."""
    rows = kind_rows(entries=[
        Entry(key=f'train:{code}', kind=RouteKind.TRAIN, number=code,
              name=code, tag='', line='')
        for code in ('T1', 'XPT', 'HUN')
    ])
    assert {row[4]: row[5] for row in rows} == {
        'T1': 1, 'HUN': 105, 'XPT': 203,
    }


def test_trainlink_is_train_and_coaches_are_coach(tmp_path: Path) -> None:
    """The XPT is a train line and coaches are Coach."""
    with closing(tiny_db(path=tmp_path / 't.db', routes=[
        RouteRow(route_id='nrc', number='NRC', long_name='Sydney to Brisbane',
                 route_type=2, agency='NSWTrains'),
        RouteRow(route_id='c1', number='C1', long_name='Coach',
                 route_type=204),
        RouteRow(route_id='ccn', number='CCN', long_name='CCN Line',
                 route_type=2, agency='NSWTrains'),
    ])) as conn:
        load_catalogue(conn=conn)
        kinds = dict(conn.execute('SELECT number, kind FROM chooser_routes'))
    assert kinds == {'NRC': 'train', 'C1': 'coach', 'CCN': 'train'}


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
        load_catalogue(conn=conn)
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
        load_catalogue(conn=conn)
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
        load_catalogue(conn=conn)
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
        load_catalogue(conn=conn)
        rows = conn.execute(
            'SELECT headsign, trips FROM chooser_towards ORDER BY headsign',
        ).fetchall()
    assert rows == [(': Stand E', 2), ('Bankstown Central', 4)]


def build_network(*, path: Path) -> Path:
    """
    Build the network feed into a timetable.

    Parameters
    ----------
    path : Path
        Directory for the feed and the timetable.

    Returns
    -------
    Path
        The timetable file.
    """
    out = path / 'timetable.db'
    source = Source(name='sydneytrains',
                    archive=network_feed(path=path / 'network.zip'))
    build_timetable(sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    return out


def test_towards_rows_use_busiest_pattern(tmp_path: Path) -> None:
    """On the network feed, bus 811 towards its headsign holds B2 B3."""
    with closing(sqlite3.connect(build_network(path=tmp_path))) as conn:
        trips, stations = conn.execute(
            'SELECT trips, stations FROM chooser_towards '
            "WHERE route_key = 'bus:811:Line 811'",
        ).fetchone()
    assert trips == 1
    assert json.loads(stations) == ['B2', 'B3']


def test_no_towards_rows_for_station_first_lines(tmp_path: Path) -> None:
    """Trains get a line row and stations, but no towards rows."""
    with closing(sqlite3.connect(build_network(path=tmp_path))) as conn:
        keys = {
            key for key, in conn.execute(
                'SELECT DISTINCT route_key FROM chooser_towards',
            )
        }
        lines = conn.execute(
            "SELECT COUNT(*) FROM chooser_routes WHERE kind = 'train'",
        ).fetchone()
    assert keys == {'bus:809:Line 809', 'bus:811:Line 811'}
    assert lines != (0,)
