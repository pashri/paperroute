# pylint: disable=unused-argument
"""Tests for rail build: fallbacks."""

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Final, NoReturn

import pytest

from paperroute_transit.build import extras
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.handoff import Handoff, read_handoff
from paperroute_transit.build.runs import load_runs
from paperroute_transit.build.source_state import load_states
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.city import MergeReport
from paperroute_transit.core.modes import Classifier
from tests.build.rail_build.helpers import (
    BODS,
    RAIL,
    Shelf,
    departures,
    night,
    rail_date,
    trip_counts,
)
from tests.build.test_rail_cut import XC_TRIP, changed
from tests.conftest import RECORDED_DAY


def bad_route_type(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Give every route a route_type the loader cannot read.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    for row in tables['routes.txt']:
        row['route_type'] = 'rail'


def blank_time(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Blank the times of one kept call.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    call = next(
        row for row in tables['stop_times.txt'] if row['trip_id'] == XC_TRIP
    )
    call['arrival_time'] = ''
    call['departure_time'] = ''


def no_trips_file(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Remove trips.txt, as a release missing a table does.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    del tables['trips.txt']


FAULTS: Final[dict[str, Callable[[dict[str, list[dict[str, str]]]], None]]] = {
    'route_type': bad_route_type,
    'blank_time': blank_time,
    'no_trips_file': no_trips_file,
}


@pytest.mark.parametrize('fault', list(FAULTS))
def test_a_rail_file_the_loader_rejects_leaves_the_buses_built(
    data_dir: Path, tmp_path: Path, fault: str,
) -> None:
    """On a first night, buses build alone and the exit is a success."""
    bad = changed(out=tmp_path / 'bad.zip', change=FAULTS[fault])
    shelf = Shelf(files={'bods': BODS, 'rail': bad})
    report = night(data_dir=data_dir, shelf=shelf)
    assert (report.failed, report.all_failed, report.rebuilt) == (
        ('rail',), False, True,
    )
    assert trip_counts(data_dir=data_dir) == {'bods': 67}
    states = load_states(path=data_dir / 'sources' / 'sources.json')
    assert 'rail' not in states
    assert swap.printed(report=report) == 0


@pytest.mark.parametrize('fault', list(FAULTS))
def test_a_rail_file_the_loader_rejects_keeps_the_earlier_trains(
    data_dir: Path, tmp_path: Path, fault: str,
) -> None:
    """After a good night, the earlier trains stay, night after night."""
    bad = changed(out=tmp_path / 'bad.zip', change=FAULTS[fault])
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    good = departures(path=data_dir / 'timetable.db', day=RECORDED_DAY)
    before = load_states(path=data_dir / 'sources' / 'sources.json')
    shelf.files['rail'] = bad
    for _ in range(2):
        report = night(data_dir=data_dir, shelf=shelf)
        assert (report.failed, report.all_failed) == (('rail',), False)
        assert trip_counts(data_dir=data_dir) == {'bods': 67, 'rail': 5}
        assert departures(
            path=data_dir / 'timetable.db', day=RECORDED_DAY,
        ) == good
        assert rail_date(data_dir=data_dir) == date(2026, 11, 16)
        after = load_states(path=data_dir / 'sources' / 'sources.json')
        assert after == before


def test_a_bus_source_that_fails_still_fails_the_refresh(
    data_dir: Path,
) -> None:
    """With the buses unreachable and the rail fine, every required fails."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    shelf.gone = {'bods'}
    report = night(data_dir=data_dir, shelf=shelf)
    assert report.failed == ('bods',)
    assert report.all_failed
    assert swap.printed(report=report) == 1


def test_the_trip_floor_counts_the_buses_alone(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """67 bus trips stay under a floor of 68, whatever the trains add."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 68)
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    with pytest.raises(swap.TimetableCheckError, match='67 trips, want 68'):
        night(data_dir=data_dir, shelf=shelf)
    assert not (data_dir / 'timetable.db').exists()


def nowhere(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Send the XC trip to Nowhere, so a build can tell this copy apart.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    for row in tables['trips.txt']:
        if row['trip_id'] == XC_TRIP:
            row['trip_headsign'] = 'Nowhere'


@dataclass
class Refusing:
    """Stand in for build_timetable, refusing builds that load a source."""

    names: frozenset[str]  # a build loading one fails; empty fails all
    calls: int = 0

    def __call__(
        self, *, sources: list[Source], window: Window, out: Path,
        city_ref: str, handoff: Handoff | None = None,
        tiles: Path | None = None,
    ) -> MergeReport:
        """
        Count the build, then refuse it or run the real one.

        Parameters
        ----------
        sources : list[Source]
            Feeds to load.
        window : Window
            Dates to cover.
        out : Path
            Timetable file.
        city_ref : str
            The city's reference.
        handoff : Handoff | None
            Passed on.
        tiles : Path | None
            Passed on.

        Returns
        -------
        MergeReport
            The real build's report.

        Raises
        ------
        ValueError
            When the build is refused.
        """
        self.calls += 1
        if not self.names or self.names & {s.name for s in sources}:
            raise ValueError('the build refused a source')
        return build_timetable(
            sources=sources, window=window, out=out, city_ref=city_ref,
            handoff=handoff, tiles=tiles,
        )


def test_a_stored_rail_copy_the_build_rejects_leaves_the_buses_built(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no new rail copy to drop, the buses are built alone."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    refusing = Refusing(names=frozenset({'rail'}))
    monkeypatch.setattr(refresh_module, 'build_timetable', refusing)
    report = night(data_dir=data_dir, shelf=shelf)
    assert (report.failed, report.all_failed, report.rebuilt) == (
        ('rail',), False, True,
    )
    assert trip_counts(data_dir=data_dir) == {'bods': 67}
    assert rail_date(data_dir=data_dir) is None
    states = load_states(path=data_dir / 'sources' / 'sources.json')
    assert 'rail' not in states
    assert refusing.calls == 2
    assert swap.printed(report=report) == 0


def test_a_build_that_fails_without_the_trains_still_fails(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New copy, stored copy, no trains: three builds, then the error."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    before = trip_counts(data_dir=data_dir)
    shelf.files['rail'] = changed(out=tmp_path / 'new.zip', change=nowhere)
    refusing = Refusing(names=frozenset())
    monkeypatch.setattr(refresh_module, 'build_timetable', refusing)
    with pytest.raises(ValueError, match='refused'):
        night(data_dir=data_dir, shelf=shelf)
    assert refusing.calls == 3
    assert trip_counts(data_dir=data_dir) == before


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """
    Put the root logger back after a test that configures it.

    Yields
    ------
    None
        Control to the test.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


NEW_COPY: Final[str] = "SELECT 1 FROM trips WHERE headsign = 'Nowhere'"


ANY_RAIL: Final[str] = "SELECT 1 FROM trips WHERE source = 'rail'"


EVERY_BUILD: Final[str] = 'SELECT 1'


@dataclass
class FaultyRuns:
    """Stand in for load_runs, failing while a query finds a row."""

    sql: str
    calls: int = 0

    def __call__(
        self, *, conn: sqlite3.Connection, classify: Classifier,
        rail_types: Collection[int], turns: bool,
    ) -> object:
        """
        Count the call, then fail or build the run tables.

        Parameters
        ----------
        conn : sqlite3.Connection
            Timetable being finished.
        classify : Classifier
            The city's classifier.
        rail_types : Collection[int]
            The city's rail route types.
        turns : bool
            Whether to store the turnarounds.

        Returns
        -------
        object
            The real report.

        Raises
        ------
        ValueError
            When the query finds a row.
        """
        self.calls += 1
        if conn.execute(self.sql).fetchone():
            raise ValueError('the run tables refused a trip')
        return load_runs(
            conn=conn, classify=classify, rail_types=rail_types, turns=turns,
        )


def run_here(path: str, argv: list[str]) -> NoReturn:
    """
    Run the program an exec would start in this process, then exit.

    The lock descriptor is put back as the exec would have kept it.

    Parameters
    ----------
    path : str
        Interpreter, unused.
    argv : list[str]
        Interpreter, '-m', module, then its arguments; the last is the
        handoff file.

    Raises
    ------
    SystemExit
        With the program's exit status.
    """
    tail = read_handoff(path=Path(argv[-1])).tail
    assert tail is not None
    spare = os.dup(tail.lock_fd)
    try:
        code = import_module(argv[2]).main(argv=argv[3:])
    finally:
        os.dup2(spare, tail.lock_fd)
        os.close(spare)
    raise SystemExit(code)


@pytest.mark.usefixtures('restored_logging')
@pytest.mark.parametrize(
    ('good_first', 'new_copy', 'fault', 'expected'),
    [
        (False, True, NEW_COPY, (0, {'bods': 67}, 2, [['rail']])),
        (True, True, NEW_COPY,
         (0, {'bods': 67, 'rail': 5}, 2, [['rail']])),
        (True, False, ANY_RAIL, (0, {'bods': 67}, 2, [['rail']])),
        (True, True, EVERY_BUILD, (1, {'bods': 67, 'rail': 5}, 3, [])),
    ],
    ids=['first-night', 'new-copy', 'stored-copy', 'every-build'],
)
def test_a_handed_over_build_falls_back_when_the_run_tables_fail(
    *,
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], good_first: bool, new_copy: bool,
    fault: str, expected: tuple[int, dict[str, int], int, list[list[str]]],
) -> None:
    """After the exec, a failure tries again without trusting the rail."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    if good_first:
        night(data_dir=data_dir, shelf=shelf)
    if new_copy:
        shelf.files['rail'] = changed(
            out=tmp_path / 'new.zip', change=nowhere,
        )
    runs = FaultyRuns(sql=fault)
    monkeypatch.setattr(extras, 'load_runs', runs)
    monkeypatch.setattr(os, 'execv', run_here)
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        night(data_dir=data_dir, shelf=shelf, hand_over=True)
    reports = [
        json.loads(line)['failed']
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"downloaded"')
    ]
    assert (
        stop.value.code, trip_counts(data_dir=data_dir), runs.calls,
        reports,
    ) == expected
