"""Tests for deploy files: install release checks."""

import os
import shutil
from pathlib import Path
from typing import Final

import pytest

from tests.deploy_files.helpers import (
    APP_NAME,
    BAD_SHAS,
    BIN,
    SET_ZONES,
    SHA,
    SWITCH_STUB,
    Sandbox,
    run_functions,
    run_script,
    script_function,
    script_text,
    script_value,
    snapshot,
    stage_tree,
    user_env,
)
from tests.deploy_files.script_helpers import release_functions

BAD_APPS: Final[tuple[str, ...]] = ('', 'Sydney', '../x', 'syd ney')


USAGE: Final[str] = 'install-release: usage: install-release SHA APP\n'


@pytest.mark.parametrize('value', BAD_SHAS)
def test_install_release_rejects_bad_sha(
    sandbox: Sandbox,
    value: str,
) -> None:
    """A malformed sha exits 2 before anything is touched."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(value, APP_NAME),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the sha must be 7 to 12 lowercase hex digits\n',
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_extra_arguments(sandbox: Sandbox) -> None:
    """install-release takes exactly two arguments."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME, APP_NAME),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == USAGE
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_symlinked_stage(sandbox: Sandbox) -> None:
    """A staged tree that is a symlink exits 2."""
    elsewhere = stage_tree(stage=sandbox.root / 'elsewhere')
    (sandbox.stage / SHA).symlink_to(elsewhere)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: the staged tree is not a real directory\n'
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_missing_stage(sandbox: Sandbox) -> None:
    """A sha with nothing staged exits 2."""
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: the staged tree is not a real directory\n'
    )
    assert snapshot(root=sandbox.root) == before


@pytest.mark.parametrize('kind', ('symlink', 'fifo'))
def test_install_release_rejects_odd_entries(
    sandbox: Sandbox,
    kind: str,
) -> None:
    """A staged tree holding anything but files and directories exits 2."""
    tree = stage_tree(stage=sandbox.stage)
    if kind == 'symlink':
        (tree / 'src' / 'link').symlink_to('/etc/passwd')
    else:
        os.mkfifo(tree / 'src' / 'pipe')
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: the staged tree may hold only regular files'
        ' and directories\n'
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_wrong_owner(sandbox: Sandbox) -> None:
    """A tree not owned by the sudo caller exits 2."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    env = sandbox.env | {'SUDO_USER': 'nobody'}
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=env,
    )
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: the staged tree is not owned by nobody\n'
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_unknown_owner(sandbox: Sandbox) -> None:
    """A sudo caller that names no real user exits 2."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    env = sandbox.env | {'SUDO_USER': 'no-such-user-paperroute'}
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=env,
    )
    assert result.returncode == 2
    assert result.stderr == 'install-release: unknown user\n'
    assert snapshot(root=sandbox.root) == before


def test_install_release_valid_stage_needs_root(sandbox: Sandbox) -> None:
    """A valid stage passes every check, then stops for want of root."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: checks passed; installing needs root\n'
    )
    assert snapshot(root=sandbox.root) == before


@pytest.mark.parametrize('value', BAD_APPS)
def test_install_release_rejects_bad_app(
    sandbox: Sandbox,
    value: str,
) -> None:
    """An app name that is not lowercase letters and digits exits 2."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, value),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the app must be lowercase letters and digits\n',
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_needs_the_app(sandbox: Sandbox) -> None:
    """A call with the sha alone gives the usage error."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA,),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (2, USAGE)
    assert snapshot(root=sandbox.root) == before


@pytest.mark.parametrize('kind', ('missing', 'directory', 'symlink'))
def test_install_release_rejects_missing_app(
    sandbox: Sandbox,
    kind: str,
) -> None:
    """A tree whose app has no regular pyproject.toml exits 2."""
    tree = stage_tree(stage=sandbox.stage)
    project = tree / 'apps' / f'paperroute-{APP_NAME}' / 'pyproject.toml'
    project.unlink()
    if kind == 'directory':
        project.mkdir()
    elif kind == 'symlink':
        project.symlink_to(tree / 'pyproject.toml')
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the staged tree has no app paperroute-sydney\n',
    )
    assert snapshot(root=sandbox.root) == before


@pytest.mark.parametrize('name', ('paperroute-stub', 'README.md', '.x'))
def test_install_release_rejects_other_apps(
    sandbox: Sandbox,
    name: str,
) -> None:
    """A tree whose apps directory holds anything else exits 2."""
    tree = stage_tree(stage=sandbox.stage)
    (tree / 'apps' / name).write_text('', encoding='utf-8')
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the staged tree holds another app\n',
    )
    assert snapshot(root=sandbox.root) == before


@pytest.mark.parametrize('board', (False, True))
def test_roll_back_stops_when_previous_predates_scripts(
    tmp_path: Path,
    board: bool,
) -> None:
    """A previous release without paperroute-board is never switched to."""
    body = script_function(path=BIN / 'install-release', name='roll_back')
    test = '! -x $prev/.venv/bin/paperroute-board'
    assert body.index(test) < body.index('switch_to "$prev"')
    branch = body[body.index(test):body.index('elif')]
    assert 'systemctl stop paperroute' in branch
    assert 'switch_to' not in branch
    prev = tmp_path / 'releases' / 'abc1233'
    new = tmp_path / 'releases' / SHA
    (prev / '.venv' / 'bin').mkdir(parents=True)
    new.mkdir()
    if board:
        script = prev / '.venv' / 'bin' / 'paperroute-board'
        script.write_text('#!/bin/sh\n', encoding='utf-8')
        script.chmod(0o755)
        (prev / 'deploy').mkdir()
        (prev / 'deploy' / 'CONTRACT').write_text('1\n', encoding='utf-8')
    current = tmp_path / 'current'
    current.symlink_to(new)
    log = tmp_path / 'systemctl.log'
    program = (
        f'SHA={SHA} prev={prev} CURRENT={current} CONTRACT=1\n'
        f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
        'journalctl() { :; }\n'
        f'{SWITCH_STUB}\n'
        'roll_back\n'
    )
    result = run_functions(
        names=('contract_in', 'roll_back'),
        program=program,
        env=user_env(),
    )
    rolling = f'install-release: {SHA} did not come up; rolling back\n'
    assert result.returncode == 1
    if board:
        assert result.stderr == rolling
        assert os.readlink(current) == str(prev)
        assert log.read_text(encoding='utf-8') == 'restart paperroute\n'
    else:
        assert result.stderr == rolling + (
            'install-release: abc1233 predates paperroute-board; stopped'
            ' paperroute. Do not reboot. Re-provision from its commit to'
            ' roll back.\n'
        )
        assert os.readlink(current) == str(new)
        assert log.read_text(encoding='utf-8') == 'stop paperroute\n'


def test_install_release_constants() -> None:
    """Three releases are kept, and the app gets 90 s to come up."""
    script = BIN / 'install-release'
    assert script_value(path=script, name='KEEP') == '3'
    assert script_value(path=script, name='HEALTH_WAIT') == '90'
    assert script_value(path=script, name='LOCK') == (
        '/run/paperroute-install.lock'
    )


@pytest.mark.parametrize(
    'line',
    (
        '../evil.timer',
        'paperroute-job.service',
        'paperroute-job.timer x',
        'paperroute-Job.timer',
        '',
    ),
)
def test_install_release_validates_manifest_names(
    tmp_path: Path,
    line: str,
) -> None:
    """The zone tool refuses a manifest line that is not a timer name."""
    manifest = tmp_path / 'apps' / 'demo' / 'timers'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f'paperroute-job.timer\n{line}\n', encoding='utf-8')
    result = release_functions(
        names=('manifest_timers',),
        program=(
            f'SHARE={tmp_path} APP_NAME=demo\n'
            'TIMERS=$(manifest_timers) || exit\n'
        ),
        script=SET_ZONES,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'set-timer-zones: the timer manifest names a bad timer\n',
    )


def contract_tree(*, root: Path, contract: str | None) -> Path:
    """
    Stage a tree that carries a units contract, or none.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    contract : str | None
        The text of ``deploy/CONTRACT``, or None for no file.

    Returns
    -------
    Path
        The tree.
    """
    tree = stage_tree(stage=root / 'stage')
    if contract is not None:
        (tree / 'deploy').mkdir()
        (tree / 'deploy' / 'CONTRACT').write_text(contract, encoding='utf-8')
    return tree


@pytest.mark.parametrize(
    ('release', 'device', 'message'),
    (
        ('1\n', '0\n', 'needs units contract 1; the device has 0'),
        ('1\n', None, 'needs units contract 1; the device has 0'),
        (None, '1\n', 'needs units contract 0; the device has 1'),
        ('2\n', '1\n', 'needs units contract 2; the device has 1'),
    ),
)
def test_install_release_refuses_other_contract(
    *,
    tmp_path: Path,
    release: str | None,
    device: str | None,
    message: str,
) -> None:
    """A release built for other units is refused and its stage removed."""
    tree = contract_tree(root=tmp_path, contract=release)
    (tmp_path / 'apps' / APP_NAME).mkdir(parents=True)
    (tmp_path / 'apps' / APP_NAME / 'timers').write_text('', encoding='utf-8')
    if device is not None:
        (tmp_path / 'units-contract').write_text(device, encoding='utf-8')
    result = release_functions(
        names=('contract_in', 'remove_stage', 'contract_ok'),
        program=(
            f'OWNER=me TREE={tree} SHARE={tmp_path}\n'
            'runuser() { shift 3; "$@"; }\ncontract_ok\n'
        ),
    )
    assert (result.returncode, result.stderr) == (
        1,
        f'install-release: this release {message}. Run scripts/provision'
        ' HOST APP first.\n',
    )
    assert not tree.exists()
    lines = script_text(path=BIN / 'install-release').splitlines()
    steps = [
        lines.index(step)
        for step in (
            'validate',
            'contract_ok',
            'build',
            'paperroute-set-timer-zones check "$APP_NAME"',
        )
    ]
    assert steps == sorted(steps)
    assert steps[-1] < lines.index('    take_over')


def test_install_release_needs_the_app_provisioned(sandbox: Sandbox) -> None:
    """A release of an app provision did not add is refused first."""
    stage_tree(stage=sandbox.stage)
    share = sandbox.root / 'share' / 'apps'
    shutil.rmtree(share / APP_NAME)
    (share / 'first').mkdir()
    (share / 'first' / 'timers').write_text('', encoding='utf-8')
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, APP_NAME),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the device has no app sydney. Run scripts/provision'
        ' HOST sydney first.\n',
    )
    assert snapshot(root=sandbox.root) == before
    calls = script_function(
        path=BIN / 'install-release',
        name='validate',
    ).splitlines()
    assert calls.index('    app_provisioned') < calls.index(
        '    APP=paperroute-$APP_NAME',
    )


def test_install_release_accepts_the_same_contract(tmp_path: Path) -> None:
    """A release of the installed contract keeps its stage and goes on."""
    tree = contract_tree(root=tmp_path, contract='1\n')
    (tmp_path / 'units-contract').write_text('1\n', encoding='utf-8')
    result = release_functions(
        names=('contract_in', 'remove_stage', 'contract_ok'),
        program=(
            f'OWNER=me TREE={tree} SHARE={tmp_path}\n'
            'runuser() { exit 9; }\ncontract_ok\nprintf "%s\\n" "$CONTRACT"\n'
        ),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, '1\n', '')
    assert tree.is_dir()


@pytest.mark.parametrize('contract', (None, '2\n'))
def test_roll_back_stops_across_contracts(
    tmp_path: Path,
    contract: str | None,
) -> None:
    """A previous release of another units contract is never switched to."""
    prev = tmp_path / 'releases' / 'abc1233'
    new = tmp_path / 'releases' / SHA
    (prev / '.venv' / 'bin').mkdir(parents=True)
    script = prev / '.venv' / 'bin' / 'paperroute-board'
    script.write_text('#!/bin/sh\n', encoding='utf-8')
    script.chmod(0o755)
    if contract is not None:
        (prev / 'deploy').mkdir()
        (prev / 'deploy' / 'CONTRACT').write_text(contract, encoding='utf-8')
    new.mkdir()
    current = tmp_path / 'current'
    current.symlink_to(new)
    log = tmp_path / 'systemctl.log'
    result = run_functions(
        names=('contract_in', 'roll_back'),
        program=(
            f'SHA={SHA} prev={prev} CURRENT={current} CONTRACT=1\n'
            f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
            f'journalctl() {{ :; }}\n{SWITCH_STUB}\nroll_back\n'
        ),
        env=user_env(),
    )
    was = '0' if contract is None else '2'
    assert (result.returncode, result.stderr) == (
        1,
        f'install-release: {SHA} did not come up; rolling back\n'
        f'install-release: abc1233 is units contract {was}, not 1; stopped'
        ' paperroute. Do not reboot. Fix forward and redeploy.\n',
    )
    assert os.readlink(current) == str(new)
    assert log.read_text(encoding='utf-8') == 'stop paperroute\n'
