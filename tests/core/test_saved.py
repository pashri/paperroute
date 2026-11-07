"""Tests for what the device keeps between restarts."""

from datetime import time

from paperroute.core.saved import (
    PEAK_WINDOWS,
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
    Settings,
)
from paperroute_device.core.device_settings import DeviceSettings, Window


def test_board_settings_defaults() -> None:
    """The board settings start as today's defaults."""
    settings = BoardSettings()
    assert settings.peak_rate is Rate.FAST
    assert settings.offpeak_rate is Rate.SLOW
    assert settings.peak_windows == PEAK_WINDOWS
    assert PEAK_WINDOWS == (
        Window(start=time(6, 30), end=time(10)),
        Window(start=time(15), end=time(19)),
    )
    assert settings.alert_lookahead is Lookahead.WEEK
    assert settings.connection_map is ConnectionMap.CHANGES
    assert settings.skip_school_buses is True
    assert settings.skip_school_on_route_pins is False
    assert settings.school_off_route_pins is False


def test_settings_defaults_to_both_parts() -> None:
    """The settings hold default device and board parts."""
    assert Settings() == Settings(
        device=DeviceSettings(), board=BoardSettings(),
    )
