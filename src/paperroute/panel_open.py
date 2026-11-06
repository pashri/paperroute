"""Opening the e-paper panel for the service."""

from collections.abc import Callable

from paperroute.adapters.epaper import Uc8179
from paperroute.adapters.panel_display import PanelDisplay
from paperroute.observers import PanelTimes
from paperroute.ports import PanelBus, PanelRefresh

__all__ = ['panel_for', 'panel_times_for']


def panel_for(
    *,
    enabled: bool,
    opener: Callable[[], PanelBus],
    report: Callable[[PanelRefresh], None],
) -> PanelDisplay | None:
    """
    Make the e-paper panel display, if asked for.

    The panel itself is opened at the first frame, and again at the
    next frame after a failed open.

    Parameters
    ----------
    enabled : bool
        Whether the panel was asked for.
    opener : Callable[[], PanelBus]
        Opens the panel's bus.
    report : Callable[[PanelRefresh], None]
        Receives each refresh the panel does, including a failed open.

    Returns
    -------
    PanelDisplay | None
        The panel display; None when not asked for.
    """
    if not enabled:
        return None
    return PanelDisplay(
        opener=lambda: Uc8179(bus=opener()),
        report=report,
    )


def panel_times_for(
    *, wanted: bool, panel: PanelDisplay | None
) -> PanelTimes | None:
    """
    Choose the panel times the device page starts with.

    Parameters
    ----------
    wanted : bool
        Whether the panel was asked for.
    panel : PanelDisplay | None
        The panel display, if asked for.

    Returns
    -------
    PanelTimes | None
        No times when the panel was not asked for, failed ones when it
        was asked for but there is no display, otherwise new ones.
    """
    if not wanted:
        return None
    return PanelTimes() if panel else PanelTimes(ok=False)
