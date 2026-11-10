"""Tests for the settings the device kit owns."""

from datetime import time

from paperroute_device.core.device_settings import (
    MY_HOURS,
    DeviceSettings,
    QuietMode,
    Window,
)


def test_device_settings_defaults() -> None:
    """The device settings start as today's defaults."""
    settings = DeviceSettings()
    assert settings.quiet_mode is QuietMode.MY_HOURS
    assert settings.my_hours == MY_HOURS
    assert MY_HOURS == Window(start=time(23), end=time(5, 30))
    assert settings.rotate is False
    assert settings.larger_text is False
    assert settings.inverted_lists is False
    assert settings.clean_after == 30
    assert settings.swap_knobs is False
