"""Tests for listing the installed apps and asking for a switch."""

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from paperroute_device.adapters import app_switch
from paperroute_device.adapters.app_switch import CARDS_FRESH, SystemAppSwitch
from paperroute_device.core.device_model import AppCard, InstalledApp

OWN = AppCard(name='First', status='here')
DEMO_CARD = '{"name": "Demo", "status": "s", "wait": "w"}'


class Box:
    """
    A device's app folders under a temporary directory.

    Parameters
    ----------
    root : Path
        The temporary directory.
    """

    def __init__(self, *, root: Path) -> None:
        self.root = root
        self.share = root / 'share'
        self.opt = root / 'opt'
        self.data = root / 'data'
        self.now = 0.0
        self.share.mkdir()
        (self.share / 'active-app').write_text('first\n', encoding='utf-8')
        self.systemctl = root / 'systemctl'
        self.set_systemctl(script='exit 0')
        self.switch = SystemAppSwitch(
            own=lambda: OWN,
            share=self.share,
            opt=self.opt,
            data=self.data,
            systemctl=str(self.systemctl),
            clock=lambda: self.now,
        )

    def add_app(self, *, key: str, script: str) -> None:
        """
        Install an app whose board logs its arguments, then runs a script.

        Parameters
        ----------
        key : str
            The app.
        script : str
            Shell lines the board runs after logging.
        """
        bin_dir = self.opt / 'apps' / key / 'current' / '.venv' / 'bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        board = bin_dir / 'paperroute-board'
        board.write_text(
            '#!/bin/bash\n'
            f'echo "$*" >> {self.root / f"board-{key}.log"}\n'
            f'{script}\n',
            encoding='utf-8',
        )
        board.chmod(0o755)

    def set_systemctl(self, *, script: str) -> None:
        """
        Write a systemctl that logs its arguments, then runs a script.

        Parameters
        ----------
        script : str
            Shell lines it runs after logging.
        """
        self.systemctl.write_text(
            '#!/bin/bash\n'
            f'echo "$*" >> {self.root / "systemctl.log"}\n'
            f'{script}\n',
            encoding='utf-8',
        )
        self.systemctl.chmod(0o755)

    def board_log(self, key: str) -> list[str]:
        """
        Read what an app's board was asked.

        Parameters
        ----------
        key : str
            The app.

        Returns
        -------
        list[str]
            One line per run.
        """
        return log_lines(path=self.root / f'board-{key}.log')

    def systemctl_log(self) -> list[str]:
        """
        Read what systemctl was asked.

        Returns
        -------
        list[str]
            One line per run.
        """
        return log_lines(path=self.root / 'systemctl.log')


def log_lines(*, path: Path) -> list[str]:
    """
    Read a log's lines.

    Parameters
    ----------
    path : Path
        The log.

    Returns
    -------
    list[str]
        Its lines; none when it is missing.
    """
    if not path.exists():
        return []
    return path.read_text(encoding='utf-8').splitlines()


@pytest.fixture(name='box')
def _box(tmp_path: Path) -> Box:
    """A device with apps first (active) and demo installed."""
    box = Box(root=tmp_path)
    box.add_app(key='first', script='echo "{}"')
    box.add_app(key='demo', script=f"echo '{DEMO_CARD}'")
    return box


async def drain(*, switch: SystemAppSwitch) -> None:
    """
    Let a background refresh finish.

    Parameters
    ----------
    switch : SystemAppSwitch
        The switch whose refresh to wait for.
    """
    for _ in range(200):
        await asyncio.sleep(0.01)
        if switch.task is None or switch.task.done():
            return


async def test_lists_installed_apps_with_the_active_one_marked(
    box: Box,
) -> None:
    """Each app is listed under its card; the active one is marked."""
    await box.switch.refresh()
    assert box.switch.installed() == (
        InstalledApp(
            key='demo',
            card=AppCard(name='Demo', status='s', wait='w'),
            active=False,
        ),
        InstalledApp(key='first', card=OWN, active=True),
    )


async def test_other_card_comes_from_its_release(box: Box) -> None:
    """Only the other app's board is asked, for its own data folder."""
    await box.switch.refresh()
    assert box.board_log('demo') == [f'card --data-dir {box.data}/apps/demo']
    assert box.board_log('first') == []


async def test_app_without_a_release_is_not_listed(box: Box) -> None:
    """A folder with no board in its release is left out."""
    (box.opt / 'apps' / 'empty').mkdir()
    await box.switch.refresh()
    assert [app.key for app in box.switch.installed()] == ['demo', 'first']


@pytest.mark.parametrize('script', [
    'exit 1',
    'echo nonsense',
    'echo "[]"',
    """echo '{"name": "", "status": "s", "wait": "w"}'""",
    """echo '{"name": 5, "status": "s", "wait": "w"}'""",
    'sleep 5 & wait',
])
async def test_broken_card_falls_back_to_the_key(
    *,
    box: Box,
    script: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A card that fails, hangs or is junk gives the key as the name."""
    monkeypatch.setattr(app_switch, 'CARD_TIMEOUT', 0.2)
    box.add_app(key='demo', script=script)
    started = time.monotonic()
    await box.switch.refresh()
    assert time.monotonic() - started < 1
    assert box.switch.installed()[0] == InstalledApp(
        key='demo', card=AppCard(name='Demo'), active=False,
    )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


async def test_hung_cards_are_read_at_the_same_time(
    box: Box,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two hung cards take about one timeout, each warned about once."""
    monkeypatch.setattr(app_switch, 'CARD_TIMEOUT', 0.5)
    box.add_app(key='demo', script='sleep 5 & wait')
    box.add_app(key='third', script='sleep 5 & wait')
    started = time.monotonic()
    await box.switch.refresh()
    assert time.monotonic() - started < 0.9
    assert box.switch.installed() == (
        InstalledApp(key='demo', card=AppCard(name='Demo'), active=False),
        InstalledApp(key='first', card=OWN, active=True),
        InstalledApp(key='third', card=AppCard(name='Third'), active=False),
    )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


async def test_board_that_cannot_start_falls_back_to_the_key(
    box: Box,
) -> None:
    """A board whose interpreter is missing gives the key as the name."""
    board = box.opt / 'apps/demo/current/.venv/bin/paperroute-board'
    board.write_text('#!/nonexistent/python\n', encoding='utf-8')
    await box.switch.refresh()
    assert box.switch.installed()[0].card == AppCard(name='Demo')


async def test_long_card_falls_back_to_the_key(box: Box) -> None:
    """A name or line too long for the page gives the key as the name."""
    name = 'n' * (app_switch.NAME_MAX + 1)
    box.add_app(
        key='demo',
        script=f"""echo '{{"name": "{name}", "status": "", "wait": ""}}'""",
    )
    await box.switch.refresh()
    assert box.switch.installed()[0].card == AppCard(name='Demo')


async def test_no_active_record_lists_nothing(box: Box) -> None:
    """With no active-app record there is no list."""
    (box.share / 'active-app').unlink()
    await box.switch.refresh()
    assert box.switch.installed() == ()


async def test_bad_active_record_lists_nothing(box: Box) -> None:
    """An active-app record that names no app gives no list."""
    (box.share / 'active-app').write_text('../demo\n', encoding='utf-8')
    await box.switch.refresh()
    assert box.switch.installed() == ()


async def test_installed_starts_the_first_refresh_itself(box: Box) -> None:
    """The first call answers at once and starts a refresh."""
    assert box.switch.installed() == ()
    await drain(switch=box.switch)
    assert [app.key for app in box.switch.installed()] == ['demo', 'first']


async def test_missing_directories_list_nothing(tmp_path: Path) -> None:
    """Folders that do not exist give no list and raise nothing."""
    switch = SystemAppSwitch(
        own=lambda: OWN,
        share=tmp_path / 'share',
        opt=tmp_path / 'opt',
        data=tmp_path / 'data',
    )
    await switch.refresh()
    assert not switch.installed()


async def test_missing_apps_folder_lists_nothing(box: Box) -> None:
    """An active record with no apps folder gives no list."""
    switch = SystemAppSwitch(
        own=lambda: OWN, share=box.share, opt=box.root / 'none',
    )
    await switch.refresh()
    assert not switch.installed()


async def test_installed_refreshes_in_the_background_when_stale(
    box: Box,
) -> None:
    """A stale list is given at once while a new one is made."""
    await box.switch.refresh()
    box.now = CARDS_FRESH + 1
    box.add_app(
        key='demo',
        script="""echo '{"name": "Demo", "status": "new", "wait": "w"}'""",
    )
    assert box.switch.installed()[0].card.status == 's'
    await drain(switch=box.switch)
    assert box.switch.installed()[0].card.status == 'new'


async def test_installed_keeps_a_fresh_list(box: Box) -> None:
    """A fresh list starts no refresh."""
    await box.switch.refresh()
    box.now = CARDS_FRESH
    box.switch.installed()
    await drain(switch=box.switch)
    assert box.board_log('demo') == [f'card --data-dir {box.data}/apps/demo']


async def test_switch_starts_the_switch_unit(box: Box) -> None:
    """Switching to an installed app starts its switch unit."""
    await box.switch.refresh()
    assert await box.switch.switch(key='demo') is True
    assert box.systemctl_log() == ['start paperroute-switch@demo.service']


async def test_switch_reports_a_refusal(box: Box) -> None:
    """A switch the system refuses gives False."""
    box.set_systemctl(script='echo "switch-app: busy" >&2\nexit 1')
    await box.switch.refresh()
    assert await box.switch.switch(key='demo') is False


@pytest.mark.parametrize('key', ['first', 'nosuch', '', '../demo'])
async def test_switch_refuses_an_active_or_unknown_key(
    box: Box, key: str,
) -> None:
    """Only an installed app that is not active is switched to."""
    await box.switch.refresh()
    assert await box.switch.switch(key=key) is False
    assert box.systemctl_log() == []


async def test_end_group_ignores_a_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group that cannot be signalled is not an error."""
    def deny(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, 'killpg', deny)
    process = await asyncio.create_subprocess_exec('true')
    await app_switch.end_group(process=process)
    assert process.returncode == 0


async def test_installed_reads_the_active_card_fresh(box: Box) -> None:
    """The active app's card is asked again, not kept from the refresh."""
    cards = [AppCard(name='First', status='old')]
    box.switch.own = lambda: cards[-1]
    await box.switch.refresh()
    cards.append(AppCard(name='First', status='new'))
    assert box.switch.installed()[-1].card.status == 'new'
