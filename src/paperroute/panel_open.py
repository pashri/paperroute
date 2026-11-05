"""Opening the e-paper panel for the service."""

import logging
from collections.abc import Callable

from paperroute.adapters.epaper import Uc8179
from paperroute.adapters.panel_display import PanelDisplay
from paperroute.ports import PanelBus, PanelRefresh

__all__ = ['panel_for']

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
