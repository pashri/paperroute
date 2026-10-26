"""The events the reducer takes and the effects it asks for."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from paperroute.core.alert_model import (
    AlertsFetched,
    BetweenLoaded,
    LoadBetween,
)
from paperroute.core.alerts_view import AlertsView
from paperroute.core.chooser import ChooserPath, Column
from paperroute.core.chooser_view import ChooserView
from paperroute.core.connection_model import Legs
from paperroute.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute.core.connection_view import ConnectionView
from paperroute.core.device_model import (
    ConfirmAction,
    DeviceFacts,
    JoinOutcome,
    JoinRequest,
    ResetKind,
    WifiNetwork,
)
from paperroute.core.feeds import FeedKind
from paperroute.core.geo import MetreBox, Shape
from paperroute.core.map_view import MapView
from paperroute.core.model import Departure, Mode, Pin, ServiceSpan, StopPlace
from paperroute.core.panel_views import PanelView, SleepView
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.reach import Reach, StripKey
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import (
    ConnectionMap,
    Lookahead,
    QuietMode,
    Rate,
    Saved,
    Window,
)
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import StripMapView, StripView


class Knob(StrEnum):
    """The two knobs."""

    TOP = 'top'
    BOTTOM = 'bottom'


class PowerAction(StrEnum):
    """What a power request does; the value is the systemctl verb."""

    OFF = 'poweroff'
    RESTART = 'reboot'


POWER_ACTIONS: Final[dict[str, PowerAction]] = {
    action.value: action for action in PowerAction
}


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
class LoadHolidays:
    """Ask for the public holidays a timetable version records."""

    timetable: str | None = None


@dataclass(frozen=True)
class LoadServiceSpan:
    """Ask when a pin's trips run on a service day, tagged likewise."""

    pin: Pin
    day: date
    timetable: str | None = None


@dataclass(frozen=True)
class NoticeView:
    """
    A screen showing a title and an optional line under it.

    Attributes
    ----------
    title : str
        The main line, such as the shutdown notice.
    detail : str
        A smaller line under the title, or empty.
    """

    title: str
    detail: str = ''


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
class Render:
    """
    Draw a view.

    Attributes
    ----------
    view : ScreenView
        The view.
    full : bool
        Whether the panel should do a full refresh rather than a partial
        one.
    """

    view: ScreenView
    full: bool = False


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


@dataclass(frozen=True)
class SaveState:
    """Write what the device keeps between restarts."""

    saved: Saved


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
class ControlPower:
    """Shut the device down or restart it."""

    action: PowerAction


@dataclass(frozen=True)
class ProbeDevice:
    """Gather the device's facts in the background."""


@dataclass(frozen=True)
class ResetDevice:
    """Reset the settings or erase everything, in the background."""

    kind: ResetKind


@dataclass(frozen=True)
class StartWifiSetup:
    """Scan for networks, then start the setup hotspot, in the background."""


@dataclass(frozen=True)
class JoinWifi:
    """
    Leave the hotspot and join a network, in the background.

    Attributes
    ----------
    request : JoinRequest
        The network to join.
    hotspot : str
        The hotspot password, to start it again if the join fails. Kept
        out of the repr.
    """

    request: JoinRequest
    hotspot: str = field(repr=False)


@dataclass(frozen=True)
class StopWifiSetup:
    """Take the setup hotspot down, in the background."""


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
    | Render
    | Stop
    | SaveState
    | MatchPlaces
    | RebuildTimetable
    | ControlPower
    | ProbeDevice
    | ResetDevice
    | StartWifiSetup
    | JoinWifi
    | StopWifiSetup
    | ChooserLoad
    | LoadHolidays
    | LoadServiceSpan
    | LoadBetween
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
    legs: Legs | None = None


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
    """A knob was held down and let go, with no turn while held."""

    knob: Knob


@dataclass(frozen=True)
class KnobsHeld:
    """Both knobs were held down together for the hold time."""


@dataclass(frozen=True)
class ConfirmAsked:
    """A phone asked for something the panel must confirm."""

    action: ConfirmAction


@dataclass(frozen=True)
class KnobHeldTurned:
    """A knob turned by some detents while held down."""

    knob: Knob
    steps: int


@dataclass(frozen=True)
class QuitRequested:
    """The user asked to quit (development monitor only)."""


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
    """

    version: str | None
    schema: str | None = None


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
    Settings were changed on the Settings page; None leaves one as it is.

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
    quiet_mode : QuietMode | None
        How quiet hours are chosen.
    my_hours : Window | None
        The quiet hours chosen by hand.
    rotate : bool | None
        Whether the panel is turned 180 degrees.
    larger_text : bool | None
        Whether departure rows use larger text.
    inverted_lists : bool | None
        Whether departure rows are white on black.
    alert_lookahead : Lookahead | None
        How far ahead planned alerts are listed.
    connection_map : ConnectionMap | None
        How the Map shows a connection pin.
    """

    auto_rebuild: bool | None = None
    peak_rate: Rate | None = None
    offpeak_rate: Rate | None = None
    peak_windows: tuple[Window, ...] | None = None
    quiet_mode: QuietMode | None = None
    my_hours: Window | None = None
    rotate: bool | None = None
    larger_text: bool | None = None
    inverted_lists: bool | None = None
    alert_lookahead: Lookahead | None = None
    connection_map: ConnectionMap | None = None


@dataclass(frozen=True)
class ApiKeyChanged:
    """
    The TfNSW API key was found set or not set.

    Attributes
    ----------
    present : bool
        Whether a key is set.
    """

    present: bool


@dataclass(frozen=True)
class DeviceProbed:
    """
    The device's facts were gathered.

    Attributes
    ----------
    facts : DeviceFacts
        What was found.
    """

    facts: DeviceFacts


@dataclass(frozen=True)
class DeviceProbeFailed:
    """Gathering the device's facts failed."""


@dataclass(frozen=True)
class PowerRequested:
    """Shutting down or restarting was asked for."""

    action: PowerAction


@dataclass(frozen=True)
class PowerFailed:
    """Shutting down or restarting didn't happen."""

    action: PowerAction


@dataclass(frozen=True)
class ResetDone:
    """A reset has finished, whether or not every part of it worked."""

    kind: ResetKind


@dataclass(frozen=True)
class HotspotStarted:
    """
    The setup hotspot is up.

    Attributes
    ----------
    networks : tuple[WifiNetwork, ...]
        The networks the scan before it found.
    password : str
        The hotspot password. Kept out of the repr.
    """

    networks: tuple[WifiNetwork, ...]
    password: str = field(repr=False)


@dataclass(frozen=True)
class HotspotFailed:
    """The setup hotspot did not start."""


@dataclass(frozen=True)
class JoinAsked:
    """A phone on the setup hotspot asked to join a network."""

    request: JoinRequest


@dataclass(frozen=True)
class WifiJoined:
    """The device joined a network from Wi-Fi setup."""

    ssid: str


@dataclass(frozen=True)
class WifiJoinFailed:
    """
    Joining a network from Wi-Fi setup failed.

    Attributes
    ----------
    ssid : str
        The network asked for.
    outcome : JoinOutcome
        How the join ended.
    hotspot : bool
        Whether the setup hotspot is back up.
    """

    ssid: str
    outcome: JoinOutcome
    hotspot: bool


@dataclass(frozen=True)
class WifiSetupStopped:
    """The setup hotspot was taken down."""


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
KnobEvent = KnobTurned | KnobPressed | KnobHeld | KnobHeldTurned
WifiEvent = (
    HotspotStarted
    | HotspotFailed
    | JoinAsked
    | WifiJoined
    | WifiJoinFailed
    | WifiSetupStopped
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
