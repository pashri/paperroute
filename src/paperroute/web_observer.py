"""The observers the web app reports to."""

from paperroute.adapters.act_led import ActLed
from paperroute.adapters.systemd import SystemdNotifier
from paperroute.observers import ObserverGroup, Timings
from paperroute.summary import Summary

__all__ = ['web_observer']


def web_observer(*, timings: Timings, summary: Summary) -> ObserverGroup:
    """
    Gather the web app's observers.

    Parameters
    ----------
    timings : Timings
        Keeps the latest timings for the status page.
    summary : Summary
        Logs the periodic summary.

    Returns
    -------
    ObserverGroup
        The timings, the summary, the systemd notifier and the ACT LED.
    """
    return ObserverGroup(
        observers=[
            timings,
            summary,
            SystemdNotifier.from_environment(),
            ActLed(),
        ]
    )
