"""List the installed apps and ask the system to switch between them."""

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import re
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from paperroute_device.adapters.systemd import run_systemctl
from paperroute_device.core.device_model import AppCard, InstalledApp

logger = logging.getLogger(__name__)

CARD_TIMEOUT: Final[float] = 30.0  # seconds
SWITCH_TIMEOUT: Final[float] = 90.0  # seconds
CARDS_FRESH: Final[float] = 600.0  # seconds
NAME_MAX: Final[int] = 40
LINE_MAX: Final[int] = 300
APP_KEY: Final[re.Pattern[str]] = re.compile(r'[a-z][a-z0-9]*')
BOARD: Final[Path] = Path('current/.venv/bin/paperroute-board')


def active_key(*, record: Path) -> str | None:
    """
    Read the active app from its record.

    Parameters
    ----------
    record : Path
        The one-line record.

    Returns
    -------
    str | None
        The app, or None when the record is missing or names no app.
    """
    try:
        key = record.read_text(encoding='utf-8').strip()
    except (OSError, ValueError):
        return None
    return key if APP_KEY.fullmatch(key) else None


def release_keys(*, apps: Path) -> tuple[str, ...]:
    """
    List the apps with a runnable release.

    Parameters
    ----------
    apps : Path
        The folder holding one folder per app.

    Returns
    -------
    tuple[str, ...]
        The apps whose current release has an executable board, sorted;
        empty when the folder can't be read.
    """
    try:
        folders = sorted(apps.iterdir())
    except OSError:
        return ()
    return tuple(
        folder.name
        for folder in folders
        if APP_KEY.fullmatch(folder.name)
        and (folder / BOARD).is_file()
        and os.access(folder / BOARD, os.X_OK)
    )


def parse_card(*, output: bytes) -> AppCard | None:
    """
    Read a card from a board's output.

    Parameters
    ----------
    output : bytes
        What the board printed.

    Returns
    -------
    AppCard | None
        The card, or None unless the output is a JSON object whose
        ``name``, ``status`` and ``wait`` are strings that fit, with a
        name that is not empty.
    """
    try:
        value = json.loads(output)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    match [value.get(field) for field in ('name', 'status', 'wait')]:
        case [str() as name, str() as status, str() as wait]:
            card = AppCard(name=name, status=status, wait=wait)
        case _:
            return None
    fits = len(card.status) <= LINE_MAX and len(card.wait) <= LINE_MAX
    return card if fits and 0 < len(card.name) <= NAME_MAX else None


async def end_group(*, process: asyncio.subprocess.Process) -> None:
    """
    Kill a process's whole group and wait for the process.

    A group that has already exited is ignored. On macOS an exited
    process that has not been reaped raises `PermissionError`.

    Parameters
    ----------
    process : asyncio.subprocess.Process
        A process that leads its own session.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def board_output(*, board: Path, data_dir: Path) -> bytes | None:
    """
    Run a board's ``card`` command in its own session.

    A board still running after `CARD_TIMEOUT` is killed with every
    process it started.

    Parameters
    ----------
    board : Path
        The app's board program.
    data_dir : Path
        The app's data folder.

    Returns
    -------
    bytes | None
        What it printed, or None, with a warning logged, when it could not
        start, timed out or exited non-zero.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            board,
            'card',
            '--data-dir',
            data_dir,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.warning('app card could not start', extra={'board': str(board)})
        return None
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(), timeout=CARD_TIMEOUT,
        )
    except TimeoutError:
        await end_group(process=process)
        logger.warning('app card timed out', extra={'board': str(board)})
        return None
    if process.returncode != 0:
        logger.warning(
            'app card failed',
            extra={'board': str(board), 'code': process.returncode},
        )
        return None
    return output


class SystemAppSwitch:
    """
    The apps installed under the release and data folders.

    Parameters
    ----------
    own : Callable[[], AppCard]
        The card of the app running now.
    share : Path
        The folder holding the ``active-app`` record.
    opt : Path
        The folder holding ``apps/<key>/current``, each app's release.
    data : Path
        The folder holding ``apps/<key>``, each app's data.
    systemctl : str
        The systemctl program.
    clock : Callable[[], float]
        Monotonic seconds.
    """

    def __init__(
        self,
        *,
        own: Callable[[], AppCard],
        share: Path = Path('/usr/local/share/paperroute'),
        opt: Path = Path('/opt/paperroute'),
        data: Path = Path('/var/lib/paperroute'),
        systemctl: str = '/usr/bin/systemctl',
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.own = own
        self.share = share
        self.opt = opt
        self.data = data
        self.systemctl = systemctl
        self.clock = clock
        self.apps: tuple[InstalledApp, ...] = ()
        self.refreshed_at: float | None = None
        self.task: asyncio.Task[None] | None = None

    async def card(self, *, key: str, active: bool) -> AppCard:
        """
        Find an app's card.

        Parameters
        ----------
        key : str
            The app.
        active : bool
            Whether it is the active app, whose card is `own`.

        Returns
        -------
        AppCard
            The card; for an app whose board gives none, the key as its
            name, with a warning logged.
        """
        if active:
            return self.own()
        output = await board_output(
            board=self.opt / 'apps' / key / BOARD,
            data_dir=self.data / 'apps' / key,
        )
        card = None if output is None else parse_card(output=output)
        if output is not None and card is None:
            logger.warning('app card unreadable', extra={'app': key})
        return card or AppCard(name=key.capitalize())

    async def refresh(self) -> None:
        """Find the installed apps again and read their cards together."""
        active = active_key(record=self.share / 'active-app')
        keys = () if active is None else release_keys(apps=self.opt / 'apps')
        cards = await asyncio.gather(
            *(
                self.card(key=key, active=key == active)
                for key in keys
            ),
        )
        self.apps = tuple(
            InstalledApp(key=key, card=card, active=key == active)
            for key, card in zip(keys, cards, strict=True)
        )
        self.refreshed_at = self.clock()

    def installed(self) -> tuple[InstalledApp, ...]:
        """
        List the installed apps without waiting.

        Starts a refresh in the background when none has run, or the last
        is older than `CARDS_FRESH`, and none is running.

        Returns
        -------
        tuple[InstalledApp, ...]
            The apps the last refresh found, in key order; empty before
            the first. The active app's card is asked for again, so it
            follows changes in the app since the refresh.
        """
        stale = (
            self.refreshed_at is None
            or self.clock() - self.refreshed_at > CARDS_FRESH
        )
        if stale and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.refresh())
        return tuple(
            dataclasses.replace(app, card=self.own()) if app.active else app
            for app in self.apps
        )

    async def switch(self, *, key: str) -> bool:
        """
        Ask the system to make an installed app the active one.

        Parameters
        ----------
        key : str
            The app.

        Returns
        -------
        bool
            False, running nothing, unless the app is listed and not
            active; else whether ``paperroute-switch@<key>.service``
            started within `SWITCH_TIMEOUT`.
        """
        if not any(app.key == key and not app.active for app in self.apps):
            return False
        return await run_systemctl(
            systemctl=self.systemctl,
            args=['start', f'paperroute-switch@{key}.service'],
            timeout=SWITCH_TIMEOUT,
        )
