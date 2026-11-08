"""Build the run tables of a timetable file in a separate process."""

import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from importlib import import_module
from pathlib import Path

from paperroute.build.swap import land
from paperroute_device.rss import peak_rss_mb
from paperroute_transit.build.catalogue import CATALOGUE_SCHEMA, load_catalogue
from paperroute_transit.build.runs import RUN_SCHEMA, load_runs
from paperroute_transit.core.city import City


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


def run_extras(*, path: Path, city: City) -> None:
    """
    Add the run and chooser tables to a timetable and print the peak memory.

    Parameters
    ----------
    path : Path
        Timetable file.
    city : City
        The city the timetable is for.
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
    print(f'progress extras peak {peak_rss_mb():.0f}MB', flush=True)


def build_extras(*, path: Path, city_ref: str) -> None:
    """
    Run run_extras in a fresh interpreter that loads only what it needs.

    Parameters
    ----------
    path : Path
        Timetable file.
    city_ref : str
        The city's reference, see `city_named`.

    Raises
    ------
    RuntimeError
        When the child exits with a non-zero code.
    """
    done = subprocess.run(
        [
            sys.executable, '-m', 'paperroute.build.extras', city_ref,
            str(path),
        ],
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f'run tables build failed with exit code {done.returncode}',
        )


def main(*, argv: Sequence[str]) -> int:
    """
    Run as a child (scratch only) or after a hand over (scratch, handoff).

    Parameters
    ----------
    argv : Sequence[str]
        The city's reference (see `city_named`), the scratch file, then
        the handoff file if any.

    Returns
    -------
    int
        Exit status.
    """
    city = city_named(ref=argv[0])
    scratch = Path(argv[1])
    if len(argv) == 2:
        run_extras(path=scratch, city=city)
        return 0
    return land(
        scratch=scratch, path=Path(argv[2]),
        build=lambda path: run_extras(path=path, city=city),
    )


if __name__ == '__main__':
    sys.exit(main(argv=sys.argv[1:]))
