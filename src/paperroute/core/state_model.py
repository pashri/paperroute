"""What the device knows, and the timings both its halves share."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.map_state import MapState
from paperroute.core.map_view import MapView
from paperroute.core.model import Departure, Pin
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.saved import LiveView, Saved
from paperroute.core.stop_view import StopView

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)
RETRY_AFTER: Final[timedelta] = timedelta(seconds=30)
FEED_IDLE: Final[timedelta] = timedelta(minutes=10)


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
    """

    saved: Saved | None = None
    changed_at: datetime | None = None
    timetable: str | None = None


@dataclass(frozen=True)
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
    last_view : StopView | MapView | None
        The view last drawn.
    running : bool
        False once the app should stop.
    view : LiveView
        The Live view shown.
    map : MapState
        The map's zoom, centre and data.
    saving : Saving
        What was last saved and the timetable last matched against.
    """

    now: datetime
    places: Places
    board: Board = field(default_factory=Board)
    feeds: tuple[str, ...] = ()
    snapshots: Mapping[str, RealtimeSnapshot] = field(default_factory=dict)
    last_fetch: datetime | None = None
    scroll: int = 0
    last_view: StopView | MapView | None = None
    running: bool = True
    view: LiveView = LiveView.STOP
    map: MapState = field(default_factory=MapState)
    saving: Saving = field(default_factory=Saving)
