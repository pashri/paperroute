"""Which realtime feed carries each mode."""

from typing import Final

from paperroute.core.model import Mode

FEEDS: Final[dict[Mode, str]] = {
    Mode.BUS: 'v1/gtfs/realtime/buses',
    Mode.TRAIN: 'v2/gtfs/realtime/sydneytrains',
    Mode.METRO: 'v2/gtfs/realtime/metro',
    Mode.REGIONAL: 'v1/gtfs/realtime/nswtrains',
}


def feed_for(*, mode: Mode) -> str | None:
    """
    Find the trip-updates feed for a mode.

    Parameters
    ----------
    mode : Mode
        Mode.

    Returns
    -------
    str | None
        Feed path, or None when the mode has no live feed yet.
    """
    return FEEDS.get(mode)
