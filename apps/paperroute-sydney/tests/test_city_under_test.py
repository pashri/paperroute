"""Tests that the app's own test run builds the test kit for Sydney."""

from paperroute_device.assets import assets
from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.city_setting import (
    city_under_test,
    line_mode,
    street_mode,
)
from paperroute_testkit.core.factories import START, TRAIN


def test_city_under_test_is_sydney() -> None:
    """The app's conftest sets Sydney and its web files."""
    assert city_under_test() is SYDNEY
    assert assets() == APP_ASSETS


def test_testkit_values_are_sydney_ones() -> None:
    """The test kit's times and modes come from Sydney."""
    assert START.tzinfo is SYDNEY.zone
    assert TRAIN.kind == line_mode() == SydneyMode.TRAIN
    assert street_mode() == SydneyMode.BUS
