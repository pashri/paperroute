"""Shared helpers for timetable files and stepping the reducer in tests."""

import importlib.util
import logging
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_sydney.build.dedupe import merge_rail
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.realtime import StopUpdate, TripRealtime
from paperroute_transit.core.saved import BoardSettings, Rate, Settings
from paperroute_transit.core.stop_view import StopView

REAL: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'
M9B_DB: Final[Path] = Path(__file__).parents[1] / 'data' / 'timetable-m9b.db'


def old_schema(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-2 file.

    The run, carry-on and chooser tables are dropped and the schema version
    set to '2'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    tables = (
        'runs', 'run_stops', 'run_calls', 'trip_runs', 'trip_next',
        'trip_joins', 'route_patterns', 'chooser_routes', 'chooser_towards',
    )
    with closing(sqlite3.connect(path)) as conn:
        for table in tables:
            conn.execute(f'DROP TABLE IF EXISTS {table}')
        conn.execute(
            "UPDATE meta SET value = '2' WHERE key = 'schema_version'",
        )
        conn.commit()


def schema_three(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-3 file.

    Only the line_stations, line_aliases, line_track, line_exits and
    run_calls tables and trip_next's next_trip column are dropped, and
    the schema version set to '3'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE IF EXISTS line_stations')
        conn.execute('DROP TABLE IF EXISTS line_aliases')
        conn.execute('DROP TABLE IF EXISTS line_exits')
        conn.execute('DROP TABLE IF EXISTS line_track')
        conn.execute('DROP TABLE IF EXISTS run_calls')
        conn.execute('ALTER TABLE trip_next DROP COLUMN next_trip')
        conn.execute(
            "UPDATE meta SET value = '3' WHERE key = 'schema_version'",
        )
        conn.commit()


def schema_four(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-4 file.

    Only trips' service column is dropped, and the schema version set to
    '4'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('ALTER TABLE trips DROP COLUMN service')
        conn.execute(
            "UPDATE meta SET value = '4' WHERE key = 'schema_version'",
        )
        conn.commit()


def schema_five(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-5 file.

    run_calls is made again without its one-way columns, trip_joins is
    dropped if there, and the schema version set to '5'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            'CREATE TABLE old_calls (run_id INTEGER NOT NULL, '
            'stops TEXT NOT NULL);'
            'INSERT INTO old_calls SELECT run_id, stops FROM run_calls;'
            'DROP TABLE run_calls;'
            'ALTER TABLE old_calls RENAME TO run_calls;'
            'CREATE INDEX run_calls_run ON run_calls (run_id);'
            'DROP TABLE IF EXISTS trip_joins;'
            "UPDATE meta SET value = '5' WHERE key = 'schema_version';",
        )
        conn.commit()


def slice_db(*, tmp_path: Path) -> Path:
    """
    Build a timetable database from the real GTFS slice, with shapes.

    Parameters
    ----------
    tmp_path : Path
        Empty directory for the database.

    Returns
    -------
    Path
        The database file.
    """
    # feeds imports this module, so it can only be imported here.
    from tests.build.feeds import build_db  # pylint: disable=C0415

    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
    return db


def schema_ten(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-10 file.

    Only stop_times' along column is dropped, and the schema version set
    to '10'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('ALTER TABLE stop_times DROP COLUMN along')
        conn.execute(
            "UPDATE meta SET value = '10' WHERE key = 'schema_version'",
        )
        conn.commit()


def sole[T](*, items: Sequence[T]) -> T:
    """
    Give the only item of a sequence.

    Parameters
    ----------
    items : Sequence[T]
        Sequence that must hold exactly one item.

    Returns
    -------
    T
        The item.
    """
    assert len(items) == 1, items
    return items[0]


def exactly_two[T](*, items: Sequence[T]) -> tuple[T, T]:
    """
    Give the two items of a sequence.

    Parameters
    ----------
    items : Sequence[T]
        Sequence that must hold exactly two items.

    Returns
    -------
    tuple[T, T]
        The first and second items.
    """
    assert len(items) == 2, items
    return items[0], items[1]


def delayed_trip(
    *,
    trip_id: str,
    sequence: int,
    seconds: int,
    estimated: bool = False,
    uncertain: bool = False,
    heard_at: datetime | None = None,
) -> TripRealtime:
    """
    Make a trip running late from one stop sequence on.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    sequence : int
        Stop sequence the delay starts at.
    seconds : int
        Departure delay.
    estimated : bool
        Whether the times are an estimate from a position.
    uncertain : bool
        Whether the estimate is uncertain.
    heard_at : datetime | None
        When the position was reported.

    Returns
    -------
    TripRealtime
        The trip.
    """
    update = StopUpdate(
        sequence=sequence,
        stop_id=None,
        arrival_delay=None,
        departure_delay=seconds,
        arrival_time=None,
        departure_time=None,
    )
    return TripRealtime(
        trip_id=trip_id,
        cancelled=False,
        updates=(update,),
        start_date=None,
        estimated=estimated,
        uncertain=uncertain,
        heard_at=heard_at,
    )


def empty_stop_view(
    *,
    origin: str,
    destination: str | None,
    clock: str,
    banner: str | None = None,
) -> StopView:
    """
    Make a Stop view with no rows.

    Parameters
    ----------
    origin : str
        The pin's From name.
    destination : str | None
        The pin's To name.
    clock : str
        Time of day shown.
    banner : str | None
        Banner text.

    Returns
    -------
    StopView
        The view.
    """
    return StopView(
        origin=origin,
        destination=destination,
        clock=clock,
        hero=None,
        rows=(),
        banner=banner,
        empty=None,
        scroll=0,
    )


def has_runs(*, path: Path) -> bool:
    """
    Say whether a timetable file exists with the run and track tables.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    bool
        True when the runs and track_places tables are there.
    """
    if not path.exists():
        return False
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        found = conn.execute(
            "SELECT name FROM sqlite_master"
            " WHERE name IN ('runs', 'track_places')",
        ).fetchall()
    return len(found) == 2


def bare_pattern(
    *, route: str, names: Sequence[str], stations: Sequence[str],
    days: tuple[date, ...] = (),
) -> TripPattern:
    """
    Make a trip pattern calling at each stop in turn, with no times.

    Parameters
    ----------
    route : str
        Route short name.
    names : Sequence[str]
        Stop names, in calling order.
    stations : Sequence[str]
        Station IDs, one for each name.
    days : tuple[date, ...]
        Service days.

    Returns
    -------
    TripPattern
        The pattern, as trip 't' of source 's'.
    """
    return TripPattern(
        trip_id='t',
        route=route,
        source='s',
        shape_id=None,
        stops=tuple(
            PatternStop(
                arrives=0,
                departs=0,
                point=None,
                station=station,
                name=name,
                passes=False,
            )
            for station, name in zip(stations, names, strict=True)
        ),
        days=days,
    )


def m9b_opened() -> closing[SqliteTimetableRepository]:
    """
    Open the M9B timetable file as Sydney's timetable.

    Returns
    -------
    closing[SqliteTimetableRepository]
        Context manager that closes the repository on exit.
    """
    return closing(SqliteTimetableRepository(path=M9B_DB, city=SYDNEY))


def version_failures(
    *, caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """
    List the logged failures to read the timetable's version.

    Parameters
    ----------
    caplog : pytest.LogCaptureFixture
        The test's captured log.

    Returns
    -------
    list[logging.LogRecord]
        The records of the failure message.
    """
    return [
        record
        for record in caplog.records
        if record.message == 'reading the timetable version failed'
    ]


def load_module(*, name: str, path: Path) -> ModuleType:
    """
    Import a script file as a module.

    Parameters
    ----------
    name : str
        Module name to register it under.
    path : Path
        The script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def changed_settings() -> Settings:
    """
    Make settings that differ from the defaults in several fields.

    Returns
    -------
    Settings
        Device settings with quiet mode, hours, rotation and text changed,
        and board settings with rebuilds, rates and peak windows changed.
    """
    return Settings(
        device=DeviceSettings(
            quiet_mode=QuietMode.APP,
            my_hours=Window(start=time(22, 15), end=time(6)),
            rotate=True,
            larger_text=True,
            inverted_lists=True,
        ),
        board=BoardSettings(
            auto_rebuild=False,
            peak_rate=Rate.SLOW,
            offpeak_rate=Rate.STEADY,
            peak_windows=(Window(start=time(7), end=time(9)),),
        ),
    )
