"""Which realtime feed carries each mode."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

from paperroute.core.model import Mode

FEEDS: Final[dict[Mode, str]] = {
    Mode.BUS: 'v1/gtfs/realtime/buses',
    Mode.TRAIN: 'v2/gtfs/realtime/sydneytrains',
    Mode.METRO: 'v2/gtfs/realtime/metro',
    Mode.REGIONAL: 'v1/gtfs/realtime/nswtrains',
}
POSITION_FEEDS: Final[dict[Mode, tuple[str, ...]]] = {
    Mode.BUS: ('v1/gtfs/vehiclepos/buses',),
    Mode.TRAIN: ('v2/gtfs/vehiclepos/sydneytrains',),
    Mode.METRO: ('v2/gtfs/vehiclepos/metro',),
    Mode.REGIONAL: ('v1/gtfs/vehiclepos/nswtrains',),
    Mode.FERRY: ('v1/gtfs/vehiclepos/ferries/sydneyferries',),
    Mode.LIGHT_RAIL: (
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
        'v1/gtfs/vehiclepos/lightrail/parramatta',
        'v1/gtfs/vehiclepos/lightrail/newcastle',
    ),
}


class FeedKind(StrEnum):
    """What a feed path carries."""

    TRIPS = 'trips'
    POSITIONS = 'positions'


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


def position_feeds(*, modes: Iterable[Mode]) -> tuple[str, ...]:
    """
    List the vehicle-positions feeds for some modes.

    Parameters
    ----------
    modes : Iterable[Mode]
        Modes.

    Returns
    -------
    tuple[str, ...]
        Feed paths, sorted and unique.
    """
    return tuple(
        sorted(
            {
                path
                for mode in modes
                for path in POSITION_FEEDS.get(mode, ())
            }
        )
    )


def feed_kind(*, feed: str) -> FeedKind:
    """
    Tell a positions feed from a trip-updates feed.

    Parameters
    ----------
    feed : str
        Feed path.

    Returns
    -------
    FeedKind
        POSITIONS for any path containing '/vehiclepos/', else TRIPS.
    """
    if '/vehiclepos/' in feed:
        return FeedKind.POSITIONS
    return FeedKind.TRIPS
