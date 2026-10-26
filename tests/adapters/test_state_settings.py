"""Tests for storing the Settings page's choices."""

from paperroute.adapters.state_settings import (
    settings_from_model,
    settings_to_model,
)
from paperroute.core.saved import ConnectionMap, Settings


def test_settings_round_trip_connection_map() -> None:
    """The connection map choice survives storing and reading back."""
    settings = Settings(connection_map=ConnectionMap.WHOLE)
    model = settings_to_model(settings=settings)
    assert settings_from_model(model=model) == settings
