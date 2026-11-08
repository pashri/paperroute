"""Tests for what the device keeps between restarts."""

from datetime import time

from paperroute.city import SYDNEY, TOWN_HALL
from paperroute_device.core.device_settings import DeviceSettings, Window
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
    Settings,
    default_saved,
    default_settings,
    seeded,
)


def test_board_settings_defaults() -> None:
    """The board settings start as today's defaults, with no peak hours."""
    settings = BoardSettings()
    assert settings.peak_rate is Rate.FAST
    assert settings.offpeak_rate is Rate.SLOW
    assert settings.peak_windows == ()
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


def test_default_saved_from_city() -> None:
    """A device with nothing saved shows the city's default pin."""
    saved = default_saved(city=SYDNEY)
    assert saved.unpinned == SYDNEY.default_pin
    assert saved.unpinned is not None
    assert saved.unpinned.origin == TOWN_HALL
    assert saved.unpinned.kind == 'train'
    assert saved.settings.board.peak_windows == (
        Window(start=time(6, 30), end=time(10)),
        Window(start=time(15), end=time(19)),
    )


def test_seeded_takes_city_settings() -> None:
    """A seeded pin starts with the city's default settings."""
    saved = seeded(pin=SYDNEY.default_pin, city=SYDNEY)
    assert saved.pins == (SYDNEY.default_pin,)
    assert saved.unpinned is None
    assert saved.settings == default_settings(city=SYDNEY)
