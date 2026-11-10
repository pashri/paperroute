"""Sleeping the screen: the state it leaves."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.device_state import DeviceState
from paperroute_device.core.text import DOT

SLEEP_BAND: Final[str] = f'Asleep{DOT}turn or press a knob to wake'
WAKE_FOR: Final[timedelta] = timedelta(minutes=5)


def slept(*, device: DeviceState, by_quiet: bool = False) -> DeviceState:
    """
    Put the screen to sleep.

    Parameters
    ----------
    device : DeviceState
        Device state.
    by_quiet : bool
        Whether quiet hours sleep it, so a tick can end the sleep.

    Returns
    -------
    DeviceState
        Device state with the screen asleep, the menu closed and no wake
        running.
    """
    panel = replace(
        device.panel,
        asleep=True,
        menu=None,
        by_quiet=by_quiet,
        awake_until=None,
    )
    return replace(device, panel=panel)


def held_awake(*, device: DeviceState) -> DeviceState:
    """
    Keep the screen awake for `WAKE_FOR` from now.

    Parameters
    ----------
    device : DeviceState
        Device state, awake.

    Returns
    -------
    DeviceState
        Device state whose wake ends `WAKE_FOR` after now.
    """
    panel = replace(device.panel, awake_until=device.now + WAKE_FOR)
    return replace(device, panel=panel)


def awake(*, device: DeviceState) -> DeviceState:
    """
    End sleep.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceState
        Device state awake, not slept by quiet hours. The app sees the
        change through `App.on_device`.
    """
    panel = replace(device.panel, asleep=False, by_quiet=False)
    return replace(device, panel=panel)
