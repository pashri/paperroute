"""The events the reducer takes and the effects it asks for."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from paperroute.core.feeds import FeedKind
from paperroute.core.geo import MetreBox, Shape
from paperroute.core.map_view import MapView
from paperroute.core.model import Departure, Mode, Pin
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.stop_view import StopView


class Knob(StrEnum):
    """The two knobs."""

    TOP = 'top'
    BOTTOM = 'bottom'


class MapData(StrEnum):
    """The kinds of map data loaded from the timetable."""

    SHAPES = 'shapes'
    PATTERNS = 'patterns'
    MODES = 'modes'


@dataclass(frozen=True)
class LoadDepartures:
    """Ask the timetable for departures."""

    pin: Pin
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FetchFeed:
    """Download a realtime feed."""

    feed: str
    kind: FeedKind = FeedKind.TRIPS


@dataclass(frozen=True)
class LoadShapes:
    """Ask the timetable for shapes."""

    shape_ids: frozenset[str]


@dataclass(frozen=True)
class LoadPatterns:
    """Ask the timetable for trip patterns around a day."""

    trip_ids: frozenset[str]
    day: date


@dataclass(frozen=True)
class LoadModes:
    """Ask the timetable for the modes in an area."""

    mpp: int
    box: MetreBox


@dataclass(frozen=True)
class Render:
    """Draw a view."""

    view: StopView | MapView


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


MapRequest = LoadShapes | LoadPatterns | LoadModes
Effect = (
    LoadDepartures
    | FetchFeed
    | LoadShapes
    | LoadPatterns
    | LoadModes
    | Render
    | Stop
)


@dataclass(frozen=True)
class Tick:
    """Time passed."""

    now: datetime


@dataclass(frozen=True)
class DeparturesLoaded:
    """Departures arrived from the timetable."""

    departures: tuple[Departure, ...]
    until: datetime


@dataclass(frozen=True)
class DeparturesFailed:
    """The timetable couldn't be read; the next tick tries again."""


@dataclass(frozen=True)
class FeedFetched:
    """A realtime feed was downloaded and parsed."""

    feed: str
    snapshot: RealtimeSnapshot


@dataclass(frozen=True)
class FeedFailed:
    """A realtime fetch failed."""

    feed: str
    at: datetime
    reason: str


@dataclass(frozen=True)
class KnobTurned:
    """A knob turned by some detents (negative is anticlockwise)."""

    knob: Knob
    steps: int


@dataclass(frozen=True)
class KnobPressed:
    """A knob was pressed."""

    knob: Knob


@dataclass(frozen=True)
class QuitRequested:
    """The user asked to quit (development monitor only)."""


@dataclass(frozen=True)
class ShapesLoaded:
    """Shapes arrived from the timetable."""

    shapes: Mapping[str, Shape]


@dataclass(frozen=True)
class PatternsLoaded:
    """Trip patterns and their shapes arrived from the timetable.

    Attributes
    ----------
    trip_ids : frozenset[str]
        Every trip ID the load asked for, found or not.
    patterns : Mapping[str, TripPattern | None]
        Patterns by trip ID; a missing or None entry is an unknown trip.
    shapes : Mapping[str, Shape]
        The patterns' shapes by shape ID.
    """

    trip_ids: frozenset[str]
    patterns: Mapping[str, TripPattern | None]
    shapes: Mapping[str, Shape]


@dataclass(frozen=True)
class ModesLoaded:
    """The modes in the map area at one zoom arrived from the timetable."""

    mpp: int
    modes: frozenset[Mode]


@dataclass(frozen=True)
class PositionsFetched:
    """A positions feed was downloaded and parsed."""

    feed: str
    snapshot: PositionsSnapshot


@dataclass(frozen=True)
class MapDataFailed:
    """A map data load failed."""

    request: MapRequest

    @property
    def kind(self) -> MapData:
        """
        Name the kind of map data that failed.

        Returns
        -------
        MapData
            The kind of the failed request.
        """
        match self.request:
            case LoadShapes():
                return MapData.SHAPES
            case LoadPatterns():
                return MapData.PATTERNS
        return MapData.MODES


MapEvent = (
    ShapesLoaded
    | PatternsLoaded
    | ModesLoaded
    | PositionsFetched
    | MapDataFailed
)
Event = (
    Tick
    | DeparturesLoaded
    | DeparturesFailed
    | FeedFetched
    | FeedFailed
    | KnobTurned
    | KnobPressed
    | QuitRequested
    | MapEvent
)
