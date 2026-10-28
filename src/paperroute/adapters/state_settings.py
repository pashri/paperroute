"""Settings as stored in the state file, and their defaults."""

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from paperroute.adapters.state_auto import CLOCK, CLOCK_FORMAT
from paperroute.core.saved import (
    ConnectionMap,
    Lookahead,
    QuietMode,
    Rate,
    Settings,
    Window,
)

MAX_PEAK_WINDOWS: Final[int] = 2

class WindowModel(BaseModel):
    """A daily window as stored, each end as ``HH:MM``."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    start: str = Field(pattern=CLOCK)
    end: str = Field(pattern=CLOCK)


class SettingsModel(BaseModel):
    """
    The Settings page's choices as stored.

    The school switch has a default, so files stored before it load.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    auto_rebuild: bool
    peak_rate: Rate
    offpeak_rate: Rate
    peak_windows: tuple[WindowModel, ...] = Field(
        max_length=MAX_PEAK_WINDOWS,
    )
    quiet_mode: QuietMode
    my_hours: WindowModel
    rotate: bool
    larger_text: bool
    inverted_lists: bool
    alert_lookahead: Lookahead
    connection_map: ConnectionMap
    skip_school_buses: bool = True
    skip_school_on_route_pins: bool = False

    @field_validator('peak_windows')
    @classmethod
    def check_peak_windows(
        cls, windows: tuple[WindowModel, ...]
    ) -> tuple[WindowModel, ...]:
        """
        Refuse peak windows that wrap midnight, are empty or overlap.

        Parameters
        ----------
        windows : tuple[WindowModel, ...]
            The stored peak windows.

        Returns
        -------
        tuple[WindowModel, ...]
            The windows, unchanged.

        Raises
        ------
        ValueError
            When a window doesn't end after it starts, or two overlap.
        """
        ordered = sorted(windows, key=lambda window: window.start)
        if any(window.start >= window.end for window in ordered):
            raise ValueError('a peak window must end after it starts')
        if any(
            earlier.end > later.start
            for earlier, later in zip(ordered, ordered[1:])
        ):
            raise ValueError('peak windows must not overlap')
        return windows


def window_to_model(*, window: Window) -> WindowModel:
    """
    Convert a window to its stored form.

    Parameters
    ----------
    window : Window
        The window.

    Returns
    -------
    WindowModel
        The window with each end as ``HH:MM``.
    """
    return WindowModel(
        start=window.start.strftime(CLOCK_FORMAT),
        end=window.end.strftime(CLOCK_FORMAT),
    )


def window_from_model(*, model: WindowModel) -> Window:
    """
    Convert a stored window back.

    Parameters
    ----------
    model : WindowModel
        The stored window.

    Returns
    -------
    Window
        The window.
    """
    return Window(
        start=datetime.strptime(model.start, CLOCK_FORMAT).time(),
        end=datetime.strptime(model.end, CLOCK_FORMAT).time(),
    )


def settings_to_model(*, settings: Settings) -> SettingsModel:
    """
    Convert the settings to their stored form.

    Parameters
    ----------
    settings : Settings
        The settings.

    Returns
    -------
    SettingsModel
        The stored settings.
    """
    return SettingsModel(
        auto_rebuild=settings.auto_rebuild,
        peak_rate=settings.peak_rate,
        offpeak_rate=settings.offpeak_rate,
        peak_windows=tuple(
            window_to_model(window=window)
            for window in settings.peak_windows
        ),
        quiet_mode=settings.quiet_mode,
        my_hours=window_to_model(window=settings.my_hours),
        rotate=settings.rotate,
        larger_text=settings.larger_text,
        inverted_lists=settings.inverted_lists,
        alert_lookahead=settings.alert_lookahead,
        connection_map=settings.connection_map,
        skip_school_buses=settings.skip_school_buses,
        skip_school_on_route_pins=settings.skip_school_on_route_pins,
    )


def settings_from_model(*, model: SettingsModel) -> Settings:
    """
    Convert stored settings back.

    Parameters
    ----------
    model : SettingsModel
        The stored settings.

    Returns
    -------
    Settings
        The settings.
    """
    return Settings(
        auto_rebuild=model.auto_rebuild,
        peak_rate=model.peak_rate,
        offpeak_rate=model.offpeak_rate,
        peak_windows=tuple(
            window_from_model(model=window) for window in model.peak_windows
        ),
        quiet_mode=model.quiet_mode,
        my_hours=window_from_model(model=model.my_hours),
        rotate=model.rotate,
        larger_text=model.larger_text,
        inverted_lists=model.inverted_lists,
        alert_lookahead=model.alert_lookahead,
        connection_map=model.connection_map,
        skip_school_buses=model.skip_school_buses,
        skip_school_on_route_pins=model.skip_school_on_route_pins,
    )


def default_settings() -> dict[str, object]:
    """
    Give the default settings as stored.

    Returns
    -------
    dict[str, object]
        The default settings as JSON data.
    """
    return settings_to_model(settings=Settings()).model_dump(mode='json')
