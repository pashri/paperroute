"""Build the run tables of a timetable file in a separate process."""

import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from importlib import import_module
from pathlib import Path

from paperroute_device.rss import peak_rss_mb
from paperroute_transit.build.catalogue import CATALOGUE_SCHEMA, load_catalogue
from paperroute_transit.build.passed import write_passed
from paperroute_transit.build.runs import RUN_SCHEMA, load_runs
from paperroute_transit.build.swap import land
from paperroute_transit.build.track_db import read_track_db
from paperroute_transit.build.weekday import write_weekday_runs
from paperroute_transit.core.city import City
from paperroute_transit.core.modes import end_point_modes


def timed(*, phase: str, step: Callable[[], object]) -> None:
    """
    Run one phase, printing its start and how long it took.

    Parameters
    ----------
    phase : str
        Phase name.
    step : Callable[[], object]
        The phase's work.
    """
    print(f'progress {phase}', flush=True)
    start = time.monotonic()
    step()
    elapsed = time.monotonic() - start
    print(f'progress {phase} done {elapsed:.1f}s', flush=True)


def city_named(*, ref: str) -> City:
    """
    Import a city from its reference.

    Parameters
    ----------
    ref : str
        The module and the name of the city in it, such as
        ``package.module:NAME``.

    Returns
    -------
    City
        The city.

    Raises
    ------
    ModuleNotFoundError
        When the module does not exist.
    ValueError
        When the name in the module is not a city.
    """
    module, _, name = ref.partition(':')
    city = getattr(import_module(module), name, None)
    if not isinstance(city, City):
        raise ValueError(f'{ref} names no city')
    return city


def run_passed(
    *, conn: sqlite3.Connection, city: City, tiles: Path | None,
) -> None:
    """
    Write the passed-station tables from the track in a tiles file.

    The track is read inside this function and dropped when it returns.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    city : City
        The city the timetable is for.
    tiles : Path | None
        Tiles file; None builds with no track.
    """
    report = write_passed(
        conn=conn,
        track=None if tiles is None else read_track_db(path=tiles),
        kinds=end_point_modes(modes=city.modes),
        projection=city.projection,
    )
    print(
        f'progress passed hops={report.hops} inside={report.inside}'
        f' added={report.added}',
        flush=True,
    )


def run_extras(*, path: Path, city: City, tiles: Path | None) -> None:
    """
    Add the run and chooser tables to a timetable and print the peak memory.

    Parameters
    ----------
    path : Path
        Timetable file.
    city : City
        The city the timetable is for.
    tiles : Path | None
        Tiles file holding the rail track; None for no track.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA cache_size = -2000')
        conn.executescript(RUN_SCHEMA)
        conn.executescript(CATALOGUE_SCHEMA)
        timed(
            phase='runs',
            step=lambda: load_runs(
                conn=conn,
                classify=city.classify,
                rail_types=city.catalogue.rail_types,
            ),
        )
        timed(
            phase='passed',
            step=lambda: run_passed(conn=conn, city=city, tiles=tiles),
        )
        timed(
            phase='weekday',
            step=lambda: write_weekday_runs(
                conn=conn, kinds=end_point_modes(modes=city.modes),
            ),
        )
        timed(
            phase='catalogue',
            step=lambda: load_catalogue(
                conn=conn,
                projection=city.projection,
                classify=city.classify,
                modes=city.modes,
                rules=city.catalogue,
            ),
        )
        conn.commit()
        if city.timetable.compact:
            conn.execute('VACUUM')
    print(f'progress extras peak {peak_rss_mb():.0f}MB', flush=True)


def build_extras(
    *, path: Path, city_ref: str, tiles: Path | None = None,
) -> None:
    """
    Run run_extras in a fresh interpreter that loads only what it needs.

    Parameters
    ----------
    path : Path
        Timetable file.
    city_ref : str
        The city's reference, see `city_named`.
    tiles : Path | None
        Tiles file holding the rail track; None for no track.

    Raises
    ------
    RuntimeError
        When the child exits with a non-zero code.
    """
    done = subprocess.run(
        [
            sys.executable, '-m', 'paperroute_transit.build.extras', city_ref,
            str(path), '' if tiles is None else str(tiles),
        ],
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f'run tables build failed with exit code {done.returncode}',
        )


def main(*, argv: Sequence[str]) -> int:
    """
    Run as a child (no handoff) or after a hand over (with a handoff).

    Parameters
    ----------
    argv : Sequence[str]
        The city's reference (see `city_named`), the scratch file, the
        tiles file (empty for none), then the handoff file if any.

    Returns
    -------
    int
        Exit status.
    """
    city = city_named(ref=argv[0])
    scratch = Path(argv[1])
    tiles = Path(argv[2]) if argv[2] else None
    if len(argv) == 3:
        run_extras(path=scratch, city=city, tiles=tiles)
        return 0
    return land(
        scratch=scratch, path=Path(argv[3]),
        build=lambda path: run_extras(path=path, city=city, tiles=tiles),
        city_ref=argv[0],
    )


if __name__ == '__main__':
    sys.exit(main(argv=sys.argv[1:]))
