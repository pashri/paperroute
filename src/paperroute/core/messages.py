"""The events the reducer takes and the effects it asks for."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from paperroute.core.chooser import ChooserPath, Column
from paperroute.core.chooser_view import ChooserView
from paperroute.core.feeds import FeedKind
from paperroute.core.geo import MetreBox, Shape
from paperroute.core.map_view import MapView
from paperroute.core.model import Departure, Mode, Pin, StopPlace
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.reach import Reach, StripKey
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import Saved
from paperroute.core.stop_view import StopView
from paperroute.core.strip_view import StripMapView, StripView


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
    """Ask the timetable for departures, tagged with its version."""

    pin: Pin
    start: datetime
    end: datetime
    timetable: str | None = None


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
class LoadStrip:
    """Build a strip's reach in a worker, tagged with the timetable version."""

    key: StripKey
    origin: StopPlace
    timetable: str | None = None


@dataclass(frozen=True)
class LoadColumn:
    """Ask for the chooser column after a path, tagged with the timetable."""

    path: ChooserPath
    timetable: str | None = None


@dataclass(frozen=True)
class LoadLine:
    """Ask for a towards row's stations, tagged with the timetable."""

    towards: int
    timetable: str | None = None


@dataclass(frozen=True)
class LoadChooserDepartures:
    """Ask for the departures from the chooser's From, tagged likewise."""

    pin: Pin
    start: datetime
    end: datetime
    timetable: str | None = None


@dataclass(frozen=True)
class Render:
    """Draw a view."""

    view: StopView | MapView | StripView | StripMapView | ChooserView


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


@dataclass(frozen=True)
class SaveState:
    """Write what the device keeps between restarts."""

    saved: Saved


@dataclass(frozen=True)
class MatchPlaces:
    """Look some places up in the timetable."""

    places: tuple[StopPlace, ...]


MapRequest = LoadShapes | LoadPatterns | LoadModes
ChooserLoad = LoadColumn | LoadLine | LoadChooserDepartures
Effect = (
    LoadDepartures
    | FetchFeed
    | LoadShapes
    | LoadPatterns
    | LoadModes
    | LoadStrip
    | Render
    | Stop
    | SaveState
    | MatchPlaces
    | ChooserLoad
)


@dataclass(frozen=True)
class Tick:
    """Time passed."""

    now: datetime


@dataclass(frozen=True)
class DeparturesLoaded:
    """Departures arrived for a pin, from the timetable version asked."""

    pin: Pin
    departures: tuple[Departure, ...]
    until: datetime
    timetable: str | None = None


@dataclass(frozen=True)
class DeparturesFailed:
    """The timetable couldn't be read for a pin; a later tick tries again."""

    pin: Pin
    timetable: str | None = None


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
class KnobHeld:
    """A knob was held down."""

    knob: Knob


@dataclass(frozen=True)
class QuitRequested:
    """The user asked to quit (development monitor only)."""


@dataclass(frozen=True)
class TimetableSeen:
    """The timetable in use was checked; None when there is none."""

    version: str | None


@dataclass(frozen=True)
class PlacesMatched:
    """
    Places were looked up in the timetable.

    Attributes
    ----------
    found : Mapping[str, StopPlace | None]
        The place now found for each old stop ID, or None when it is
        gone.
    """

    found: Mapping[str, StopPlace | None]


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
    box: MetreBox
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


@dataclass(frozen=True)
class StripLoaded:
    """
    A strip's reach was built.

    Attributes
    ----------
    key : StripKey
        The strip asked for.
    reach : Reach | None
        The reach, or None when the timetable has no run tables.
    timetable : str | None
        The timetable version it was built from.
    """

    key: StripKey
    reach: Reach | None
    timetable: str | None = None


@dataclass(frozen=True)
class StripFailed:
    """A strip's reach couldn't be built; a later tick tries again."""

    key: StripKey
    timetable: str | None = None


@dataclass(frozen=True)
class ColumnLoaded:
    """
    A chooser column arrived.

    Attributes
    ----------
    path : ChooserPath
        The path it follows.
    column : Column | None
        The column, or None when the timetable has no catalogue.
    timetable : str | None
        The timetable version it was read from.
    """

    path: ChooserPath
    column: Column | None
    timetable: str | None = None


@dataclass(frozen=True)
class ColumnFailed:
    """A chooser column couldn't be read."""

    path: ChooserPath
    timetable: str | None = None


@dataclass(frozen=True)
class LineLoaded:
    """
    A towards row's stations arrived.

    Attributes
    ----------
    towards : int
        Towards ID.
    line : tuple[StopPlace, ...] | None
        Stations in running order, or None when there are none to read.
    timetable : str | None
        The timetable version they were read from.
    """

    towards: int
    line: tuple[StopPlace, ...] | None
    timetable: str | None = None


@dataclass(frozen=True)
class LineFailed:
    """A towards row's stations couldn't be read."""

    towards: int
    timetable: str | None = None


@dataclass(frozen=True)
class ChooserDeparturesLoaded:
    """Departures from the chooser's From arrived, up to a time."""

    pin: Pin
    departures: tuple[Departure, ...]
    until: datetime
    timetable: str | None = None


@dataclass(frozen=True)
class ChooserDeparturesFailed:
    """The departures from the chooser's From couldn't be read."""

    pin: Pin
    timetable: str | None = None


ChooserEvent = (
    ColumnLoaded
    | ColumnFailed
    | LineLoaded
    | LineFailed
    | ChooserDeparturesLoaded
    | ChooserDeparturesFailed
)
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
    | KnobHeld
    | QuitRequested
    | TimetableSeen
    | PlacesMatched
    | StripLoaded
    | StripFailed
    | MapEvent
    | ChooserEvent
)
