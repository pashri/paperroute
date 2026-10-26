"""Which realtime feed carries each mode."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

from paperroute.core.model import Mode, RouteKind

FEEDS: Final[dict[Mode, str]] = {
    Mode.BUS: 'v1/gtfs/realtime/buses',
    Mode.TRAIN: 'v2/gtfs/realtime/sydneytrains',
    Mode.METRO: 'v2/gtfs/realtime/metro',
    Mode.REGIONAL: 'v1/gtfs/realtime/nswtrains',
}
ALERT_FEEDS: Final[dict[Mode, str]] = {
    Mode.BUS: 'v2/gtfs/alerts/buses',
    Mode.TRAIN: 'v2/gtfs/alerts/sydneytrains',
    Mode.METRO: 'v2/gtfs/alerts/metro',
    Mode.LIGHT_RAIL: 'v2/gtfs/alerts/lightrail',
    Mode.REGIONAL: 'v2/gtfs/alerts/nswtrains',
    Mode.FERRY: 'v2/gtfs/alerts/ferries',
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
KIND_MODES: Final[dict[RouteKind, frozenset[Mode]]] = {
    RouteKind.BUS: frozenset({Mode.BUS}),
    RouteKind.SCHOOL: frozenset({Mode.BUS}),
    RouteKind.REPLACEMENT: frozenset({Mode.BUS}),
    RouteKind.TRAIN: frozenset({Mode.TRAIN}),
    RouteKind.METRO: frozenset({Mode.METRO}),
    RouteKind.LIGHT_RAIL: frozenset({Mode.LIGHT_RAIL}),
    RouteKind.FERRY: frozenset({Mode.FERRY}),
    RouteKind.COACH: frozenset({Mode.COACH}),
}
ALL_POSITION_MODES: Final[frozenset[Mode]] = frozenset(POSITION_FEEDS)
MODE_NAMES: Final[dict[Mode, str]] = {
    Mode.BUS: 'Buses',
    Mode.TRAIN: 'Sydney Trains',
    Mode.METRO: 'Metro',
    Mode.REGIONAL: 'NSW TrainLink',
    Mode.FERRY: 'Ferries',
    Mode.LIGHT_RAIL: 'Light rail',
}


class FeedKind(StrEnum):
    """What a feed path carries."""

    TRIPS = 'trips'
    POSITIONS = 'positions'
    ALERTS = 'alerts'


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


def alert_feeds_for(*, modes: Iterable[Mode]) -> tuple[str, ...]:
    """
    List the alert feeds for some modes.

    Parameters
    ----------
    modes : Iterable[Mode]
        Modes.

    Returns
    -------
    tuple[str, ...]
        Feed paths, sorted and unique; modes with no alert feed add
        nothing.
    """
    return tuple(
        sorted({ALERT_FEEDS[mode] for mode in modes if mode in ALERT_FEEDS})
    )


def feed_kind(*, feed: str) -> FeedKind:
    """
    Tell what a feed path carries.

    Parameters
    ----------
    feed : str
        Feed path.

    Returns
    -------
    FeedKind
        POSITIONS for any path containing '/vehiclepos/', ALERTS for any
        containing '/alerts/', else TRIPS.
    """
    if '/alerts/' in feed:
        return FeedKind.ALERTS
    if '/vehiclepos/' in feed:
        return FeedKind.POSITIONS
    return FeedKind.TRIPS


def feed_label(*, feed: str) -> str:
    """
    Name a feed path for people.

    Parameters
    ----------
    feed : str
        Feed path.

    Returns
    -------
    str
        The mode's name and what the feed carries, with the line for a
        light rail positions feed; an unknown path as it is.
    """
    for mode, path in FEEDS.items():
        if path == feed:
            return f'{MODE_NAMES[mode]} trip updates'
    for mode, path in ALERT_FEEDS.items():
        if path == feed:
            return f'{MODE_NAMES[mode]} alerts'
    for mode, paths in POSITION_FEEDS.items():
        if feed not in paths:
            continue
        label = f'{MODE_NAMES[mode]} positions'
        if mode is Mode.LIGHT_RAIL:
            return f"{label} ({feed.rsplit('/', maxsplit=1)[-1]})"
        return label
    return feed
