"""The observers the web app reports to."""

from pathlib import Path

from paperroute_device.adapters.act_led import ActLed
from paperroute_device.adapters.systemd import SystemdNotifier
from paperroute_transit.observers import ObserverGroup, Timings
from paperroute_transit.summary import Summary

__all__ = ['web_observer']


def web_observer(
    *,
    timings: Timings,
    summary: Summary,
    led_root: Path = Path('/sys/class/leds/ACT'),
) -> ObserverGroup:
    """
    Gather the web app's observers.

    Parameters
    ----------
    timings : Timings
        Keeps the latest timings for the status page.
    summary : Summary
        Logs the periodic summary.
    led_root : Path
        The ACT LED's sysfs directory.

    Returns
    -------
    ObserverGroup
        The timings, the summary, the systemd notifier and the ACT LED.
    """
    return ObserverGroup(
        observers=[timings, summary],
        devices=(
            SystemdNotifier.from_environment(),
            ActLed(root=led_root),
        ),
    )
