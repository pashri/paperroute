"""How much memory this process and its finished children have used."""

import os
import resource
import subprocess
import sys
from pathlib import Path
from typing import Final

BYTES_PER_KB: Final[int] = 1024
BYTES_PER_MB: Final[int] = BYTES_PER_KB * BYTES_PER_KB
STATM: Final[Path] = Path('/proc/self/statm')


def max_rss_mb() -> float:
    """
    Peak memory use of this process or its largest finished child.

    A child counts only once it has exited and been waited for.

    Returns
    -------
    float
        Megabytes. Linux reports kilobytes and macOS bytes, so the raw
        value is scaled by platform.
    """
    peak = max(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    )
    if sys.platform == 'linux':
        return peak / BYTES_PER_KB
    return peak / BYTES_PER_MB


def current_rss_mb(*, statm: Path = STATM) -> float:
    """
    Memory this process holds right now.

    Parameters
    ----------
    statm : Path
        Linux's statm file for the process; when it is missing, ``ps``
        is asked instead.

    Returns
    -------
    float
        Megabytes resident.
    """
    if statm.exists():
        pages = int(statm.read_text(encoding='ascii').split()[1])
        return pages * os.sysconf('SC_PAGE_SIZE') / BYTES_PER_MB
    kilobytes = subprocess.run(
        ['ps', '-o', 'rss=', '-p', str(os.getpid())],
        check=True, capture_output=True, text=True,
    ).stdout
    return int(kilobytes) / BYTES_PER_KB
