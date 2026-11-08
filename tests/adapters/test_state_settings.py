"""Tests for storing the Settings page's choices."""

import pytest
from pydantic import ValidationError

from paperroute.adapters.state_settings import (
    SettingsModel,
    default_settings,
    settings_from_model,
    settings_to_model,
)
from paperroute.city import SYDNEY
from paperroute.core.saved import BoardSettings, ConnectionMap, Settings
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
)


def test_settings_model_keys_unchanged() -> None:
    """The stored settings keep today's fifteen flat keys."""
    assert set(default_settings(city=SYDNEY)) == {
        'auto_rebuild',
        'peak_rate',
        'offpeak_rate',
        'peak_windows',
        'quiet_mode',
        'my_hours',
        'rotate',
        'larger_text',
        'inverted_lists',
        'alert_lookahead',
        'connection_map',
        'skip_school_buses',
        'skip_school_on_route_pins',
        'clean_after',
        'swap_knobs',
    }


def test_settings_round_trip_connection_map() -> None:
    """The connection map choice survives storing and reading back."""
    settings = Settings(
        board=BoardSettings(connection_map=ConnectionMap.WHOLE),
    )
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings


def test_settings_without_school_switch_skip_school() -> None:
    """Settings stored before the school switch leave school buses out."""
    stored = default_settings(city=SYDNEY)
    del stored['skip_school_buses']
    model = SettingsModel.model_validate(stored)
    assert settings_from_model(model=model).board.skip_school_buses


def test_settings_without_route_pin_switch_keep_school() -> None:
    """Settings stored before the route-pin switch leave it off."""
    stored = default_settings(city=SYDNEY)
    del stored['skip_school_on_route_pins']
    model = SettingsModel.model_validate(stored)
    assert not settings_from_model(model=model).board.skip_school_on_route_pins


def test_settings_round_trip_route_pin_switch() -> None:
    """The route-pin school switch survives storing and reading back."""
    settings = Settings(
        board=BoardSettings(skip_school_on_route_pins=True),
    )
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings


def test_settings_round_trip_school_switch() -> None:
    """The school switch survives storing and reading back."""
    settings = Settings(board=BoardSettings(skip_school_buses=False))
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings


def test_settings_clean_after_defaults_to_thirty() -> None:
    """The screen is cleaned after 30 changes unless told otherwise."""
    assert Settings().device.clean_after == 30


def test_settings_without_clean_after_clean_after_thirty() -> None:
    """Settings stored before the cleaning choice clean after 30."""
    stored = default_settings(city=SYDNEY)
    del stored['clean_after']
    model = SettingsModel.model_validate(stored)
    assert settings_from_model(model=model).device.clean_after == 30


@pytest.mark.parametrize('count', CLEAN_CHOICES)
def test_settings_round_trip_clean_after(count: int) -> None:
    """Each cleaning choice survives storing and reading back."""
    settings = Settings(device=DeviceSettings(clean_after=count))
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings


@pytest.mark.parametrize('count', [0, 29, 1000])
def test_settings_refuse_unknown_clean_after(count: int) -> None:
    """A stored count the page never offers is refused."""
    stored = default_settings(city=SYDNEY) | {'clean_after': count}
    with pytest.raises(ValidationError):
        SettingsModel.model_validate(stored)


def test_settings_swap_knobs_defaults_off() -> None:
    """Knob A is the top knob unless told otherwise."""
    assert Settings().device.swap_knobs is False


def test_settings_without_swap_knobs_leave_the_knobs() -> None:
    """Settings stored before the swap switch load with it off."""
    stored = default_settings(city=SYDNEY)
    del stored['swap_knobs']
    model = SettingsModel.model_validate(stored)
    assert settings_from_model(model=model).device.swap_knobs is False


def test_settings_round_trip_swap_knobs() -> None:
    """The swap switch survives storing and reading back."""
    settings = Settings(device=DeviceSettings(swap_knobs=True))
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings
