"""Tests that the app's own test run builds the test kit for Sydney."""

from datetime import datetime
from pathlib import Path

from paperroute_device.assets import assets
from paperroute_device.core.text import DOT
from paperroute_sydney.__main__ import SYDNEY_PARTS
from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.city_setting import (
    city_under_test,
    line_mode,
    street_mode,
)
from paperroute_testkit.core.factories import START, TRAIN
from paperroute_transit.transit_app import TransitApp


def test_city_under_test_is_sydney() -> None:
    """The app's conftest sets Sydney and its web files."""
    assert city_under_test() is SYDNEY
    assert assets() == APP_ASSETS


def test_testkit_values_are_sydney_ones() -> None:
    """The test kit's times and modes come from Sydney."""
    assert START.tzinfo is SYDNEY.zone
    assert TRAIN.kind == line_mode() == SydneyMode.METRO
    assert street_mode() == SydneyMode.BUS


def test_sydney_card_names_the_city(tmp_path: Path) -> None:
    """Sydney's card names the city and says it has no timetable yet."""
    app = TransitApp(city=SYDNEY, parts=SYDNEY_PARTS)
    now = datetime(2026, 11, 16, 9, 0, tzinfo=SYDNEY.zone)
    card = app.card(data_dir=tmp_path, now=now)
    assert card.name == 'Sydney'
    assert card.status == f'Buses, trains and ferries{DOT}no timetable yet'
