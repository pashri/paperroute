"""The Live views, and what the device keeps between restarts."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Final

from paperroute_device.core.device_settings import DeviceSettings, Window
from paperroute_transit.core.auto_model import AutoState
from paperroute_transit.core.city import City
from paperroute_transit.core.model import Pin


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
    """How often the feeds are polled."""

    FAST = '30s'
    STEADY = '60s'
    SLOW = '2min'


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
    FIRST_LEG = 'first_leg'
    START = 'start'


LOOKAHEAD: Final[dict[Lookahead, timedelta | None]] = {
    Lookahead.DAY: timedelta(days=1),
    Lookahead.THREE_DAYS: timedelta(days=3),
    Lookahead.WEEK: timedelta(days=7),
    Lookahead.ALL: None,
}


@dataclass(frozen=True)
class BoardSettings:
    """
    The transit board's choices on the Settings page.

    These are `Settings.board`; the departures shown are `State.board`,
    a different thing.

    Attributes
    ----------
    peak_rate : Rate
        The rate inside peak hours.
    offpeak_rate : Rate
        The rate outside peak hours.
    peak_windows : tuple[Window, ...]
        The weekday peak windows; none of them wraps midnight. A city's
        defaults come from `default_settings`.
    alert_lookahead : Lookahead
        How far ahead planned alerts are listed.
    connection_map : ConnectionMap
        How the Map frames a connection pin.
    skip_school_buses : bool
        Whether routes the timetable marks as school services are left
        out of pins with a To, the stop search and Trip Planner
        suggestions.
    skip_school_on_route_pins : bool
        Whether school routes are also left out of pins without a To;
        it has no effect while `skip_school_buses` is off.
    """

    peak_rate: Rate = Rate.FAST
    offpeak_rate: Rate = Rate.SLOW
    peak_windows: tuple[Window, ...] = ()
    alert_lookahead: Lookahead = Lookahead.WEEK
    connection_map: ConnectionMap = ConnectionMap.CHANGES
    skip_school_buses: bool = True
    skip_school_on_route_pins: bool = False

    @property
    def school_off_route_pins(self) -> bool:
        """
        Say whether school routes are left out of pins without a To.

        Returns
        -------
        bool
            True when both school switches are on.
        """
        return self.skip_school_buses and self.skip_school_on_route_pins


@dataclass(frozen=True)
class Settings:
    """
    The choices made on the Settings page.

    Attributes
    ----------
    device : DeviceSettings
        The device's own choices.
    board : BoardSettings
        The transit board's choices.
    """

    device: DeviceSettings = field(default_factory=DeviceSettings)
    board: BoardSettings = field(default_factory=BoardSettings)


DEVICE_FIELDS: Final[frozenset[str]] = frozenset(
    item.name for item in fields(DeviceSettings)
)


def with_changes(
    *, settings: Settings, changes: Mapping[str, Any]
) -> Settings:
    """
    Change settings named by their flat Settings page names.

    Parameters
    ----------
    settings : Settings
        The settings.
    changes : Mapping[str, Any]
        New values by field name, from either part.

    Returns
    -------
    Settings
        The settings with each value on its own part.
    """
    device = {
        name: value
        for name, value in changes.items()
        if name in DEVICE_FIELDS
    }
    board = {
        name: value
        for name, value in changes.items()
        if name not in DEVICE_FIELDS
    }
    return Settings(
        device=replace(settings.device, **device),
        board=replace(settings.board, **board),
    )


@dataclass(frozen=True)
class Quota:
    """
    The API calls made on one local day.

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
    The pins, the place shown, the Live view and settings.

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
    settings: Settings = field(default_factory=Settings)
    quota: Quota = field(default_factory=Quota)
    auto: AutoState = field(default_factory=AutoState)


def default_settings(*, city: City) -> Settings:
    """
    Give a city's default settings.

    Parameters
    ----------
    city : City
        The city.

    Returns
    -------
    Settings
        The defaults, with the city's peak windows.
    """
    return Settings(board=BoardSettings(peak_windows=city.peak_windows))


def default_saved(*, city: City) -> Saved:
    """
    Give what a device starts with when nothing is saved.

    Parameters
    ----------
    city : City
        The city.

    Returns
    -------
    Saved
        No pins, the city's default pin unpinned, the Map view and the
        city's default settings.
    """
    return Saved(
        pins=(),
        current=0,
        unpinned=city.default_pin,
        view=LiveView.MAP,
        settings=default_settings(city=city),
    )


def seeded(*, pin: Pin, city: City) -> Saved:
    """
    Start with one pin, selected, on the Stop view.

    Parameters
    ----------
    pin : Pin
        The pin.
    city : City
        The city, whose default settings it starts with.

    Returns
    -------
    Saved
        The pin alone, current, with no unpinned place.
    """
    return Saved(
        pins=(pin,),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
        settings=default_settings(city=city),
    )
