"""Opening the knobs for the service."""

import logging
from collections.abc import Callable

from paperroute.adapters.knob_reader import KnobReader
from paperroute.core.knob_decoder import KnobDecoder
from paperroute.ports import KnobLines

__all__ = ['knobs_for', 'knobs_state_for']

logger = logging.getLogger(__name__)


def knobs_for(
    *,
    enabled: bool,
    opener: Callable[[], KnobLines],
    swapped: Callable[[], bool],
) -> KnobReader | None:
    """
    Open the knobs' lines, if asked for and available.

    Parameters
    ----------
    enabled : bool
        Whether the knobs were asked for.
    opener : Callable[[], KnobLines]
        Opens the knobs' lines.
    swapped : Callable[[], bool]
        Whether the knobs are swapped; asked from the reader's thread
        at every event.

    Returns
    -------
    KnobReader | None
        The reader, not yet started; None when not asked for or not
        available.
    """
    if not enabled:
        return None
    try:
        lines = opener()
    except (OSError, ImportError):
        logger.error('knobs not available', exc_info=True)
        return None
    return KnobReader(lines=lines, decoder=KnobDecoder(swapped=swapped))


def knobs_state_for(
    *, wanted: bool, knobs: KnobReader | None
) -> bool | None:
    """
    Choose what the device page says about the knobs.

    Parameters
    ----------
    wanted : bool
        Whether the knobs were asked for.
    knobs : KnobReader | None
        The reader, if the lines opened.

    Returns
    -------
    bool | None
        None when the knobs were not asked for, else whether they
        opened.
    """
    if not wanted:
        return None
    return knobs is not None
