"""What the device knows, and the timings both its halves share."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.chooser import ChooserState
from paperroute.core.map_state import MapState
from paperroute.core.messages import PowerAction, ScreenView
from paperroute.core.model import Departure, Pin, shifted
from paperroute.core.reach import Reach, StripKey
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import LiveView, Saved, Settings
from paperroute.core.stop_view import HORIZON

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)
RETRY_AFTER: Final[timedelta] = timedelta(seconds=30)
FEED_IDLE: Final[timedelta] = timedelta(minutes=10)


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


def load_window(*, now: datetime) -> tuple[datetime, datetime]:
    """
    Choose the span of departures to load from a stop.

    Parameters
    ----------
    now : datetime
        Current time.

    Returns
    -------
    tuple[datetime, datetime]
        From `LOOKBACK` before now to `HORIZON` and `RELOAD_BEFORE`
        after, in real seconds.
    """
    ahead = HORIZON + RELOAD_BEFORE
    return (
        shifted(moment=now, seconds=-int(LOOKBACK.total_seconds())),
        shifted(moment=now, seconds=int(ahead.total_seconds())),
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
    """

    departures: tuple[Departure, ...] = ()
    loaded_until: datetime | None = None
    loading: bool = False


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
    """

    saved: Saved | None = None
    changed_at: datetime | None = None
    timetable: str | None = None
    match_failed_at: datetime | None = None


@dataclass(frozen=True)
class Rebuild:
    """
    Whether the timetable needs rebuilding, and when to try again.

    Attributes
    ----------
    outdated : bool
        True when the timetable's schema is older than the app's.
    running : bool
        True while a rebuild runs.
    failures : int
        Rebuilds failed in a row.
    retry_at : datetime | None
        The earliest time for the next rebuild, or None for now.
    """

    outdated: bool = False
    running: bool = False
    failures: int = 0
    retry_at: datetime | None = None


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
    """

    reaches: Mapping[StripKey, Reach | None] = field(default_factory=dict)
    pending: frozenset[StripKey] = frozenset()
    failed_at: datetime | None = None
    scroll: int = 0
    asked: int = 0
    bounds: tuple[int, int] | None = None
    opened: frozenset[str] = frozenset()
    context: FoldContext | None = None


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
    """

    now: datetime
    places: Places
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
