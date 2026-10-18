"""How much memory this process and its finished children have used."""

import resource
import sys
from typing import Final

BYTES_PER_KB: Final[int] = 1024
BYTES_PER_MB: Final[int] = BYTES_PER_KB * BYTES_PER_KB


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
