"""Write what the kit keeps, logging each kind of failure once."""

# An app package keeps a twin of this module until it switches over.
# pylint: disable=duplicate-code

import asyncio
import logging

from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.kit_effects import SaveState

logger = logging.getLogger(__name__)

type ErrorKind = tuple[type[OSError], int | None, str | None]


class StateSaver:
    """
    Write the state file, logging each kind of write error once.

    Errors of the same type, number and text are one kind, whatever file
    they name, since each save writes a new temporary file.

    Parameters
    ----------
    states : StateFile
        Where the state is kept.
    """

    def __init__(self, *, states: StateFile) -> None:
        self.states = states
        self.logged: set[ErrorKind] = set()

    async def save(self, *, effect: SaveState) -> None:
        """
        Write the state in a worker thread; a write error is never raised.

        Parameters
        ----------
        effect : SaveState
            What to write.
        """
        try:
            await asyncio.to_thread(self.states.save, saved=effect.saved)
        except OSError as error:
            kind = (type(error), error.errno, error.strerror)
            if kind not in self.logged:
                self.logged.add(kind)
                logger.exception('saving state failed')
