"""Opening the e-paper panel for the service."""

import logging
from collections.abc import Callable

from paperroute.adapters.epaper import Uc8179
from paperroute.adapters.panel_display import PanelDisplay
from paperroute.observers import PanelTimes
from paperroute.ports import PanelBus, PanelRefresh

__all__ = ['panel_for', 'panel_times_for']

logger = logging.getLogger(__name__)


def panel_for(
    *,
    enabled: bool,
    opener: Callable[[], PanelBus],
    report: Callable[[PanelRefresh], None],
) -> PanelDisplay | None:
    """
    Open the e-paper panel, if asked for and available.

    Parameters
    ----------
    enabled : bool
        Whether the panel was asked for.
    opener : Callable[[], PanelBus]
        Opens the panel's bus.
    report : Callable[[PanelRefresh], None]
        Receives each refresh the panel does.

    Returns
    -------
    PanelDisplay | None
        The panel display; None when not asked for or not available.
    """
    if not enabled:
        return None
    try:
        controller = Uc8179(bus=opener())
    except (OSError, ImportError):
        logger.error('panel not available', exc_info=True)
        return None
    return PanelDisplay(controller=controller, report=report)


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
        The panel that opened, if any.

    Returns
    -------
    PanelTimes | None
        No times when the panel was not asked for, failed ones when it
        was asked for but did not open, otherwise new ones.
    """
    if not wanted:
        return None
    return PanelTimes() if panel else PanelTimes(ok=False)
