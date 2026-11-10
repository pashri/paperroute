"""Settings as stored in the state file, and their defaults."""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from paperroute_device.adapters.state_file import (
    WindowModel,
    device_settings_to_model,
    window_from_model,
    window_to_model,
)
from paperroute_transit.core import saved
from paperroute_transit.core.city import City
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
)

MAX_PEAK_WINDOWS: Final[int] = 2


class BoardSettingsModel(BaseModel):
    """
    The transit board's Settings page choices as stored.

    The school switches have defaults, so files stored before them load.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    auto_rebuild: bool
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
        auto_rebuild=settings.auto_rebuild,
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
        auto_rebuild=model.auto_rebuild,
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
