"""Service alerts, as plain data."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum

from paperroute.core.model import Pin


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


class AlertTie(IntEnum):
    """How an alert is tied to a pin; lower ties rank first."""

    TRIP = 0
    STOP = 1
    ROUTE = 2


@dataclass(frozen=True)
class Matched:
    """
    An alert tied to the shown pin.

    Attributes
    ----------
    alert : Alert
        The alert.
    tie : AlertTie
        How it is tied to the pin.
    names_place : bool
        True when a route alert's text names one of the pin's places.
    """

    alert: Alert
    tie: AlertTie
    names_place: bool


@dataclass(frozen=True)
class AlertBanner:
    """
    The alert line shown in the banner.

    Attributes
    ----------
    text : str
        Line text.
    active : bool
        True when the alert applies now, False when it is coming.
    more : int
        How many other alerts could have taken the banner.
    """

    text: str
    active: bool
    more: int


@dataclass(frozen=True)
class LoadBetween:
    """
    Ask the timetable for the stations between a pin's From and To.

    Attributes
    ----------
    pin : Pin
        The pin.
    trip_ids : frozenset[str]
        Some of its trips.
    day : date
        Service day of the first of them.
    timetable : str | None
        Timetable version, echoed back.
    """

    pin: Pin
    trip_ids: frozenset[str]
    day: date
    timetable: str | None = None


@dataclass(frozen=True)
class BetweenLoaded:
    """
    Place names of the stations between a pin's From and To.

    Attributes
    ----------
    pin : Pin
        The pin asked about.
    names : frozenset[str]
        The names; empty when the read failed.
    timetable : str | None
        Timetable version asked of.
    families : Mapping[str, frozenset[str]]
        Stop families of From and To; empty when the read failed.
    trainlink : bool
        True when an NSW TrainLink trip calls at From or To.
    """

    pin: Pin
    names: frozenset[str]
    timetable: str | None = None
    families: Mapping[str, frozenset[str]] = field(default_factory=dict)
    trainlink: bool = False


@dataclass(frozen=True)
class AlertsState:
    """
    The alerts known and how they tie to the shown pin.

    Attributes
    ----------
    snapshots : Mapping[str, AlertsSnapshot]
        The latest download of each alert feed.
    fetched_at : Mapping[str, datetime]
        When each alert feed was last asked for.
    between : tuple[Pin, frozenset[str]] | None
        A pin and the place names between its From and To.
    families : Mapping[str, frozenset[str]]
        Stop families of the `between` pin's From and To.
    trainlink : bool
        The `between` pin's TrainLink answer.
    asked : tuple[Pin, str | None, bool] | None
        The pin and timetable version stations between were last asked
        for, and whether its trips were known then.
    matched : tuple[Pin, tuple[Matched, ...]] | None
        A pin and the alerts tied to it.
    selected : str | None
        ID of the alert selected in the list.
    page : int
        Page of the selected alert's text.
    """

    snapshots: Mapping[str, AlertsSnapshot] = field(default_factory=dict)
    fetched_at: Mapping[str, datetime] = field(default_factory=dict)
    between: tuple[Pin, frozenset[str]] | None = None
    families: Mapping[str, frozenset[str]] = field(default_factory=dict)
    trainlink: bool = False
    asked: tuple[Pin, str | None, bool] | None = None
    matched: tuple[Pin, tuple[Matched, ...]] | None = None
    selected: str | None = None
    page: int = 0
