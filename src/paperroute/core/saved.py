"""The Live views, and what the device keeps between restarts."""

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from enum import StrEnum
from typing import Final

from paperroute.core.auto_model import AutoState
from paperroute.core.geo import to_metres
from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.model import Pin, RouteKind, StopPlace


class LiveView(StrEnum):
    """The views the bottom knob press switches between."""

    STOP = 'stop'
    MAP = 'map'
    STRIP = 'strip'
    STRIP_MAP = 'strip_map'
    ALERTS = 'alerts'


LIVE_VIEWS: Final[tuple[LiveView, ...]] = (
    LiveView.STOP,
    LiveView.MAP,
    LiveView.STRIP,
    LiveView.STRIP_MAP,
)


class Rate(StrEnum):
    """How often the feeds are polled and the panel fully refreshed."""

    FAST = '30s'
    STEADY = '60s'
    SLOW = '2min'


@dataclass(frozen=True, order=True)
class Window:
    """
    A daily stretch of Sydney local time, from start up to end.

    Attributes
    ----------
    start : time
        The first moment inside.
    end : time
        The first moment after.
    """

    start: time
    end: time


class QuietMode(StrEnum):
    """How quiet hours are chosen."""

    MY_HOURS = 'my_hours'
    TIMETABLE = 'timetable'


class Lookahead(StrEnum):
    """How far ahead planned alerts are listed."""

    DAY = '1d'
    THREE_DAYS = '3d'
    WEEK = '7d'
    ALL = 'all'


class ConnectionMap(StrEnum):
    """How the Map shows a connection pin."""

    CHANGES = 'changes'
    WHOLE = 'whole'
    FIRST = 'first'


LOOKAHEAD: Final[dict[Lookahead, timedelta | None]] = {
    Lookahead.DAY: timedelta(days=1),
    Lookahead.THREE_DAYS: timedelta(days=3),
    Lookahead.WEEK: timedelta(days=7),
    Lookahead.ALL: None,
}
PEAK_WINDOWS: Final[tuple[Window, ...]] = (
    Window(start=time(6, 30), end=time(10)),
    Window(start=time(15), end=time(19)),
)
MY_HOURS: Final[Window] = Window(start=time(23), end=time(5, 30))


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """
    The choices made on the Settings page.

    Attributes
    ----------
    auto_rebuild : bool
        Whether an outdated timetable is rebuilt on the device.
    peak_rate : Rate
        The rate inside peak hours.
    offpeak_rate : Rate
        The rate outside peak hours.
    peak_windows : tuple[Window, ...]
        The weekday peak windows; none of them wraps midnight.
    quiet_mode : QuietMode
        How quiet hours are chosen.
    my_hours : Window
        The quiet hours chosen by hand; may wrap midnight.
    rotate : bool
        Whether the panel is turned 180 degrees.
    larger_text : bool
        Whether departure rows use larger text.
    inverted_lists : bool
        Whether departure rows are white on black.
    alert_lookahead : Lookahead
        How far ahead planned alerts are listed.
    connection_map : ConnectionMap
        Whether the Map zooms on each change, the first or the whole trip.
    skip_school_buses : bool
        Whether routes the timetable marks as school services are left
        out of pins with a To, the stop search and Trip Planner
        suggestions.
    """

    auto_rebuild: bool = True
    peak_rate: Rate = Rate.FAST
    offpeak_rate: Rate = Rate.SLOW
    peak_windows: tuple[Window, ...] = PEAK_WINDOWS
    quiet_mode: QuietMode = QuietMode.MY_HOURS
    my_hours: Window = MY_HOURS
    rotate: bool = False
    larger_text: bool = False
    inverted_lists: bool = False
    alert_lookahead: Lookahead = Lookahead.WEEK
    connection_map: ConnectionMap = ConnectionMap.CHANGES
    skip_school_buses: bool = True


@dataclass(frozen=True)
class Quota:
    """
    The API calls made on one Sydney day.

    Attributes
    ----------
    day : date | None
        The day the calls were made, or None before any were counted.
    calls : int
        The calls made that day.
    """

    day: date | None = None
    calls: int = 0


@dataclass(frozen=True)
class Saved:
    """
    The pins, the place shown, the Live view, the zoom and settings.

    Attributes
    ----------
    pins : tuple[Pin, ...]
        The pins, in knob order.
    current : int
        Index of the current pin; 0 when there are none.
    unpinned : Pin | None
        The place shown ahead of the pins, or None to show the current pin.
    view : LiveView
        The Live view shown.
    zoom : int
        The map zoom, an index into `ZOOMS`.
    settings : Settings
        The choices made on the Settings page.
    quota : Quota
        The API calls counted today.
    auto : AutoState
        What the auto pin rules have done.
    """

    pins: tuple[Pin, ...]
    current: int
    unpinned: Pin | None
    view: LiveView
    zoom: int
    settings: Settings = field(default_factory=Settings)
    quota: Quota = field(default_factory=Quota)
    auto: AutoState = field(default_factory=AutoState)


TOWN_HALL: Final[StopPlace] = StopPlace(
    stop_id='200070',
    name='Town Hall Station',
    point=to_metres(lon=151.206899293, lat=-33.8735963257),
)
DEFAULT_PLACE: Final[Pin] = Pin(origin=TOWN_HALL, kind=RouteKind.TRAIN)
DEFAULT_SAVED: Final[Saved] = Saved(
    pins=(),
    current=0,
    unpinned=DEFAULT_PLACE,
    view=LiveView.MAP,
    zoom=DEFAULT_ZOOM,
)


def seeded(*, pin: Pin) -> Saved:
    """
    Start with one pin, selected, on the Stop view.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    Saved
        The pin alone, current, with no unpinned place and the default
        zoom.
    """
    return Saved(
        pins=(pin,),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
        zoom=DEFAULT_ZOOM,
    )
