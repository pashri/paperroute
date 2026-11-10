"""The events the reducer takes and the effects it asks for."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from paperroute_device.core.device_events import (
    ApiKeyChanged,
    ConfirmAsked,
    PowerRequested,
)
from paperroute_device.core.device_settings import Window
from paperroute_device.core.kit_effects import KitEffect
from paperroute_device.core.kit_events import (
    DeviceProbed,
    DeviceProbeFailed,
    PowerFailed,
    QuitRequested,
    ResetDone,
    Tick,
    WifiEvent,
)
from paperroute_device.core.panel_views import NoticeView, PanelView
from paperroute_hardware.core.knob_events import (
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_transit.core.alert_model import (
    AlertsFetched,
    BetweenLoaded,
    LoadBetween,
)
from paperroute_transit.core.alerts_view import AlertsView
from paperroute_transit.core.chains import Legs
from paperroute_transit.core.chooser import ChooserPath, Column
from paperroute_transit.core.chooser_view import ChooserView
from paperroute_transit.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute_transit.core.connection_view import ConnectionView
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import MetreBox, Shape
from paperroute_transit.core.map_view import MapView
from paperroute_transit.core.model import (
    Departure,
    Pin,
    ServiceSpan,
    StopPlace,
)
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.reach import Reach, StripKey
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.saved import ConnectionMap, Lookahead, Rate
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.core.strip_model import StripMapView, StripView


class MapData(StrEnum):
    """The kinds of map data loaded from the timetable."""

    SHAPES = 'shapes'
    PATTERNS = 'patterns'
    MODES = 'modes'


@dataclass(frozen=True)
class LoadDepartures:
    """
    Ask the timetable for departures, tagged with its version.

    `skip_school` leaves school routes out of a pin with a To;
    `skip_school_pins` leaves them out of a pin without one.
    """

    pin: Pin
    start: datetime
    end: datetime
    timetable: str | None = None
    skip_school: bool = True
    skip_school_pins: bool = False


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
class LoadHolidays:
    """Ask for the public holidays a timetable version records."""

    timetable: str | None = None


@dataclass(frozen=True)
class LoadServiceSpan:
    """Ask when a pin's trips run on a service day, tagged likewise."""

    pin: Pin
    day: date
    timetable: str | None = None


ScreenView = (
    StopView
    | MapView
    | StripView
    | StripMapView
    | ConnectionView
    | ConnectionStripView
    | ConnectionStripMapView
    | AlertsView
    | ChooserView
    | NoticeView
    | PanelView
    | SleepView
)


@dataclass(frozen=True)
class RebuildTimetable:
    """
    Rebuild the timetable in the background.

    Attributes
    ----------
    force : bool
        Whether to build even when the timetable is up to date.
    """

    force: bool = False


@dataclass(frozen=True)
class MatchPlaces:
    """Look some places up in the timetable, tagged with its version."""

    places: tuple[StopPlace, ...]
    timetable: str | None = None


MapRequest = LoadShapes | LoadPatterns | LoadModes
ChooserLoad = LoadColumn | LoadLine | LoadChooserDepartures
Effect = (
    LoadDepartures
    | FetchFeed
    | LoadShapes
    | LoadPatterns
    | LoadModes
    | LoadStrip
    | MatchPlaces
    | RebuildTimetable
    | ChooserLoad
    | LoadHolidays
    | LoadServiceSpan
    | LoadBetween
    | KitEffect
)


@dataclass(frozen=True)
class DeparturesLoaded:
    """
    Departures arrived for a pin, from the timetable version asked.

    `skip_school` and `skip_school_pins` echo the load's.
    """

    pin: Pin
    departures: tuple[Departure, ...]
    until: datetime
    timetable: str | None = None
    legs: Legs | None = None
    skip_school: bool = True
    skip_school_pins: bool = False


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
class FeedJoined:
    """A fetch shared another fetch's request, so it sent none of its own."""

    feed: str


@dataclass(frozen=True)
class FeedFailed:
    """
    A realtime fetch failed.

    Attributes
    ----------
    feed : str
        Feed path.
    at : datetime
        When it failed.
    reason : str
        What went wrong.
    sent : bool
        False when no request went out, because the feed was backing off.
    """

    feed: str
    at: datetime
    reason: str
    sent: bool = True


@dataclass(frozen=True)
class TimetableSeen:
    """
    The timetable in use was checked; None when there is none.

    Attributes
    ----------
    version : str | None
        The build time.
    schema : str | None
        The file's schema version, or None when it records none.
    origin : str | None
        The file's projection origin text, or None when it records none.
    last_day : date | None
        The last day the file has service for every source, or None
        when it records no window.
    """

    version: str | None
    schema: str | None = None
    origin: str | None = None
    last_day: date | None = None


@dataclass(frozen=True)
class HolidaysLoaded:
    """
    The public holidays a timetable records; none when the read failed.

    Attributes
    ----------
    dates : frozenset[date]
        The holidays.
    timetable : str | None
        The timetable version they were read from.
    """

    dates: frozenset[date]
    timetable: str | None = None


@dataclass(frozen=True)
class ServiceSpanLoaded:
    """
    When a pin's trips run on a service day; none when the read failed.

    Attributes
    ----------
    pin : Pin
        The pin asked about.
    day : date
        The service day.
    span : ServiceSpan | None
        The span, or None for no trips or a failed read.
    timetable : str | None
        The timetable version it was read from.
    """

    pin: Pin
    day: date
    span: ServiceSpan | None
    timetable: str | None = None


@dataclass(frozen=True)
class TimetableRebuilt:
    """A timetable rebuild finished; ok is True when it succeeded."""

    ok: bool


@dataclass(frozen=True)
class SettingsChanged:  # pylint: disable=too-many-instance-attributes
    """
    Board settings were changed on the Settings page; None leaves one.

    Attributes
    ----------
    auto_rebuild : bool | None
        Whether an outdated timetable is rebuilt on the device.
    peak_rate : Rate | None
        The rate inside peak hours.
    offpeak_rate : Rate | None
        The rate outside peak hours.
    peak_windows : tuple[Window, ...] | None
        The weekday peak windows.
    alert_lookahead : Lookahead | None
        How far ahead planned alerts are listed.
    connection_map : ConnectionMap | None
        How the Map shows a connection pin.
    skip_school_buses : bool | None
        Whether school routes are left out.
    skip_school_on_route_pins : bool | None
        Whether school routes are also left out of pins without a To.
    """

    auto_rebuild: bool | None = None
    peak_rate: Rate | None = None
    offpeak_rate: Rate | None = None
    peak_windows: tuple[Window, ...] | None = None
    alert_lookahead: Lookahead | None = None
    connection_map: ConnectionMap | None = None
    skip_school_buses: bool | None = None
    skip_school_on_route_pins: bool | None = None


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
class PlacesFailed:
    """The places couldn't be looked up; a later tick tries again."""

    timetable: str | None = None


@dataclass(frozen=True)
class PinSaved:
    """
    A pin was added or edited.

    Attributes
    ----------
    ref : str | None
        The ref of the pin to replace, or None to add a pin.
    pin : Pin
        The pin to keep.
    """

    ref: str | None
    pin: Pin


@dataclass(frozen=True)
class PinDeleted:
    """A pin was deleted, named by its ref."""

    ref: str


@dataclass(frozen=True)
class PinMoved:
    """
    A pin was moved along the knob order.

    Attributes
    ----------
    ref : str
        The pin's ref.
    steps : int
        How far to move it; negative is earlier.
    """

    ref: str
    steps: int


@dataclass(frozen=True)
class PinsOrdered:
    """The pins were put in a new order, named by their refs."""

    refs: tuple[str, ...]


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
    modes: frozenset[str]


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
PinEdit = PinSaved | PinDeleted | PinMoved | PinsOrdered
Event = (
    Tick
    | DeparturesLoaded
    | DeparturesFailed
    | FeedFetched
    | FeedFailed
    | FeedJoined
    | AlertsFetched
    | BetweenLoaded
    | KnobTurned
    | KnobPressed
    | KnobHeld
    | KnobHeldTurned
    | KnobsHeld
    | ConfirmAsked
    | QuitRequested
    | TimetableSeen
    | TimetableRebuilt
    | HolidaysLoaded
    | ServiceSpanLoaded
    | SettingsChanged
    | ApiKeyChanged
    | DeviceProbed
    | DeviceProbeFailed
    | PowerRequested
    | PowerFailed
    | ResetDone
    | PlacesMatched
    | PlacesFailed
    | StripLoaded
    | StripFailed
    | WifiEvent
    | MapEvent
    | ChooserEvent
    | PinEdit
)
