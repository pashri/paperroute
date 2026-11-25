"""Tests for storing the Settings page's choices."""

import pytest
from pydantic import ValidationError

from paperroute_device.adapters.state_file import (
    DeviceSettingsModel,
    device_settings_from_model,
    device_settings_to_model,
)
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
)
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.state_migrations import V11_DEVICE_FIELDS
from paperroute_transit.adapters.state_settings import (
    BoardSettingsModel,
    board_settings_from_model,
    board_settings_to_model,
    default_board_settings,
    default_device_settings,
    default_settings,
)
from paperroute_transit.core.positions import ParkedAge
from paperroute_transit.core.saved import (
    DEVICE_FIELDS,
    BoardSettings,
    ConnectionMap,
    MapFraming,
    Settings,
    StopSides,
)


def test_settings_model_keys_unchanged() -> None:
    """The settings older files kept stay today's nineteen flat keys."""
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
        'parked_age',
        'map_framing',
        'stop_sides',
        'street_group',
        'connection_map',
        'skip_school_buses',
        'skip_school_on_route_pins',
        'clean_after',
        'swap_knobs',
    }


def test_device_and_board_settings_split_by_field() -> None:
    """The device keeps its own fields; the board keeps the rest."""
    device = default_device_settings(city=SYDNEY)
    board = default_board_settings(city=SYDNEY)
    assert set(device) == DEVICE_FIELDS
    assert not set(board) & DEVICE_FIELDS
    assert set(device) | set(board) == set(default_settings(city=SYDNEY))


def test_settings_round_trip_connection_map() -> None:
    """The connection map choice survives storing and reading back."""
    settings = BoardSettings(connection_map=ConnectionMap.WHOLE)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings


def test_settings_without_school_switch_skip_school() -> None:
    """Settings stored before the school switch leave school buses out."""
    stored = default_board_settings(city=SYDNEY)
    del stored['skip_school_buses']
    model = BoardSettingsModel.model_validate(stored)
    assert board_settings_from_model(model=model).skip_school_buses


def test_settings_without_route_pin_switch_keep_school() -> None:
    """Settings stored before the route-pin switch leave it off."""
    stored = default_board_settings(city=SYDNEY)
    del stored['skip_school_on_route_pins']
    model = BoardSettingsModel.model_validate(stored)
    assert not board_settings_from_model(model=model).skip_school_on_route_pins


def test_settings_round_trip_route_pin_switch() -> None:
    """The route-pin school switch survives storing and reading back."""
    settings = BoardSettings(skip_school_on_route_pins=True)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings


def test_settings_round_trip_school_switch() -> None:
    """The school switch survives storing and reading back."""
    settings = BoardSettings(skip_school_buses=False)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings


def test_settings_clean_after_defaults_to_thirty() -> None:
    """The screen is cleaned after 30 changes unless told otherwise."""
    assert Settings().device.clean_after == 30


def test_settings_without_clean_after_clean_after_thirty() -> None:
    """Settings stored before the cleaning choice clean after 30."""
    stored = default_device_settings(city=SYDNEY)
    del stored['clean_after']
    model = DeviceSettingsModel.model_validate(stored)
    assert device_settings_from_model(model=model).clean_after == 30


@pytest.mark.parametrize('count', CLEAN_CHOICES)
def test_settings_round_trip_clean_after(count: int) -> None:
    """Each cleaning choice survives storing and reading back."""
    settings = DeviceSettings(clean_after=count)
    model = device_settings_to_model(settings=settings)
    assert device_settings_from_model(model=model) == settings


@pytest.mark.parametrize('count', [0, 29, 1000])
def test_settings_refuse_unknown_clean_after(count: int) -> None:
    """A stored count the page never offers is refused."""
    stored = default_device_settings(city=SYDNEY) | {'clean_after': count}
    with pytest.raises(ValidationError):
        DeviceSettingsModel.model_validate(stored)


def test_settings_swap_knobs_defaults_off() -> None:
    """Knob A is the top knob unless told otherwise."""
    assert Settings().device.swap_knobs is False


def test_settings_without_swap_knobs_leave_the_knobs() -> None:
    """Settings stored before the swap switch load with it off."""
    stored = default_device_settings(city=SYDNEY)
    del stored['swap_knobs']
    model = DeviceSettingsModel.model_validate(stored)
    assert device_settings_from_model(model=model).swap_knobs is False


def test_settings_round_trip_swap_knobs() -> None:
    """The swap switch survives storing and reading back."""
    settings = DeviceSettings(swap_knobs=True)
    model = device_settings_to_model(settings=settings)
    assert device_settings_from_model(model=model) == settings


def test_v11_device_fields_snapshot() -> None:
    """The frozen v11 field names are today's device fields and the switch."""
    assert V11_DEVICE_FIELDS == DEVICE_FIELDS | {'auto_rebuild'}


def test_settings_without_parked_age_load_as_off() -> None:
    """Settings without parked age load as off."""
    stored = board_settings_to_model(settings=BoardSettings()).model_dump()
    del stored['parked_age']
    model = BoardSettingsModel.model_validate(stored)
    assert board_settings_from_model(model=model).parked_age is ParkedAge.OFF


def test_settings_round_trip_parked_age() -> None:
    """Settings round trip parked age."""
    settings = BoardSettings(parked_age=ParkedAge.THIRTY)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings


def test_settings_without_map_framing_load_as_approach() -> None:
    """Settings stored before the map framing load as Approach."""
    centred = BoardSettings(map_framing=MapFraming.CENTRED)
    stored = board_settings_to_model(settings=centred).model_dump()
    del stored['map_framing']
    model = BoardSettingsModel.model_validate(stored)
    found = board_settings_from_model(model=model)
    assert found.map_framing is MapFraming.APPROACH


def test_settings_round_trip_map_framing() -> None:
    """Settings round trip map framing."""
    settings = BoardSettings(map_framing=MapFraming.CENTRED)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings


def test_settings_without_stop_sides_load_as_time_order() -> None:
    """Settings stored before the both-sides view load as time order."""
    each_way = BoardSettings(stop_sides=StopSides.EACH_WAY)
    stored = board_settings_to_model(settings=each_way).model_dump()
    del stored['stop_sides']
    model = BoardSettingsModel.model_validate(stored)
    found = board_settings_from_model(model=model)
    assert found.stop_sides is StopSides.TIME_ORDER


def test_settings_round_trip_stop_sides() -> None:
    """Settings round trip the both-sides view."""
    settings = BoardSettings(stop_sides=StopSides.EACH_WAY)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings


def test_settings_without_street_group_load_as_three() -> None:
    """Settings stored before street headings group from three stops."""
    stored = board_settings_to_model(
        settings=BoardSettings(street_group=5),
    ).model_dump()
    del stored['street_group']
    model = BoardSettingsModel.model_validate(stored)
    assert board_settings_from_model(model=model).street_group == 3


def test_settings_round_trip_street_group() -> None:
    """Settings round trip the smallest street group."""
    settings = BoardSettings(street_group=5)
    model = board_settings_to_model(settings=settings)
    assert board_settings_from_model(model=model) == settings
