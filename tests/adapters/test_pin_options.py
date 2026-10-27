"""Tests for the choices a pin's editor lists from the timetable."""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute.adapters import pin_options
from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.pin_options import SqlitePinOptions
from paperroute.core.chooser import UNPICKED, Choice, Level, focusable
from paperroute.core.model import RouteKind, StopPlace
from paperroute.core.reach import RowKind, StripKey, build_reach
from paperroute.core.route_colours import RouteColour
from tests.adapters.test_network_db import T1, build_network

SCHOOL_RUN: str = (
    "INSERT INTO runs VALUES (90, 'school', 'S901', '[\"B1\", \"B2\"]', 3)"
)
REPLACEMENT_RUN: str = (
    "INSERT INTO runs VALUES (91, 'replacement', 'T1X', '[\"B2\"]', 3)"
)
STOP_ROWS: str = (
    "INSERT INTO run_stops VALUES ('B2', 90, 1), ('B2', 91, 0)"
)


@pytest.fixture(name='built', scope='module')
def _built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network timetable once for the module.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file.
    """
    return build_network(folder=tmp_path_factory.mktemp('options'))


@pytest.fixture(name='network')
def _network(built: Path) -> Iterator[SqliteNetworkRepository]:
    """
    A repository over the unchanged timetable.

    Parameters
    ----------
    built : Path
        Timetable file.

    Yields
    ------
    SqliteNetworkRepository
        Repository, closed after the test.
    """
    with closing(SqliteNetworkRepository(path=built)) as repo:
        yield repo


@pytest.fixture(name='options')
def _options(network: SqliteNetworkRepository) -> SqlitePinOptions:
    """
    Pin options over the unchanged timetable.

    Parameters
    ----------
    network : SqliteNetworkRepository
        Repository.

    Returns
    -------
    SqlitePinOptions
        The options.
    """
    return SqlitePinOptions(network=network)


def at(*, options: SqlitePinOptions, stop_id: str) -> StopPlace:
    """
    Read a place that is in the timetable.

    Parameters
    ----------
    options : SqlitePinOptions
        The options.
    stop_id : str
        Stop or station ID.

    Returns
    -------
    StopPlace
        The place.
    """
    found = options.place(stop_id=stop_id)
    assert found is not None
    return found


def test_search_prefix_first(options: SqlitePinOptions) -> None:
    """A name that starts with the text comes first; platforms never do."""
    found = options.search(text='c st')
    assert found[0].place.stop_id == 'C'
    assert 'C_1' not in {match.place.stop_id for match in found}


def test_search_shows_kinds_and_routes(options: SqlitePinOptions) -> None:
    """Each result lists its kinds and route numbers."""
    by_id = {m.place.stop_id: m for m in options.search(text='stop')}
    assert by_id['C'].kinds == (RouteKind.TRAIN,)
    assert by_id['C'].routes == ('T1', 'T2')
    assert by_id['B2'].kinds == (RouteKind.BUS,)
    assert by_id['B2'].routes == ('809', '811')


def test_search_lists_routes_naturally(built: Path, tmp_path: Path) -> None:
    """Rail lines come before buses, each in natural order, capped at six."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    runs = ['9', '10', '100', '20', '21', '3']
    with closing(sqlite3.connect(copy)) as conn:
        for number, route in enumerate(runs, start=92):
            conn.execute(
                f"INSERT INTO runs VALUES ({number}, 'bus', '{route}', "
                "'[\"C\"]', 3)",
            )
            conn.execute(f"INSERT INTO run_stops VALUES ('C', {number}, 0)")
        conn.commit()
    with closing(SqliteNetworkRepository(path=copy)) as network:
        found = SqlitePinOptions(network=network).search(text='c st')
    assert found[0].routes == ('T1', 'T2', '3', '9', '10', '20')
    assert found[0].kinds == (RouteKind.BUS, RouteKind.TRAIN)


def test_search_escapes_like(options: SqlitePinOptions) -> None:
    """LIKE wildcards and quotes in the text match nothing special."""
    assert options.search(text='%') == ()
    assert options.search(text='C_') == ()
    assert options.search(text="O'C") == ()
    assert options.search(text='%%') == ()


def test_search_needs_two_letters(options: SqlitePinOptions) -> None:
    """Text shorter than two characters, once trimmed, finds nothing."""
    assert options.search(text=' c ') == ()


def test_search_limit(
    options: SqlitePinOptions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No more than the limit are returned."""
    monkeypatch.setattr(pin_options, 'SEARCH_LIMIT', 3)
    assert len(options.search(text='stop')) == 3


def test_place_finds_or_misses(options: SqlitePinOptions) -> None:
    """A known ID gives its place with a point; an unknown one None."""
    assert at(options=options, stop_id='C').point is not None
    assert options.place(stop_id='nowhere') is None


def test_trip_stop_lifts_to_parent(options: SqlitePinOptions) -> None:
    """A platform maps to its station; a station to itself."""
    station = at(options=options, stop_id='C')
    assert options.trip_stop(stop_id='C_1') == station
    assert options.trip_stop(stop_id='C') == station
    assert options.trip_stop(stop_id='nowhere') is None


def test_kinds_at_lists_kinds_with_lines(options: SqlitePinOptions) -> None:
    """Only kinds that have a line or route at the stop are listed."""
    assert options.kinds_at(stop_id='C') == (RouteKind.TRAIN,)
    assert options.kinds_at(stop_id='B2') == (RouteKind.BUS,)


def test_lines_at_station(options: SqlitePinOptions) -> None:
    """A station lists the lines whose stations hold it."""
    both = options.lines_at(stop_id='C', kind=RouteKind.TRAIN)
    assert tuple(line.badge for line in both) == ('T1', 'T2')
    assert both[0].key == 'train:T1'
    only_one = options.lines_at(stop_id='R2', kind=RouteKind.TRAIN)
    assert tuple(line.badge for line in only_one) == ('T1',)


def test_lines_at_stop(options: SqlitePinOptions) -> None:
    """A bus stop lists the routes of the runs that serve it."""
    found = options.lines_at(stop_id='B2', kind=RouteKind.BUS)
    assert tuple(line.badge for line in found) == ('809', '811')
    assert options.lines_at(stop_id='B2', kind=RouteKind.TRAIN) == ()


def test_destinations_station_first_match_to_column(
    network: SqliteNetworkRepository, options: SqlitePinOptions,
) -> None:
    """The To list is the focusable rows of the line's TO column."""
    line = options.lines_at(stop_id='C', kind=RouteKind.TRAIN)[0]
    column = network.choices(path=(*T1, (Level.STATION, 'C')))
    assert column is not None
    expected = tuple(
        row.place
        for row in column.items
        if focusable(choice=row) and row.tag != 'ALL' and row.place
    )
    found = options.destinations(
        origin=at(options=options, stop_id='C'),
        kind=RouteKind.TRAIN,
        line=line,
    )
    assert found == expected
    ids = {place.stop_id for place in found}
    assert {'E1', 'J', 'W2'} <= ids
    assert 'C' not in ids


def test_destinations_route_first_match_strip(
    network: SqliteNetworkRepository, options: SqlitePinOptions,
) -> None:
    """The To list is the strip rows the TO knob can stop on."""
    origin = at(options=options, stop_id='B2')
    line = options.lines_at(stop_id='B2', kind=RouteKind.BUS)[1]
    data = network.reach_input(
        key=StripKey(station='B2', kind=RouteKind.BUS, routes=('811',)),
        origin=origin,
    )
    assert data is not None
    expected = tuple(
        row.place
        for row in build_reach(data=data).rows
        if row.kind not in UNPICKED and row.kind is not RowKind.ORIGIN
    )
    found = options.destinations(
        origin=origin, kind=RouteKind.BUS, line=line,
    )
    assert found == expected
    assert [place.stop_id for place in found] == ['B3']


def test_destinations_without_line_tables(
    built: Path, tmp_path: Path,
) -> None:
    """A file without the line tables offers no station-first To."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE line_stations')
        conn.commit()
    with closing(SqliteNetworkRepository(path=copy)) as network:
        found = SqlitePinOptions(network=network).destinations(
            origin=StopPlace(stop_id='C', name='C Stop', point=None),
            kind=RouteKind.TRAIN,
            line=Choice(key='train:T1', label='Line T1', badge='T1'),
        )
    assert found == ()


def test_filters_for_bus_include_school_and_replacement(
    built: Path, tmp_path: Path,
) -> None:
    """A bus stop's filter lists every route that boards there."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute(SCHOOL_RUN)
        conn.execute(REPLACEMENT_RUN)
        conn.execute(STOP_ROWS)
        conn.commit()
    with closing(SqliteNetworkRepository(path=copy)) as network:
        found = SqlitePinOptions(network=network).filters_at(
            stop_id='B2', kind=RouteKind.BUS,
        )
        trains = SqlitePinOptions(network=network).filters_at(
            stop_id='B2', kind=RouteKind.TRAIN,
        )
    assert {'809', '811', 'S901', 'T1X'} <= set(found)
    assert trains == ()


def test_filters_for_train_are_its_lines(options: SqlitePinOptions) -> None:
    """A station's filter lists the routes of its trains in line order."""
    found = options.filters_at(stop_id='C', kind=RouteKind.TRAIN)
    assert found == ('T1', 'T2')


def test_nearby_lists_close_stops(
    options: SqlitePinOptions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stops with runs of the kind within the distance, nearest first."""
    centre = at(options=options, stop_id='C')
    assert options.nearby(place=centre, kind=RouteKind.TRAIN) == ()
    monkeypatch.setattr(pin_options, 'NEARBY_M', 2500)
    found = options.nearby(place=centre, kind=RouteKind.TRAIN)
    ids = [place.stop_id for place in found]
    assert 'J' in ids
    assert 'C' not in ids
    assert len(ids) <= 5
    assert 'B2' not in ids


def test_nearby_without_a_point(options: SqlitePinOptions) -> None:
    """A place with no point has nothing near it."""
    place = StopPlace(stop_id='C', name='C Stop', point=None)
    assert options.nearby(place=place, kind=None) == ()


def test_old_file_offers_nothing(built: Path, tmp_path: Path) -> None:
    """A file without the run and chooser tables has no choices."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE chooser_routes')
        conn.commit()
    with closing(SqliteNetworkRepository(path=copy)) as network:
        old = SqlitePinOptions(network=network)
        centre = StopPlace(stop_id='C', name='C', point=(0.0, 0.0))
        assert old.search(text='stop') == ()
        assert old.lines_at(stop_id='C', kind=RouteKind.TRAIN) == ()
        assert old.filters_at(stop_id='C', kind=RouteKind.TRAIN) == ()
        assert old.nearby(place=centre, kind=None) == ()


def test_route_colours_reads_timetable(built: Path, tmp_path: Path) -> None:
    """The most common pair wins; names without a colour are absent."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute(
            "UPDATE routes SET color = 'F99D1C', text_color = '000000'"
            " WHERE short_name = 'T1'",
        )
        conn.executemany(
            "INSERT INTO routes VALUES (?, 'T1', 'T1', 2, ?, NULL, NULL, 'x')",
            [('x:1', '0098CD'), ('x:2', 'f99d1c'), ('x:3', 'bad')],
        )
        conn.commit()
    with closing(SqliteNetworkRepository(path=copy)) as network:
        found = SqlitePinOptions(network=network).route_colours(
            names=('T1', 'T2', '809'),
        )
    assert found == {'T1': RouteColour(fill='f99d1c', text='000000')}


def test_route_colours_without_network(built: Path, tmp_path: Path) -> None:
    """A file without the network tables has no colours."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute("UPDATE routes SET color = 'F99D1C'")
        conn.execute('DROP TABLE chooser_routes')
        conn.commit()
    with closing(SqliteNetworkRepository(path=copy)) as network:
        found = SqlitePinOptions(network=network).route_colours(names=('T1',))
    assert found == {}
