"""Tests for storing the Settings page's choices."""

from paperroute.adapters.state_settings import (
    SettingsModel,
    default_settings,
    settings_from_model,
    settings_to_model,
)
from paperroute.core.saved import ConnectionMap, Settings


def test_settings_round_trip_connection_map() -> None:
    """The connection map choice survives storing and reading back."""
    settings = Settings(connection_map=ConnectionMap.WHOLE)
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings


def test_settings_without_school_switch_skip_school() -> None:
    """Settings stored before the school switch leave school buses out."""
    stored = default_settings()
    del stored['skip_school_buses']
    model = SettingsModel.model_validate(stored)
    assert settings_from_model(model=model).skip_school_buses


def test_settings_without_route_pin_switch_keep_school() -> None:
    """Settings stored before the route-pin switch leave it off."""
    stored = default_settings()
    del stored['skip_school_on_route_pins']
    model = SettingsModel.model_validate(stored)
    assert not settings_from_model(model=model).skip_school_on_route_pins


def test_settings_round_trip_route_pin_switch() -> None:
    """The route-pin school switch survives storing and reading back."""
    settings = Settings(skip_school_on_route_pins=True)
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings


def test_settings_round_trip_school_switch() -> None:
    """The school switch survives storing and reading back."""
    settings = Settings(skip_school_buses=False)
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings
