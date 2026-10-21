"""Shut the device down or restart it through systemctl."""

import asyncio
import logging

from paperroute.core.messages import PowerAction

logger = logging.getLogger(__name__)


class NullPower:
    """A power control that never acts, for development machines."""

    def __init__(self) -> None:
        self.logged = False

    async def control(self, *, action: PowerAction) -> bool:
        """
        Refuse at once, saying so the first time.

        Parameters
        ----------
        action : PowerAction
            The action asked for.

        Returns
        -------
        bool
            Always False.
        """
        if not self.logged:
            logger.warning(
                'power control not enabled', extra={'action': action.value},
            )
            self.logged = True
        return False


class SystemctlPower:
    """
    Run ``systemctl poweroff`` or ``systemctl reboot``.

    The app's user is allowed these by a polkit rule; with no rule,
    systemctl fails and the action is refused.

    Parameters
    ----------
    systemctl : str
        The systemctl program.
    """

    def __init__(self, *, systemctl: str = '/usr/bin/systemctl') -> None:
        self.systemctl = systemctl

    async def control(self, *, action: PowerAction) -> bool:
        """
        Run systemctl with the action's verb and wait for it.

        Parameters
        ----------
        action : PowerAction
            Shut down or restart.

        Returns
        -------
        bool
            True when systemctl exited 0.
        """
        logger.info('power action', extra={'action': action.value})
        try:
            process = await asyncio.create_subprocess_exec(
                self.systemctl,
                action.value,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError:
            logger.exception('systemctl could not start')
            return False
        output, _ = await process.communicate()
        if process.returncode != 0:
            logger.error(
                'power action failed: %s',
                output.decode(errors='replace').strip(),
                extra={'code': process.returncode},
            )
        return process.returncode == 0
