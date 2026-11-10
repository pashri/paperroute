"""The requests the device's own pages send to the app."""

from dataclasses import dataclass

from paperroute_device.core.device_model import (
    ConfirmAction,
    JoinRequest,
    PowerAction,
)


@dataclass(frozen=True)
class ConfirmAsked:
    """A phone asked for something the panel must confirm."""

    action: ConfirmAction


@dataclass(frozen=True)
class ApiKeyChanged:
    """
    Which API keys are set was found out.

    Attributes
    ----------
    missing : tuple[str, ...]
        The display names of the keys not set, in declaration order.
    """

    missing: tuple[str, ...]


@dataclass(frozen=True)
class PowerRequested:
    """Shutting down or restarting was asked for."""

    action: PowerAction


@dataclass(frozen=True)
class JoinAsked:
    """A phone on the setup hotspot asked to join a network."""

    request: JoinRequest


DeviceRequest = ApiKeyChanged | ConfirmAsked | JoinAsked | PowerRequested
