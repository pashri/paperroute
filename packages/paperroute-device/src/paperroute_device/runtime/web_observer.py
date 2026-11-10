"""The observers the web board reports to."""

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from paperroute_device.adapters.act_led import ActLed
from paperroute_device.adapters.systemd import SystemdNotifier
from paperroute_device.ports import DeviceObserver
from paperroute_device.runtime.timings import KitTimings, ObserverGroup

__all__ = ['web_observer']

ACT_LED: Final[Path] = Path('/sys/class/leds/ACT')


def web_observer(
    *,
    timings: KitTimings,
    extra: Sequence[DeviceObserver] = (),
    led_root: Path = ACT_LED,
) -> ObserverGroup:
    """
    Gather the web board's observers.

    Parameters
    ----------
    timings : KitTimings
        Keeps the latest timings for the status page.
    extra : Sequence[DeviceObserver]
        The app's own observers, told after the timings.
    led_root : Path
        The ACT LED's sysfs directory.

    Returns
    -------
    ObserverGroup
        The timings, the app's observers, the systemd notifier and the
        ACT LED, in that order.
    """
    return ObserverGroup(
        observers=[
            timings,
            *extra,
            SystemdNotifier.from_environment(),
            ActLed(root=led_root),
        ],
    )
