"""Build the run tables of a timetable file in a separate process."""

import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from paperroute.build.runs import RUN_SCHEMA, load_runs
from paperroute.rss import max_rss_mb


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


def run_extras(*, path: Path) -> None:
    """
    Add the run tables to a built timetable and print the peak memory.

    Parameters
    ----------
    path : Path
        Timetable file.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA cache_size = -8000')
        conn.executescript(RUN_SCHEMA)
        timed(phase='runs', step=lambda: load_runs(conn=conn))
        conn.commit()
    print(f'progress extras peak {max_rss_mb():.0f}MB', flush=True)


def build_extras(*, path: Path) -> None:
    """
    Run run_extras in a fresh interpreter that loads only what it needs.

    Parameters
    ----------
    path : Path
        Timetable file.

    Raises
    ------
    RuntimeError
        When the child exits with a non-zero code.
    """
    done = subprocess.run(
        [sys.executable, '-m', 'paperroute.build.extras', str(path)],
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f'run tables build failed with exit code {done.returncode}',
        )


if __name__ == '__main__':
    run_extras(path=Path(sys.argv[1]))
