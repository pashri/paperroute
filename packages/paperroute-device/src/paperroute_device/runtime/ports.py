"""The adapters the kit's loop runs on."""

from dataclasses import dataclass, field

from paperroute_device.adapters.power import NullPower
from paperroute_device.adapters.state_file import StateFile
from paperroute_device.ports import Clock, DeviceObserver, PowerControl
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.timings import NullObserver
from paperroute_hardware.ports import Display


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
    states : StateFile
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
    states: StateFile
    power: PowerControl = field(default_factory=NullPower)
    device: DevicePorts = field(default_factory=DevicePorts)
    observer: DeviceObserver = field(default_factory=NullObserver)
