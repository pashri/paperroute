"""Settings as stored in the state file, and their defaults."""

import logging
from datetime import datetime
from typing import Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from paperroute.adapters.state_auto import CLOCK, CLOCK_FORMAT
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_hardware.ports import CLEAN_AFTER
from paperroute_transit.core import saved
from paperroute_transit.core.city import City
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
)

logger = logging.getLogger(__name__)

MAX_PEAK_WINDOWS: Final[int] = 2


class WindowModel(BaseModel):
    """A daily window as stored, each end as ``HH:MM``."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    start: str = Field(pattern=CLOCK)
    end: str = Field(pattern=CLOCK)


class DeviceSettingsModel(BaseModel):
    """
    The device's own Settings page choices as stored.

    The cleaning count and the knob swap have defaults, so files stored
    before them load.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    auto_rebuild: bool
    quiet_mode: QuietMode
    my_hours: WindowModel
    rotate: bool
    larger_text: bool
    inverted_lists: bool
    clean_after: int = CLEAN_AFTER
    swap_knobs: bool = False

    @field_validator('clean_after')
    @classmethod
    def check_clean_after(cls, count: int) -> int:
        """
        Refuse a cleaning count the Settings page never offers.

        Parameters
        ----------
        count : int
            The stored count.

        Returns
        -------
        int
            The count, unchanged.

        Raises
        ------
        ValueError
            When the count is not one of `CLEAN_CHOICES`.
        """
        if count not in CLEAN_CHOICES:
            raise ValueError(f'clean_after must be one of {CLEAN_CHOICES}')
        return count


class BoardSettingsModel(BaseModel):
    """
    The transit board's Settings page choices as stored.

    The school switches have defaults, so files stored before them load.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    peak_rate: Rate
    offpeak_rate: Rate
    peak_windows: tuple[WindowModel, ...] = Field(
        max_length=MAX_PEAK_WINDOWS,
    )
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


def device_settings_to_model(
    *, settings: DeviceSettings,
) -> DeviceSettingsModel:
    """
    Convert the device's settings to their stored form.

    Parameters
    ----------
    settings : DeviceSettings
        The device's settings.

    Returns
    -------
    DeviceSettingsModel
        The stored settings.
    """
    return DeviceSettingsModel(
        auto_rebuild=settings.auto_rebuild,
        quiet_mode=settings.quiet_mode,
        my_hours=window_to_model(window=settings.my_hours),
        rotate=settings.rotate,
        larger_text=settings.larger_text,
        inverted_lists=settings.inverted_lists,
        clean_after=settings.clean_after,
        swap_knobs=settings.swap_knobs,
    )


def device_settings_from_model(
    *, model: DeviceSettingsModel,
) -> DeviceSettings:
    """
    Convert the device's stored settings back.

    Parameters
    ----------
    model : DeviceSettingsModel
        The stored settings.

    Returns
    -------
    DeviceSettings
        The device's settings.
    """
    return DeviceSettings(
        auto_rebuild=model.auto_rebuild,
        quiet_mode=model.quiet_mode,
        my_hours=window_from_model(model=model.my_hours),
        rotate=model.rotate,
        larger_text=model.larger_text,
        inverted_lists=model.inverted_lists,
        clean_after=model.clean_after,
        swap_knobs=model.swap_knobs,
    )


def board_settings_to_model(*, settings: BoardSettings) -> BoardSettingsModel:
    """
    Convert the board's settings to their stored form.

    Parameters
    ----------
    settings : BoardSettings
        The board's settings.

    Returns
    -------
    BoardSettingsModel
        The stored settings.
    """
    return BoardSettingsModel(
        peak_rate=settings.peak_rate,
        offpeak_rate=settings.offpeak_rate,
        peak_windows=tuple(
            window_to_model(window=window)
            for window in settings.peak_windows
        ),
        alert_lookahead=settings.alert_lookahead,
        connection_map=settings.connection_map,
        skip_school_buses=settings.skip_school_buses,
        skip_school_on_route_pins=settings.skip_school_on_route_pins,
    )


def board_settings_from_model(*, model: BoardSettingsModel) -> BoardSettings:
    """
    Convert the board's stored settings back.

    Parameters
    ----------
    model : BoardSettingsModel
        The stored settings.

    Returns
    -------
    BoardSettings
        The board's settings.
    """
    return BoardSettings(
        peak_rate=model.peak_rate,
        offpeak_rate=model.offpeak_rate,
        peak_windows=tuple(
            window_from_model(model=window) for window in model.peak_windows
        ),
        alert_lookahead=model.alert_lookahead,
        connection_map=model.connection_map,
        skip_school_buses=model.skip_school_buses,
        skip_school_on_route_pins=model.skip_school_on_route_pins,
    )


def default_device_settings(*, city: City) -> dict[str, object]:
    """
    Give a city's default device settings as stored.

    Parameters
    ----------
    city : City
        The city.

    Returns
    -------
    dict[str, object]
        The default device settings as JSON data.
    """
    return device_settings_to_model(
        settings=saved.default_settings(city=city).device,
    ).model_dump(mode='json')


def default_board_settings(*, city: City) -> dict[str, object]:
    """
    Give a city's default board settings as stored.

    Parameters
    ----------
    city : City
        The city.

    Returns
    -------
    dict[str, object]
        The default board settings as JSON data.
    """
    return board_settings_to_model(
        settings=saved.default_settings(city=city).board,
    ).model_dump(mode='json')


def default_settings(*, city: City) -> dict[str, object]:
    """
    Give a city's default settings in the one object older files kept.

    Parameters
    ----------
    city : City
        The city.

    Returns
    -------
    dict[str, object]
        The device and board defaults together, as JSON data; schemas
        before 12 stored both in one ``settings`` object.
    """
    return {
        **default_device_settings(city=city),
        **default_board_settings(city=city),
    }


def repaired_settings(
    *,
    data: dict[str, object],
    model: type[BaseModel],
    defaults: dict[str, object],
) -> dict[str, object]:
    """
    Reset each setting that cannot be read, so the rest are kept.

    Parameters
    ----------
    data : dict[str, object]
        A migrated section.
    model : type[BaseModel]
        The model its settings must match.
    defaults : dict[str, object]
        Its default settings as stored.

    Returns
    -------
    dict[str, object]
        The section with each bad setting at its default and unknown ones
        dropped; all defaults when the settings are not an object. The
        peak windows are reset as a whole when any of them is bad.
    """
    settings = data.get('settings')
    if not isinstance(settings, dict):
        logger.warning('state.json settings unusable, resetting them')
        return {**data, 'settings': defaults}
    try:
        model.model_validate(settings)
    except ValidationError as error:
        bad = {item['loc'][0] for item in error.errors() if item['loc']}
        logger.warning('state.json settings %s unusable, resetting', bad)
        kept = {
            name: value
            for name, value in settings.items()
            if name in defaults and name not in bad
        }
        return {**data, 'settings': {**defaults, **kept}}
    return data
