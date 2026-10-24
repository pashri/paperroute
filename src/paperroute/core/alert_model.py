"""Service alerts, as plain data."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Period:
    """A window in which an alert applies; a missing end never ends."""

    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class Informed:
    """
    One thing an alert is about.

    ``route`` and ``stops`` are filled in once the feed's IDs have been
    looked up in the timetable.
    """

    route_id: str | None = None
    stop_id: str | None = None
    trip_id: str | None = None
    route: str | None = None
    stops: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Alert:
    """One service alert."""

    alert_id: str
    header: str
    description: str
    periods: tuple[Period, ...]
    informed: tuple[Informed, ...]


@dataclass(frozen=True)
class AlertsSnapshot:
    """Every alert of one feed at one moment."""

    feed: str
    fetched_at: datetime
    alerts: tuple[Alert, ...]


@dataclass(frozen=True)
class AlertPlaces:
    """
    What the timetable knows about the IDs an alert feed names.

    ``routes`` maps a feed route ID to a route short name and ``stops``
    maps a feed stop ID to its family: the stop, its parent station and
    its child stops. Unknown IDs are absent. ``metro`` is the short name
    of the timetable's only metro route, or None.
    """

    routes: Mapping[str, str]
    stops: Mapping[str, frozenset[str]]
    metro: str | None = None


@dataclass(frozen=True)
class AlertsFetched:
    """One alert feed was downloaded and parsed."""

    feed: str
    snapshot: AlertsSnapshot
