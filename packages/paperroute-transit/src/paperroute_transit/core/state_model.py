"""What the device knows, and the timings both its halves share."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Final

from paperroute_device.core.device_model import Panel, PowerAction
from paperroute_transit.core.alert_model import AlertsState
from paperroute_transit.core.auto_model import AutoState
from paperroute_transit.core.chains import Legs
from paperroute_transit.core.chooser import ChooserState
from paperroute_transit.core.city import City
from paperroute_transit.core.map_state import MapState
from paperroute_transit.core.messages import ScreenView
from paperroute_transit.core.model import Departure, Pin, ServiceSpan, shifted
from paperroute_transit.core.reach import Reach, StripKey
from paperroute_transit.core.realtime import RealtimeSnapshot
from paperroute_transit.core.saved import LiveView, Quota, Saved, Settings
from paperroute_transit.core.stop_view import HORIZON
from paperroute_transit.core.strip_model import TripDay

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)
RETRY_AFTER: Final[timedelta] = timedelta(seconds=30)
FEED_IDLE: Final[timedelta] = timedelta(minutes=10)
CLOCK_WAIT: Final[timedelta] = timedelta(minutes=2)


def elapsed(*, since: datetime | None, now: datetime, span: timedelta) -> bool:
    """
    Say whether a span of real time has passed since a moment.

    Parameters
    ----------
    since : datetime | None
        The moment, or None for never.
    now : datetime
        Current time.
    span : timedelta
        Span of real time.

    Returns
    -------
    bool
        True when `since` is None or at least `span` ago.
    """
    if since is None:
        return True
    return now.timestamp() - since.timestamp() >= span.total_seconds()


def load_window(
    *, now: datetime, zone: tzinfo,
) -> tuple[datetime, datetime]:
    """
    Choose the span of departures to load from a stop.

    Parameters
    ----------
    now : datetime
        Current time.
    zone : tzinfo
        Local time zone of the span's ends.

    Returns
    -------
    tuple[datetime, datetime]
        From `LOOKBACK` before now to `HORIZON` and `RELOAD_BEFORE`
        after, in real seconds.
    """
    ahead = HORIZON + RELOAD_BEFORE
    return (
        shifted(
            moment=now, seconds=-int(LOOKBACK.total_seconds()), zone=zone,
        ),
        shifted(moment=now, seconds=int(ahead.total_seconds()), zone=zone),
    )


def runs_short(*, loaded_until: datetime | None, now: datetime) -> bool:
    """
    Say whether loaded departures are missing or reach under HORIZON.

    Parameters
    ----------
    loaded_until : datetime | None
        The end of the loaded window, or None before the first load.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when less than HORIZON of real time remains loaded.
    """
    if loaded_until is None:
        return True
    left = loaded_until.timestamp() - now.timestamp()
    return left < HORIZON.total_seconds()


@dataclass(frozen=True)
class Board:
    """
    The departures loaded for the place shown.

    Attributes
    ----------
    departures : tuple[Departure, ...]
        Departures, in timetable order.
    loaded_until : datetime | None
        The end of the loaded window, or None before the first load.
    loading : bool
        True while a load is in flight.
    legs : Legs | None
        A connection pin's leg-2 and direct departures; None otherwise.
    """

    departures: tuple[Departure, ...] = ()
    loaded_until: datetime | None = None
    loading: bool = False
    legs: Legs | None = None


@dataclass(frozen=True)
class Places:
    """
    The pins and which place is shown.

    Attributes
    ----------
    pins : tuple[Pin, ...]
        The pins, in knob order.
    current : int
        Index of the current pin; 0 when there are none.
    unpinned : Pin | None
        The place shown ahead of the pins, or None to show the current pin.
    """

    pins: tuple[Pin, ...] = ()
    current: int = 0
    unpinned: Pin | None = None


@dataclass(frozen=True)
class Saving:
    """
    What was last saved, and when the saved state last changed.

    Attributes
    ----------
    saved : Saved | None
        What was last written, or None when nothing has been.
    changed_at : datetime | None
        When the saved state last changed, or None since start.
    timetable : str | None
        The timetable version the places were last matched against.
    match_failed_at : datetime | None
        When matching the places last failed, or None when no retry is
        due.
    quota_at : datetime | None
        When a quota was last written, or None since start.
    """

    saved: Saved | None = None
    changed_at: datetime | None = None
    timetable: str | None = None
    match_failed_at: datetime | None = None
    quota_at: datetime | None = None


@dataclass(frozen=True)
class Rebuild:
    """
    Whether the timetable needs rebuilding, and when to try again.

    Attributes
    ----------
    outdated : bool
        True when the timetable's schema is older than the app's, or it
        was built for another projection origin.
    running : bool
        True while a rebuild runs.
    failures : int
        Rebuilds failed in a row.
    retry_at : datetime | None
        The earliest time for the next rebuild, or None for now.
    schema : str | None
        The schema version of the timetable seen last.
    origin : str | None
        The projection origin text of the timetable seen last.
    missing : bool
        True when there is no timetable.
    forced : bool
        True while the rebuild running was asked for by hand.
    failed : bool
        True when the last rebuild failed.
    """

    outdated: bool = False
    running: bool = False
    failures: int = 0
    retry_at: datetime | None = None
    schema: str | None = None
    origin: str | None = None
    missing: bool = False
    forced: bool = False
    failed: bool = False


type FoldContext = tuple[str, StripKey | None, str | None]


@dataclass(frozen=True)
class StripState:
    """
    The strips' reaches and the Strip's scroll.

    Attributes
    ----------
    reaches : Mapping[StripKey, Reach | None]
        Built reaches by key; None when the timetable has no run tables.
    pending : frozenset[StripKey]
        Keys whose reaches are being built.
    failed_at : datetime | None
        When a reach last failed to build, or None.
    scroll : int
        The Strip's scroll, in rows from the default; may be negative.
    asked : int
        The scroll last set, which `scroll` keeps to as far as the rows
        allow, so rows that shrink for a while don't lose it.
    bounds : tuple[int, int] | None
        The lowest and highest scroll of the rows last shown, which a
        turn of the knob stays within; None before any.
    opened : frozenset[str]
        Keys of the folds opened on the strip on screen.
    context : FoldContext | None
        Which strip on screen `opened` belongs to: the view or chooser
        stage, the strip and the Destination; None off the strips.
    selected : TripDay | None
        The trip ID and service day of the panel train picked on the
        Strip, whose times the rows below the From show; None for the
        next train.
    """

    reaches: Mapping[StripKey, Reach | None] = field(default_factory=dict)
    pending: frozenset[StripKey] = frozenset()
    failed_at: datetime | None = None
    scroll: int = 0
    asked: int = 0
    bounds: tuple[int, int] | None = None
    opened: frozenset[str] = frozenset()
    context: FoldContext | None = None
    selected: TripDay | None = None


@dataclass(frozen=True)
class Calendar:
    """
    What the timetable says about the days ahead.

    Attributes
    ----------
    holidays : frozenset[date]
        The public holidays the timetable records.
    spans : Mapping[date, ServiceSpan | None]
        When the shown pin's trips run, by service day; None for a day
        with no trips or a failed read.
    pending : frozenset[date]
        Service days whose spans are being read.
    spans_for : tuple[Pin, str | None] | None
        The pin and timetable version the spans are for, or None.
    """

    holidays: frozenset[date] = frozenset()
    spans: Mapping[date, ServiceSpan | None] = field(default_factory=dict)
    pending: frozenset[date] = frozenset()
    spans_for: tuple[Pin, str | None] | None = None


@dataclass(frozen=True)
class Refresh:
    """
    When the app started and the knobs were touched, and any menu shown.

    Attributes
    ----------
    started_at : datetime | None
        The time of the first event, or None before it.
    touched_at : datetime | None
        The last knob touch, or None before the first.
    in_menu : bool
        Whether the last frame rendered was a menu or the route chooser.
    """

    started_at: datetime | None = None
    touched_at: datetime | None = None
    in_menu: bool = False


@dataclass(frozen=True)
class Cycle:
    """
    The poll cycle under way.

    Attributes
    ----------
    started : datetime | None
        When the cycle began, or None before the first.
    alerts_at : datetime | None
        When the alerts were last asked for, or None before the first.
    asked : frozenset[str]
        The feeds asked for in this cycle.
    waiting : frozenset[str]
        The asked feeds that have not answered yet.
    free : bool
        Whether the cycle's frame may be drawn at once.
    """

    started: datetime | None = None
    alerts_at: datetime | None = None
    asked: frozenset[str] = frozenset()
    waiting: frozenset[str] = frozenset()
    free: bool = False


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class State:
    """
    Everything the app knows.

    Attributes
    ----------
    now : datetime
        The latest time seen.
    places : Places
        The pins and the place shown.
    city : City
        The city the app runs for.
    board : Board
        The shown place's departures.
    feeds : tuple[str, ...]
        The trip-updates feeds the departures need.
    snapshots : Mapping[str, RealtimeSnapshot]
        The latest download of each trip-updates feed.
    last_fetch : datetime | None
        When the trip-updates feeds were last asked for.
    scroll : int
        The Stop view's scroll.
    last_view : ScreenView | None
        The view last drawn.
    running : bool
        False once the app should stop.
    view : LiveView
        The Live view shown.
    map : MapState
        The map's zoom, centre and data.
    saving : Saving
        What was last saved and the timetable last matched against.
    strip : StripState
        The strips' reaches and the Strip's scroll.
    chooser : ChooserState | None
        The route chooser, or None while it is closed.
    rebuild : Rebuild
        Whether the timetable needs rebuilding.
    settings : Settings
        The choices made on the Settings page.
    power : PowerAction | None
        The power action under way; while set, nothing is redrawn.
    quota : Quota
        The API calls counted today.
    panel : Panel
        The device's own state.
    calendar : Calendar
        The public holidays, from the timetable.
    refresh : Refresh
        The full-refresh schedule.
    alerts : AlertsState
        The alerts and their ties to the shown pin.
    auto : AutoState
        What the auto pin rules have done.
    cycle : Cycle
        The poll cycle under way.
    clock_wait_until : datetime | None
        When to stop waiting for the clock to be synchronised, or None
        when not waiting.
    clock_gave_up : bool
        True once the wait has run out unsynchronised; the hold is over
        for good, but a later sync still starts afresh.
    clock_known : bool
        False from start-up until the first tick says whether the clock
        is synchronised; the app waits for the clock meanwhile.
    """

    now: datetime
    places: Places
    city: City = field(repr=False)
    board: Board = field(default_factory=Board)
    feeds: tuple[str, ...] = ()
    snapshots: Mapping[str, RealtimeSnapshot] = field(default_factory=dict)
    last_fetch: datetime | None = None
    scroll: int = 0
    last_view: ScreenView | None = None
    running: bool = True
    view: LiveView = LiveView.STOP
    map: MapState = field(default_factory=MapState)
    saving: Saving = field(default_factory=Saving)
    strip: StripState = field(default_factory=StripState)
    chooser: ChooserState | None = None
    rebuild: Rebuild = field(default_factory=Rebuild)
    settings: Settings = field(default_factory=Settings)
    power: PowerAction | None = None
    quota: Quota = field(default_factory=Quota)
    panel: Panel = field(default_factory=Panel)
    calendar: Calendar = field(default_factory=Calendar)
    refresh: Refresh = field(default_factory=Refresh)
    alerts: AlertsState = field(default_factory=AlertsState)
    auto: AutoState = field(default_factory=AutoState)
    cycle: Cycle = field(default_factory=Cycle)
    clock_wait_until: datetime | None = None
    clock_gave_up: bool = False
    clock_known: bool = True


def clock_waiting(*, state: State) -> bool:
    """
    Say whether the app still waits for the clock to be synchronised.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True before the first tick, and while a wait is set, has not been
        given up, and its end has not come, in real time.
    """
    if not state.clock_known:
        return True
    until = state.clock_wait_until
    if until is None or state.clock_gave_up:
        return False
    return state.now.timestamp() < until.timestamp()


def restarted(*, state: State) -> State:
    """
    Forget the cycle under way, so the next one starts at once.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with a cycle not yet started that keeps when the alerts
        were last asked for and whether its frame is free.
    """
    cycle = Cycle(alerts_at=state.cycle.alerts_at, free=state.cycle.free)
    return replace(state, cycle=cycle)
