"""Shared fixtures for the deploy files tests."""

import os
from pathlib import Path

import pytest

from tests.deploy_files.helpers import APP_NAME, Sandbox, user_env
from tests.deploy_files.layout_helpers import (
    SWITCH_INSTALL_FILES,
    SWITCH_RUN,
    SWITCH_RUNUSER,
    SWITCH_SET_ZONES,
    SWITCH_SYSTEMCTL,
    SwitchBox,
    add_release,
)
from tests.deploy_files.script_helpers import TilesSandbox, stub_commands


@pytest.fixture(name='sandbox')
def fixture_sandbox(tmp_path: Path) -> Sandbox:
    """
    Make an empty stage, an opt holding Sydney's releases directory and a
    share holding Sydney's empty timer list, and an environment that
    points the install scripts at them.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    Sandbox
        The directories and the environment.
    """
    if os.geteuid() == 0:
        pytest.fail('run the install script tests as a normal user')
    stage = tmp_path / 'stage'
    opt = tmp_path / 'opt'
    share = tmp_path / 'share'
    stage.mkdir()
    (opt / 'apps' / APP_NAME / 'releases').mkdir(parents=True)
    (share / 'apps' / APP_NAME).mkdir(parents=True)
    (share / 'apps' / APP_NAME / 'timers').write_text('', encoding='utf-8')
    env = user_env(
        PAPERROUTE_STAGE=str(stage),
        PAPERROUTE_OPT=str(opt),
        PAPERROUTE_SHARE=str(share),
    )
    return Sandbox(root=tmp_path, stage=stage, opt=opt, env=env)


@pytest.fixture(name='tiles')
def fixture_tiles(tmp_path: Path) -> TilesSandbox:
    """
    Stage a tiles file and make an empty data directory.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    TilesSandbox
        The paths and an environment that points install-tiles at them.
    """
    if os.geteuid() == 0:
        pytest.fail('run the install script tests as a normal user')
    staged = tmp_path / 'stage' / 'tiles.db'
    staged.parent.mkdir()
    staged.write_text('new tiles\n', encoding='utf-8')
    data = tmp_path / 'data'
    data.mkdir()
    env = user_env(
        PAPERROUTE_TILES_STAGE=str(staged),
        PAPERROUTE_DATA=str(data),
    )
    return TilesSandbox(root=tmp_path, staged=staged, data=data, env=env)


@pytest.fixture(name='switch_box')
def fixture_switch_box(tmp_path: Path) -> SwitchBox:
    """
    Make a device holding first, active, and demo, with stubs on PATH.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    SwitchBox
        The folders, demo's board and the environment.
    """
    share, opt, etc = tmp_path / 'share', tmp_path / 'opt', tmp_path / 'etc'
    for app, ready, timers in (
        ('first', 'paperroute-one.service', ''),
        ('demo', 'paperroute-job.service', 'paperroute-job.timer\n'),
    ):
        (share / 'apps' / app).mkdir(parents=True)
        (share / 'apps' / app / 'ready').write_text(
            f'{ready}\n',
            encoding='utf-8',
        )
        (share / 'apps' / app / 'timers').write_text(
            timers,
            encoding='utf-8',
        )
    (share / 'active-app').write_text('first\n', encoding='utf-8')
    etc.mkdir()
    add_release(opt=opt, app='first', zone='UTC')
    board = add_release(opt=opt, app='demo', zone='Europe/London')
    stubs = stub_commands(
        root=tmp_path,
        scripts={
            'systemctl': SWITCH_SYSTEMCTL,
            'flock': '#!/bin/sh\nexit "${FLOCK_STATUS:-0}"\n',
            'paperroute-install-files': SWITCH_INSTALL_FILES,
            'runuser': SWITCH_RUNUSER,
            'paperroute-set-timer-zones': SWITCH_SET_ZONES,
            'systemd-run': SWITCH_RUN,
        },
    )
    env = user_env(
        PATH=f'{stubs}:{os.environ["PATH"]}',
        SWITCH_ROOT=str(tmp_path),
    )
    return SwitchBox(
        root=tmp_path,
        share=share,
        opt=opt,
        etc=etc,
        board=board,
        env=env,
    )
