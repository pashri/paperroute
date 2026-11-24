"""Shared helpers for the state file tests."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from paperroute_device.core.device_settings import DeviceSettings
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY, SydneyMode
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.saved import (
    BoardSettings,
    LiveView,
    Saved,
    Settings,
    default_settings,
)
from tests.adapters.board_states import BoardStates, schema_of

SCHEMA: Final[int] = schema_of(city=SYDNEY)


ROTATED: Final[Settings] = Settings(
    board=BoardSettings(peak_windows=PEAK_WINDOWS),
    device=DeviceSettings(rotate=True),
)


def sample() -> Saved:
    """
    Build a small saved state.

    Returns
    -------
    Saved
        One pin with a destination, twice.
    """
    pin = Pin(
        origin=StopPlace(stop_id='a', name='A', point=(1.5, -2.5)),
        destination=StopPlace(stop_id='b', name='B', point=None),
        kind=SydneyMode.BUS,
    )
    return Saved(
        pins=(pin, pin),
        current=1,
        unpinned=None,
        view=LiveView.STRIP,
        settings=default_settings(city=SYDNEY),
    )


def stored_with(
    *,
    path: Path,
    pin_fields: dict[str, object],
) -> dict[str, Any]:
    """
    Save the sample, then overwrite fields on its second pin.

    Parameters
    ----------
    path : Path
        The state file.
    pin_fields : dict[str, object]
        Fields to set on the second stored pin.

    Returns
    -------
    dict[str, Any]
        The rewritten file's data.
    """
    BoardStates(path=path, city=SYDNEY).save(saved=sample())
    data: dict[str, Any] = json.loads(path.read_text())
    data['board']['pins'][1].update(pin_fields)
    path.write_text(json.dumps(data))
    return data


def settings_without(*, path: Path, setting: str) -> Settings:
    """
    Load the settings of a stored file that lacks one device setting.

    Parameters
    ----------
    path : Path
        Where the file is written.
    setting : str
        The device setting to delete from the stored file.

    Returns
    -------
    Settings
        The settings the stored file loads as.
    """
    repository = BoardStates(path=path, city=SYDNEY)
    repository.save(saved=replace(sample(), settings=ROTATED))
    data = json.loads(path.read_text())
    del data['device']['settings'][setting]
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.stored
    return loaded.saved.settings
