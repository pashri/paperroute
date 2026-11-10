"""The effects the kit's reducer asks for."""

from dataclasses import dataclass, field

from paperroute_device.core.device_model import (
    JoinRequest,
    PowerAction,
    ResetKind,
)
from paperroute_device.core.device_state import KitSaved


@dataclass(frozen=True)
class Render:
    """
    Draw a view.

    Attributes
    ----------
    view : object
        The view.
    full : bool
        Whether the panel should do a full refresh rather than a partial
        one.
    clear : bool
        Whether the panel should do a clear refresh: the whole screen in
        black and white, with no wipe.
    """

    view: object
    full: bool = False
    clear: bool = False


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


@dataclass(frozen=True)
class SaveState:
    """Write what the device keeps between restarts."""

    saved: KitSaved


@dataclass(frozen=True)
class ControlPower:
    """Shut the device down or restart it."""

    action: PowerAction


@dataclass(frozen=True)
class ProbeDevice:
    """Gather the device's facts in the background."""


@dataclass(frozen=True)
class ResetDevice:
    """Reset the settings or erase everything, in the background."""

    kind: ResetKind


@dataclass(frozen=True)
class StartWifiSetup:
    """Scan for networks, then start the setup hotspot, in the background."""


@dataclass(frozen=True)
class JoinWifi:
    """
    Leave the hotspot and join a network, in the background.

    Attributes
    ----------
    request : JoinRequest
        The network to join.
    hotspot : str
        The hotspot password, to start it again if the join fails. Kept
        out of the repr.
    """

    request: JoinRequest
    hotspot: str = field(repr=False)


@dataclass(frozen=True)
class StopWifiSetup:
    """Take the setup hotspot down, in the background."""


KitEffect = (
    Render
    | SaveState
    | Stop
    | ControlPower
    | ProbeDevice
    | ResetDevice
    | StartWifiSetup
    | JoinWifi
    | StopWifiSetup
)


def is_kit_effect(effect: object) -> bool:
    """
    Say whether an effect is one the kit performs itself.

    Parameters
    ----------
    effect : object
        An effect.

    Returns
    -------
    bool
        True for every `KitEffect`; False for the app's own.
    """
    return isinstance(effect, KitEffect)
