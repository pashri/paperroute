"""The adapters the kit's loop runs on."""

from dataclasses import dataclass, field
from typing import Protocol

from paperroute_device.adapters.power import NullPower
from paperroute_device.adapters.state_file import Loaded
from paperroute_device.core.device_state import KitSaved
from paperroute_device.ports import Clock, DeviceObserver, PowerControl
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.timings import NullObserver
from paperroute_hardware.ports import Display


class StateStore(Protocol):
    """Keeps the device settings and the app's section between runs."""

    def load(self) -> Loaded:
        """
        Read what was kept.

        Returns
        -------
        Loaded
            What was kept, or the defaults.
        """

    def save(self, *, saved: KitSaved) -> None:
        """
        Keep the device settings and the app's part.

        Parameters
        ----------
        saved : KitSaved
            What the kit and the app keep.

        Raises
        ------
        OSError
            When it can't be kept.
        """


@dataclass(frozen=True, kw_only=True)
class KitPorts:
    """
    The adapters the kit's loop runs on.

    Attributes
    ----------
    display : Display
        Shows the frames.
    clock : Clock
        The current time.
    states : StateStore
        Where the device settings and the app's section are kept.
    power : PowerControl
        Shuts the device down or restarts it.
    device : DevicePorts
        What the device's own effects run on; each set has its own radio
        lock.
    observer : DeviceObserver
        Receives the timings and statuses.
    """

    display: Display
    clock: Clock
    states: StateStore
    power: PowerControl = field(default_factory=NullPower)
    device: DevicePorts = field(default_factory=DevicePorts)
    observer: DeviceObserver = field(default_factory=NullObserver)
