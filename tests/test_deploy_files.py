"""Tests for the device files and the provision and deploy scripts."""

import configparser
import getpass
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

import numpy as np
import pytest

from paperroute_hardware.screen import load_frame

ROOT: Final[Path] = Path(__file__).parents[1]
DEPLOY: Final[Path] = ROOT / 'deploy'
BIN: Final[Path] = DEPLOY / 'bin'
SYSTEMD: Final[Path] = DEPLOY / 'systemd'
CONFIG_TXT: Final[Path] = Path(__file__).parent / 'fixtures' / 'config.txt'
BIN_SCRIPTS: Final[tuple[str, ...]] = (
    'install-files',
    'install-release',
    'install-tiles',
    'paperroute-led',
    'provision-device',
)
OFF_HOOK: Final[Path] = DEPLOY / 'system-shutdown' / 'paperroute-off-screen'
SHELL_SCRIPTS: Final[tuple[Path, ...]] = (
    OFF_HOOK,
    ROOT / 'scripts' / 'provision',
    ROOT / 'scripts' / 'deploy',
    ROOT / 'scripts' / 'simulate-pi-build',
    *(BIN / name for name in BIN_SCRIPTS),
)
SUDOERS_RULE: Final[str] = (
    'pashri ALL=(root) NOPASSWD: '
    '/usr/local/sbin/paperroute-install-release, '
    '/usr/local/sbin/paperroute-install-files "", '
    '/usr/local/sbin/paperroute-install-tiles ""'
)
SECRETS_LINE: Final[str] = (
    "setsid -w runuser -u paperroute -- sh -c 'umask 077 && "
    "{ [ -e /var/lib/paperroute/secrets ] || "
    ": >/var/lib/paperroute/secrets; }'"
)
BAD_SHAS: Final[tuple[str, ...]] = (
    '',
    'ABCDEF1',
    'abc',
    '1234567890abc',
    'abc1234;id',
    '../abc1234',
)
SHA: Final[str] = 'abc1234'
KMS_OFF: Final[str] = 's/^dtoverlay=vc4-kms-v3d/#&/'
ROOT_SAFE_ENVIRONMENT: Final[frozenset[str]] = frozenset(
    {'PAPERROUTE_API_KEY_FILE', 'FONTCONFIG_FILE'}
)
LED_PATTERNS: Final[dict[str, dict[str, str]]] = {
    'boot': {'trigger': 'timer', 'delay_on': '100', 'delay_off': '100'},
    'rebuild': {
        'trigger': 'timer',
        'delay_on': '1000',
        'delay_off': '1000',
    },
    'on': {'trigger': 'default-on'},
}
PYTHON: Final[str] = '/opt/paperroute/current/.venv/bin/python'
FONTS_CONF: Final[str] = (
    'packages/paperroute-device/src/paperroute_device/fontconfig/fonts.conf'
)
UNIT_ENVIRONMENT: Final[str] = (
    'PAPERROUTE_API_KEY_FILE=/var/lib/paperroute/secrets'
    f' FONTCONFIG_FILE=/opt/paperroute/current/{FONTS_CONF}'
)
JOURNALD: Final[dict[str, dict[str, str]]] = {
    'Journal': {'Storage': 'volatile', 'RuntimeMaxUse': '24M'},
}
APP_SERVICE: Final[dict[str, str]] = {
    'Type': 'notify',
    'NotifyAccess': 'main',
    'User': 'paperroute',
    'Group': 'paperroute',
    'SupplementaryGroups': 'spi gpio',
    'Environment': UNIT_ENVIRONMENT,
    'ExecStart': (
        f'{PYTHON} -m paperroute run --display web --panel --knobs'
        ' --timetable /var/lib/paperroute/timetable.db'
        ' --tiles /var/lib/paperroute/tiles.db'
        ' --state /var/lib/paperroute/state.json'
        ' --error-log /var/lib/paperroute/log/errors.jsonl'
        ' --rebuild --power --wifi'
        ' --version-file /opt/paperroute/current/VERSION'
    ),
    'ExecStopPost': '+/usr/local/bin/paperroute-led boot',
    'Restart': 'always',
    'RestartSec': '5',
    'WatchdogSec': '30',
    'TimeoutStopSec': '15',
    'AmbientCapabilities': 'CAP_NET_BIND_SERVICE',
    'CapabilityBoundingSet': 'CAP_NET_BIND_SERVICE',
    'NoNewPrivileges': 'yes',
    'ProtectSystem': 'strict',
    'ReadWritePaths': '/var/lib/paperroute',
    'ProtectHome': 'yes',
    'PrivateTmp': 'yes',
    'SystemCallFilter': '~@debug',
}
TIMETABLE_SERVICE: Final[dict[str, str]] = {
    'Type': 'oneshot',
    'User': 'paperroute',
    'Group': 'paperroute',
    'Environment': UNIT_ENVIRONMENT,
    'ExecStartPre': '+/usr/local/bin/paperroute-led rebuild',
    'ExecStart': (
        '/usr/bin/env TMPDIR=/var/lib/paperroute/work'
        f' {PYTHON} -m paperroute.build refresh'
        ' --data-dir /var/lib/paperroute'
        ' --error-log /var/lib/paperroute/log/errors.jsonl'
    ),
    'ExecStartPost': '+/usr/bin/systemctl start --no-block paperroute',
    'ExecStopPost': '+/usr/local/bin/paperroute-led restore',
    'Nice': '19',
    'IOSchedulingClass': 'idle',
    'OOMScoreAdjust': '500',
    'TimeoutStartSec': '3h',
    'NoNewPrivileges': 'yes',
    'ProtectSystem': 'strict',
    'ReadWritePaths': '/var/lib/paperroute',
    'ProtectHome': 'yes',
    'PrivateTmp': 'yes',
    'SystemCallFilter': '~@debug',
}
UNITS: Final[dict[str, dict[str, dict[str, str]]]] = {
    'paperroute.service': {
        'Unit': {
            'Description': 'paperroute',
        },
        'Service': APP_SERVICE,
        'Install': {'WantedBy': 'multi-user.target'},
    },
    'paperroute-timetable.service': {
        'Unit': {
            'Description': 'paperroute timetable refresh',
            'After': 'network-online.target time-sync.target',
            'Wants': 'network-online.target',
        },
        'Service': TIMETABLE_SERVICE,
    },
    'paperroute-timetable.timer': {
        'Timer': {
            'OnCalendar': '*-*-* 03:30:00 Australia/Sydney',
            'Persistent': 'true',
            'RandomizedDelaySec': '0',
        },
        'Install': {'WantedBy': 'timers.target'},
    },
    'paperroute-on-screen.service': {
        'Unit': {
            'ConditionPathExists': '/dev/spidev0.0',
        },
        'Service': {
            'Type': 'oneshot',
            'TimeoutStartSec': '30',
            'ExecStart': (
                '/opt/paperroute/current/.venv/bin/python'
                ' -m paperroute_device.screen --image'
                ' /usr/local/share/paperroute/deploy/screens/on.raw'
                ' --state /var/lib/paperroute/state.json'
            ),
        },
        'Install': {'WantedBy': 'multi-user.target'},
    },
    'paperroute-led-boot.service': {
        'Unit': {
            'DefaultDependencies': 'no',
            'Before': 'sysinit.target',
        },
        'Service': {
            'Type': 'oneshot',
            'ExecStart': '/usr/local/bin/paperroute-led boot',
        },
        'Install': {'WantedBy': 'sysinit.target'},
    },
}
ROOT_EXEC_KEYS: Final[tuple[str, ...]] = (
    'ExecStartPre',
    'ExecStartPost',
    'ExecStopPost',
)


class UnitParser(configparser.ConfigParser):
    """A parser for systemd units that keeps key case."""

    def optionxform(self, optionstr: str) -> str:
        """
        Keep a key exactly as written.

        Parameters
        ----------
        optionstr : str
            The key.

        Returns
        -------
        str
            The same key.
        """
        return optionstr


@dataclass(frozen=True)
class Sandbox:
    """Stage and releases directories that override the real ones."""

    root: Path
    stage: Path
    releases: Path
    env: dict[str, str]


def read_unit(name: str) -> dict[str, dict[str, str]]:
    """
    Parse a unit file into sections of keys and values.

    Parameters
    ----------
    name : str
        The unit's file name under ``deploy/systemd``.

    Returns
    -------
    dict[str, dict[str, str]]
        Each section's keys and values.
    """
    parser = UnitParser(
        strict=False,
        interpolation=None,
        delimiters=('=',),
    )
    parser.read(filenames=SYSTEMD / name, encoding='utf-8')
    return {
        section: dict(parser[section])
        for section in parser.sections()
    }


def run_script(
    name: str,
    args: tuple[str, ...],
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """
    Run a script from ``deploy/bin`` with bash as the current user.

    Parameters
    ----------
    name : str
        The script's file name.
    args : tuple[str, ...]
        Its arguments.
    env : dict[str, str]
        The environment to run it in.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    return subprocess.run(
        args=['bash', str(BIN / name), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def file_content(*, path: Path) -> str | bytes:
    """
    Read a file as text, or as bytes when it is not UTF-8.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    str | bytes
        Its text, or its bytes.
    """
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return path.read_bytes()


def snapshot(root: Path) -> dict[str, str | bytes]:
    """
    Describe every path under a directory, without following links.

    Parameters
    ----------
    root : Path
        The directory.

    Returns
    -------
    dict[str, str | bytes]
        Each relative path mapped to its link target, its text (its
        bytes when it is not text), ``'dir'`` or ``'other'``.
    """
    return {
        str(path.relative_to(root)): (
            f'link:{os.readlink(path)}'
            if path.is_symlink()
            else 'dir' if path.is_dir()
            else file_content(path=path) if path.is_file()
            else 'other'
        )
        for path in root.rglob('*')
    }


def script_text(path: Path) -> str:
    """
    Read a script.

    Parameters
    ----------
    path : Path
        The script.

    Returns
    -------
    str
        Its text.
    """
    return path.read_text(encoding='utf-8')


def kms_off() -> str:
    """
    Take the ``KMS_OFF`` sed expression from ``provision-device``.

    Returns
    -------
    str
        The expression.
    """
    found = re.search(
        pattern=r"^KMS_OFF='([^']*)'$",
        string=script_text(path=BIN / 'provision-device'),
        flags=re.MULTILINE,
    )
    assert found is not None
    return found.group(1)


def script_value(path: Path, name: str) -> str:
    """
    Take a variable's value from a script's top-level assignment.

    Parameters
    ----------
    path : Path
        The script.
    name : str
        The variable.

    Returns
    -------
    str
        The text after ``NAME=``.
    """
    found = re.search(
        pattern=rf'^{name}=(.*)$',
        string=script_text(path=path),
        flags=re.MULTILINE,
    )
    assert found is not None
    return found.group(1)


def commands(path: Path, word: str) -> list[str]:
    """
    Find a script's commands that use a word, with continuations joined.

    Parameters
    ----------
    path : Path
        The script.
    word : str
        The word to look for, such as a program name.

    Returns
    -------
    list[str]
        Each matching command on one line.
    """
    joined = script_text(path=path).replace('\\\n', ' ')
    return [
        ' '.join(line.split())
        for line in joined.splitlines()
        if re.search(pattern=rf'\b{word}\b', string=line)
        and not line.lstrip().startswith('#')
    ]


def environment_names(unit: dict[str, dict[str, str]]) -> set[str]:
    """
    List the variables a unit's ``Environment=`` sets for every command.

    Parameters
    ----------
    unit : dict[str, dict[str, str]]
        The parsed unit.

    Returns
    -------
    set[str]
        The variable names.
    """
    assignments = unit['Service'].get('Environment', '').split()
    return {assignment.split('=')[0] for assignment in assignments}


def led_dir(tmp_path: Path) -> Path:
    """
    Make files standing in for the ACT LED's sysfs attributes.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    Path
        The directory holding ``trigger``, ``delay_on``, ``delay_off``
        and ``brightness``, each empty.
    """
    led = tmp_path / 'ACT'
    led.mkdir()
    for name in ('trigger', 'delay_on', 'delay_off', 'brightness'):
        (led / name).write_text('', encoding='utf-8')
    return led


def led_state(led: Path) -> dict[str, str]:
    """
    Read back the LED attribute files that were written.

    Parameters
    ----------
    led : Path
        The stand-in directory.

    Returns
    -------
    dict[str, str]
        Each non-empty attribute's value, without the newline.
    """
    return {
        path.name: path.read_text(encoding='utf-8').strip()
        for path in sorted(led.iterdir())
        if path.read_text(encoding='utf-8')
    }


def user_env(**extra: str) -> dict[str, str]:
    """
    Copy this process's environment without ``SUDO_USER``.

    Parameters
    ----------
    **extra : str
        Variables to add.

    Returns
    -------
    dict[str, str]
        The environment.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key != 'SUDO_USER'
    }
    return env | extra


def edit_config(config: Path) -> None:
    """
    Run provision's ``edit_config`` function over a file.

    Parameters
    ----------
    config : Path
        The ``config.txt`` copy to edit in place.
    """
    found = re.search(
        pattern=r'^edit_config\(\) \{$.*?^\}$',
        string=script_text(path=BIN / 'provision-device'),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert found is not None
    program = (
        f"set -euo pipefail\nKMS_OFF='{kms_off()}'\n"
        f'{found.group(0)}\nedit_config "$1"\n'
    )
    subprocess.run(
        args=['bash', '-c', program, 'edit_config', str(config)],
        check=True,
        timeout=30,
    )


def last_section(text: str) -> list[str]:
    """
    Return the lines after the last ``[section]`` header.

    Parameters
    ----------
    text : str
        A ``config.txt``.

    Returns
    -------
    list[str]
        The header and the lines after it.
    """
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.startswith('[')
    ]
    return lines[starts[-1]:]


@pytest.fixture(name='sandbox')
def fixture_sandbox(tmp_path: Path) -> Sandbox:
    """
    Make empty stage and releases directories and an environment that
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
    releases = tmp_path / 'releases'
    stage.mkdir()
    releases.mkdir()
    env = user_env(
        PAPERROUTE_STAGE=str(stage),
        PAPERROUTE_RELEASES=str(releases),
    )
    return Sandbox(root=tmp_path, stage=stage, releases=releases, env=env)


def stage_tree(stage: Path) -> Path:
    """
    Stage a small valid release tree.

    Parameters
    ----------
    stage : Path
        The stage directory.

    Returns
    -------
    Path
        The tree, ``stage/<SHA>``.
    """
    tree = stage / SHA / 'src'
    tree.mkdir(parents=True)
    (tree / 'module.py').write_text('x = 1\n', encoding='utf-8')
    (stage / SHA / 'pyproject.toml').write_text(
        '[project]\n',
        encoding='utf-8',
    )
    return stage / SHA


@pytest.mark.parametrize('name', sorted(UNITS))
def test_unit_has_exact_values(name: str) -> None:
    """Each unit holds exactly the planned sections, keys and values."""
    assert read_unit(name=name) == UNITS[name]


def test_units_are_the_planned_set() -> None:
    """deploy/systemd holds the planned units and nothing else."""
    assert sorted(path.name for path in SYSTEMD.iterdir()) == sorted(UNITS)


def test_app_unit_is_notify_with_watchdog() -> None:
    """The app reports readiness and is restarted when it stops pinging."""
    service = read_unit(name='paperroute.service')['Service']
    assert (service['Type'], service['WatchdogSec']) == ('notify', '30')
    assert service['AmbientCapabilities'] == 'CAP_NET_BIND_SERVICE'
    assert service['CapabilityBoundingSet'] == 'CAP_NET_BIND_SERVICE'


def test_service_unit_passes_rebuild() -> None:
    """The app rebuilds an outdated timetable; the nightly timer stays."""
    service = read_unit(name='paperroute.service')['Service']
    timer = read_unit(name='paperroute-timetable.timer')['Timer']
    assert service['ExecStart'].endswith(
        ' --rebuild --power --wifi'
        ' --version-file /opt/paperroute/current/VERSION'
    )
    assert timer['OnCalendar'] == '*-*-* 03:30:00 Australia/Sydney'


def test_service_unit_drives_the_panel() -> None:
    """The unit asks for the panel and joins the spi and gpio groups."""
    service = read_unit(name='paperroute.service')['Service']
    assert ' --display web --panel ' in service['ExecStart']
    assert service['SupplementaryGroups'] == 'spi gpio'


def test_service_unit_reads_the_knobs() -> None:
    """The unit asks for the knobs."""
    service = read_unit(name='paperroute.service')['Service']
    assert ' --panel --knobs ' in service['ExecStart']


def test_root_exec_lines_carry_plus() -> None:
    """Every pre, post and stop-post command runs with the + prefix."""
    lines = [
        value
        for name in UNITS
        for key, value in read_unit(name=name).get('Service', {}).items()
        if key in ROOT_EXEC_KEYS
    ]
    assert len(lines) == 4
    assert all(line.startswith('+') for line in lines)


def test_timetable_unit_logs_errors_and_uses_work_tmp() -> None:
    """The refresh logs errors to the file and spills temp files to disk."""
    service = read_unit(name='paperroute-timetable.service')['Service']
    assert service['ExecStart'].endswith(
        '--error-log /var/lib/paperroute/log/errors.jsonl'
    )
    assert service['ExecStart'].startswith(
        '/usr/bin/env TMPDIR=/var/lib/paperroute/work '
    )
    assert (service['Nice'], service['OOMScoreAdjust']) == ('19', '500')


def test_sudoers_allows_exactly_the_three_scripts(tmp_path: Path) -> None:
    """The sudoers file holds one rule for the three root scripts."""
    path = DEPLOY / 'sudoers' / 'paperroute-deploy'
    rules = [
        line
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert rules == [SUDOERS_RULE]
    if shutil.which('visudo'):
        copy = tmp_path / 'paperroute-deploy'
        shutil.copyfile(src=path, dst=copy)
        copy.chmod(0o440)
        result = subprocess.run(
            args=['visudo', '-cf', str(copy)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr


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
        args=(value,),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the sha must be 7 to 12 lowercase hex digits\n',
    )
    assert snapshot(root=sandbox.root) == before


@pytest.mark.parametrize('value', (*BAD_SHAS, SHA))
def test_install_files_rejects_any_argument(
    sandbox: Sandbox,
    value: str,
) -> None:
    """install-files takes no arguments at all."""
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-files',
        args=(value,),
        env=sandbox.env,
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-files: takes no arguments\n',
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_extra_arguments(sandbox: Sandbox) -> None:
    """install-release takes exactly one argument."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA, SHA),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == 'install-release: usage: install-release SHA\n'
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_symlinked_stage(sandbox: Sandbox) -> None:
    """A staged tree that is a symlink exits 2."""
    elsewhere = stage_tree(stage=sandbox.root / 'elsewhere')
    (sandbox.stage / SHA).symlink_to(elsewhere)
    before = snapshot(root=sandbox.root)
    result = run_script(
        name='install-release',
        args=(SHA,),
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
        args=(SHA,),
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
        args=(SHA,),
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
        args=(SHA,),
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
        args=(SHA,),
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
        args=(SHA,),
        env=sandbox.env,
    )
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: checks passed; installing needs root\n'
    )
    assert snapshot(root=sandbox.root) == before


def test_sshd_drop_in_sorts_before_cloud_init() -> None:
    """sshd reads the drop-in before cloud-init's, so its values win."""
    found = re.search(
        pattern=r'/etc/ssh/sshd_config\.d/([^"\s]+\.conf)',
        string=script_text(path=BIN / 'install-files'),
    )
    assert found is not None
    assert found.group(1) == '10-paperroute.conf'
    assert found.group(1) < '50-cloud-init.conf'


def test_kms_off_comments_out_overlay_once() -> None:
    """Running the KMS_OFF expression twice comments the overlay once."""
    text = CONFIG_TXT.read_text(encoding='utf-8')
    for _ in range(2):
        text = subprocess.run(
            args=['sed', kms_off()],
            input=text,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    lines = text.splitlines()
    assert lines.count('#dtoverlay=vc4-kms-v3d') == 1
    assert 'dtoverlay=vc4-kms-v3d' not in lines


def test_kms_off_is_the_planned_expression() -> None:
    """provision-device uses exactly the planned sed expression."""
    assert kms_off() == KMS_OFF


def test_edit_config_is_idempotent(tmp_path: Path) -> None:
    """Two runs give the same file, with the lines once under [all]."""
    config = tmp_path / 'config.txt'
    shutil.copyfile(src=CONFIG_TXT, dst=config)
    edit_config(config=config)
    first = config.read_text(encoding='utf-8')
    edit_config(config=config)
    assert config.read_text(encoding='utf-8') == first
    assert first.splitlines().count('#dtoverlay=vc4-kms-v3d') == 1
    assert last_section(text=first) == [
        '[all]',
        'gpu_mem=16',
        'dtparam=act_led_trigger=timer',
        'dtparam=spi=on',
    ]


def test_edit_config_appends_all_section(tmp_path: Path) -> None:
    """A file not ending in an [all] section gets one appended."""
    config = tmp_path / 'config.txt'
    text = CONFIG_TXT.read_text(encoding='utf-8')
    config.write_text(text.removesuffix('[all]\n'), encoding='utf-8')
    edit_config(config=config)
    edit_config(config=config)
    edited = config.read_text(encoding='utf-8')
    assert edited.count('[all]') == 1
    assert last_section(text=edited)[1:] == [
        'gpu_mem=16',
        'dtparam=act_led_trigger=timer',
        'dtparam=spi=on',
    ]


def test_bin_holds_the_planned_scripts() -> None:
    """deploy/bin holds the five scripts and nothing else."""
    assert sorted(path.name for path in BIN.iterdir()) == list(BIN_SCRIPTS)


@pytest.mark.parametrize('path', SHELL_SCRIPTS, ids=lambda path: path.name)
def test_shell_script_parses(path: Path) -> None:
    """Each script is executable and passes bash -n."""
    assert os.access(path, os.X_OK)
    result = subprocess.run(
        args=['bash', '-n', str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert (result.returncode, result.stderr) == (0, '')


@pytest.mark.parametrize('path', SHELL_SCRIPTS, ids=lambda path: path.name)
def test_shell_script_never_names_the_key(path: Path) -> None:
    """No script names the key file, so none can handle or echo the key."""
    text = (
        script_text(path=path)
        .replace(SECRETS_LINE, '')
        .replace('make_secrets', '')
    )
    assert 'tfnsw_api_key' not in text
    assert 'secrets' not in text


def test_root_exec_lines_get_no_service_writable_environment() -> None:
    """Units with root + lines read env only from /etc, and set no TMPDIR."""
    for name in UNITS:
        unit = read_unit(name=name)
        service = unit.get('Service', {})
        if not any(key in service for key in ROOT_EXEC_KEYS):
            continue
        assert environment_names(unit=unit) <= ROOT_SAFE_ENVIRONMENT
        assert service.get('EnvironmentFile', '/etc/').startswith('/etc/')


def test_install_release_constants() -> None:
    """Three releases are kept, and the app gets 90 s to come up."""
    script = BIN / 'install-release'
    assert script_value(path=script, name='KEEP') == '3'
    assert script_value(path=script, name='HEALTH_WAIT') == '90'
    assert script_value(path=script, name='LOCK') == (
        '/run/paperroute-install.lock'
    )


def test_uv_installer_is_pinned() -> None:
    """provision-device fetches a versioned installer and checks its hash."""
    script = BIN / 'provision-device'
    assert script_value(path=script, name='UV_VERSION') == '0.12.1'
    assert script_value(path=script, name='UV_INSTALLER_URL') == (
        '"https://astral.sh/uv/$UV_VERSION/install.sh"'
    )
    digest = script_value(path=script, name='UV_INSTALLER_SHA256')
    assert re.fullmatch(pattern='[0-9a-f]{64}', string=digest)
    assert '| env UV_UNMANAGED_INSTALL' not in script_text(path=script)


def test_provision_retires_the_pin_file() -> None:
    """provision-device removes the old pin file and never writes one."""
    script = BIN / 'provision-device'
    text = script_text(path=script)
    assert script_value(path=script, name='PIN_DIR') == '/etc/paperroute'
    assert script_value(path=script, name='PIN_FILE') == '$PIN_DIR/m2a.env'
    assert 'PAPERROUTE_FROM' not in text
    assert 'write_pin' not in text
    assert 'rm -f -- "$PIN_FILE"' in text
    assert '[[ ! -L $PIN_DIR ]] || fail "$PIN_DIR is a symlink"' in text
    assert 'm2a.env' not in script_text(path=BIN / 'install-release')


def test_no_unit_reads_m2a_env() -> None:
    """No systemd unit loads the retired pin file."""
    for unit in sorted(SYSTEMD.iterdir()):
        text = unit.read_text(encoding='utf-8')
        assert 'EnvironmentFile' not in text, unit.name
        assert 'm2a.env' not in text, unit.name


def test_provision_retires_pin_after_installing_files() -> None:
    """The pin goes only once the new unit is installed and loaded."""
    lines = script_text(path=BIN / 'provision-device').splitlines()
    install = lines.index('/usr/local/sbin/paperroute-install-files')
    assert lines.index('retire_pin') > install


def test_provision_refuses_before_m4_saved_the_pin() -> None:
    """Provision stops before installing anything if state.json is missing."""
    lines = script_text(path=BIN / 'provision-device').splitlines()
    gate = lines.index(
        "[[ ! -e $PIN_FILE || -e /var/lib/paperroute/state.json ]] ||"
        " fail 'deploy M4 first so state.json holds the pin'"
    )
    assert gate < lines.index('copy_deploy')
    assert gate < lines.index('/usr/local/sbin/paperroute-install-files')


@pytest.mark.parametrize('mode', sorted(LED_PATTERNS))
def test_led_sets_pattern(tmp_path: Path, mode: str) -> None:
    """Each mode writes its trigger, then its delays when it blinks."""
    led = led_dir(tmp_path=tmp_path)
    result = run_script(
        name='paperroute-led',
        args=(mode,),
        env=user_env(PAPERROUTE_LED_DIR=str(led)),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert led_state(led=led) == LED_PATTERNS[mode]


def test_led_restore_without_app_blinks_boot(tmp_path: Path) -> None:
    """With the app not active, restore goes back to the boot blink."""
    led = led_dir(tmp_path=tmp_path)
    result = run_script(
        name='paperroute-led',
        args=('restore',),
        env=user_env(PAPERROUTE_LED_DIR=str(led)),
    )
    assert result.returncode == 0
    assert led_state(led=led) == LED_PATTERNS['boot']


@pytest.mark.parametrize(
    'args',
    ((), ('',), ('blink',), ('BOOT',), ('boot', 'on'), ('on;id',)),
)
def test_led_rejects_bad_arguments(
    tmp_path: Path,
    args: tuple[str, ...],
) -> None:
    """Anything but one known mode exits 2 and writes nothing."""
    led = led_dir(tmp_path=tmp_path)
    result = run_script(
        name='paperroute-led',
        args=args,
        env=user_env(PAPERROUTE_LED_DIR=str(led)),
    )
    assert (result.returncode, result.stderr) == (
        2,
        'usage: paperroute-led boot|rebuild|restore|on\n',
    )
    assert led_state(led=led) == {}


def test_led_without_led_exits_quietly(tmp_path: Path) -> None:
    """With no ACT directory the script does nothing and exits 0."""
    result = run_script(
        name='paperroute-led',
        args=('boot',),
        env=user_env(PAPERROUTE_LED_DIR=str(tmp_path / 'missing')),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert not (tmp_path / 'missing').exists()


def test_led_ignores_override_as_root() -> None:
    """The test override applies only when the caller isn't root."""
    text = script_text(path=BIN / 'paperroute-led')
    assert 'if [[ $EUID -ne 0 ]]; then\n    LED=${PAPERROUTE_LED_DIR' in text


def copy_deploy(tmp_path: Path) -> Path:
    """
    Copy deploy/ the way scripts/provision stages it.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    Path
        The copy.
    """
    copy = tmp_path / 'paperroute-provision'
    shutil.copytree(src=DEPLOY, dst=copy)
    return copy


def run_provision(
    copy: Path,
    sudo_user: str | None,
) -> subprocess.CompletedProcess[str]:
    """
    Run provision-device over a staged copy as the current user.

    Parameters
    ----------
    copy : Path
        The staged deploy/ copy.
    sudo_user : str | None
        The ``SUDO_USER`` to claim, or None to leave it unset.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    extra = {} if sudo_user is None else {'SUDO_USER': sudo_user}
    return subprocess.run(
        args=['bash', str(copy / 'bin' / 'provision-device'), str(copy)],
        env=user_env(**extra),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    ('sudo_user', 'reason'),
    (
        (None, 'run it through sudo'),
        ('nobody', 'DIR is not owned by nobody'),
        ('no-such-user-paperroute', 'unknown user'),
        (getpass.getuser(), 'checks passed; provisioning needs root'),
    ),
)
def test_provision_checks_the_staged_owner(
    tmp_path: Path,
    sudo_user: str | None,
    reason: str,
) -> None:
    """Staged files must belong to the sudo caller before root runs them."""
    copy = copy_deploy(tmp_path=tmp_path)
    before = snapshot(root=tmp_path)
    result = run_provision(copy=copy, sudo_user=sudo_user)
    assert (result.returncode, result.stderr) == (
        2,
        f'provision-device: {reason}\n',
    )
    assert snapshot(root=tmp_path) == before


def test_provision_rejects_odd_entries(tmp_path: Path) -> None:
    """A staged copy holding a symlink exits 2 before anything runs."""
    copy = copy_deploy(tmp_path=tmp_path)
    (copy / 'conf' / 'link').symlink_to('/etc/passwd')
    result = run_provision(copy=copy, sudo_user=getpass.getuser())
    assert (result.returncode, result.stderr) == (
        2,
        'provision-device: DIR may hold only regular files and'
        ' directories\n',
    )


def test_service_units_block_debug_syscalls() -> None:
    """Both paperroute services refuse ptrace and the other debug calls."""
    for name in ('paperroute.service', 'paperroute-timetable.service'):
        service = read_unit(name=name)['Service']
        assert service['SystemCallFilter'] == '~@debug'


def test_provision_runs_service_user_detached() -> None:
    """runuser in provision gets no terminal, so it can't inject input."""
    assert commands(path=BIN / 'provision-device', word='runuser') == [
        'if ! setsid -w runuser -u paperroute -- install -d -m 0750 "$1"'
        ' </dev/null >/dev/null 2>&1; then',
        f'if ! {SECRETS_LINE} </dev/null >/dev/null 2>&1; then',
    ]


def test_install_release_writes_version() -> None:
    """take_over writes the SHA to VERSION before making the tree root's."""
    text = script_text(path=BIN / 'install-release')
    body = text[text.index('take_over() {'):]
    body = body[:body.index('\n}\n')]
    line = '    printf \'%s\\n\' "$SHA" >"$INCOMING/tree/VERSION"\n'
    assert body.count(line) == 1
    changed = body.index('the staged tree changed')
    assert changed < body.index(line) < body.index('chown -hR')


def test_install_release_closes_lock_for_owner() -> None:
    """The stage owner's commands never inherit the install lock's fd."""
    found = commands(path=BIN / 'install-release', word='runuser')
    assert len(found) == 2
    assert all(command.endswith(' 9>&-') for command in found)


def test_install_release_builds_with_copied_files() -> None:
    """uv copies files, so the release shares no inode with the cache."""
    found = commands(path=BIN / 'install-release', word='uv sync')
    assert len(found) == 1
    assert ' UV_LINK_MODE=copy ' in found[0]


def test_install_release_syncs_only_the_app() -> None:
    """The release installs the app and its packages, no dependency group."""
    found = commands(path=BIN / 'install-release', word='uv sync')
    assert len(found) == 1
    words = found[0].split()
    assert {
        '--package',
        'paperroute',
        '--no-default-groups',
        '--frozen',
        '--no-editable',
    } <= set(words)
    assert words[words.index('--package') + 1] == 'paperroute'
    assert '--no-dev' not in words


def test_deploy_stages_packages() -> None:
    """Deploy copies the workspace packages along with the app."""
    found = commands(path=DEPLOY_SCRIPT, word='git ls-files')
    assert len(found) == 1
    assert 'packages' in found[0].split()


def test_install_release_starts_without_timetable() -> None:
    """The app starts and is checked whether or not a timetable exists."""
    text = script_text(path=BIN / 'install-release')
    assert 'timetable.db' not in text
    assert 'paperroute-timetable' not in text
    lines = text.splitlines()
    last = max(
        index
        for index, line in enumerate(lines)
        if 'systemctl restart paperroute' in line
    )
    assert lines[last] == (
        'if ! systemctl restart paperroute || ! healthy; then'
    )
    assert 'exit 0' not in lines[:last]


TILES_SCRIPT: Final[Path] = BIN / 'install-tiles'
DEPLOY_SCRIPT: Final[Path] = ROOT / 'scripts' / 'deploy'
TILES_ROOT_MESSAGE: Final[str] = (
    'install-tiles: checks passed; installing needs root\n'
)
TILES_NOT_A_FILE: Final[str] = (
    'install-tiles: the staged tiles are not a regular file\n'
)
TILES_DIR_NOT_REAL: Final[str] = (
    'install-tiles: the stage tiles directory is not a real directory\n'
)
DATA_PATH: Final[re.Pattern[str]] = re.compile(
    pattern=r'\$\{?(?:DATA|DEST|STAMP|tmp)\b'
)
TMP_PATH: Final[re.Pattern[str]] = re.compile(pattern=r'\$\{?tmp\b')
STAGED_PATH: Final[re.Pattern[str]] = re.compile(pattern=r'\$\{?STAGED\b')
LEADING_WORDS: Final[re.Pattern[str]] = re.compile(
    pattern=r'^(?:(?:if|then|while|!)\s+)+'
)
SERVICE_HEADS: Final[tuple[str, ...]] = (
    'as_service ',
    '[[ ',
    'DATA=${PAPERROUTE_DATA:-$DATA}',
    'DEST=$DATA/tiles.db',
    'STAMP=$DATA/tiles.stamp',
    'suffix=${tmp##*.}',
)
OWNER_HEADS: Final[tuple[str, ...]] = (
    'as_owner ',
    '[[ ',
    'find "$STAGED" -maxdepth 0 -user ',
    'STAGED=${PAPERROUTE_TILES_STAGE:-',
    'local uid found dir=${STAGED%/*}',
)


@dataclass(frozen=True)
class TilesSandbox:
    """A staged tiles file and a data directory that override the real ones."""

    root: Path
    staged: Path
    data: Path
    env: dict[str, str]


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


def stamp_of(path: Path) -> str:
    """
    Describe a file by its size and whole-second mtime.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    str
        ``'<size> <mtime>'``, as install-tiles writes its stamp.
    """
    status = path.stat()
    return f'{status.st_size} {int(status.st_mtime)}'


def command_heads(line: str, pattern: re.Pattern[str]) -> list[str]:
    """
    Find the simple command that uses each match of a pattern in a line.

    Parameters
    ----------
    line : str
        One command line, with continuations joined.
    pattern : re.Pattern[str]
        What to look for, such as a variable reference.

    Returns
    -------
    list[str]
        For each match inside a ``[[ ... ]]`` test that runs no ``$(...)``,
        ``'[[ '``. For any other, the line from the start of its simple
        command (after any pipe, list operator, ``;`` or ``$(``, and
        without a leading ``if``, ``then``, ``while`` or ``!``) to the end.
    """
    tests = [
        found.span()
        for found in re.finditer(pattern=r'\[\[ .*? \]\]', string=line)
        if '$(' not in found.group(0)
    ]
    return [
        '[[ '
        if any(start < found.start() < end for start, end in tests)
        else LEADING_WORDS.sub(
            repl='',
            string=re.split(
                pattern=r'\|\||&&|[|;(]',
                string=line[:found.start()],
            )[-1].lstrip(),
        ) + line[found.start():]
        for found in pattern.finditer(line)
    ]


def run_tiles(
    tiles: TilesSandbox,
    args: tuple[str, ...] = (),
    sudo_user: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run install-tiles in the sandbox as the current user.

    Parameters
    ----------
    tiles : TilesSandbox
        The sandbox.
    args : tuple[str, ...]
        The arguments.
    sudo_user : str | None
        The ``SUDO_USER`` to claim, or None to leave it unset.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    extra = {} if sudo_user is None else {'SUDO_USER': sudo_user}
    return run_script(
        name='install-tiles',
        args=args,
        env=tiles.env | extra,
    )


@pytest.mark.parametrize('value', ('', 'x', '--help', SHA))
def test_install_tiles_rejects_any_argument(
    tiles: TilesSandbox,
    value: str,
) -> None:
    """install-tiles takes no arguments at all."""
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles, args=(value,))
    assert (result.returncode, result.stderr) == (
        2,
        'install-tiles: takes no arguments\n',
    )
    assert snapshot(root=tiles.root) == before


def test_install_tiles_rejects_a_symlinked_stage(tiles: TilesSandbox) -> None:
    """A staged file that is a symlink exits 2 and writes nothing."""
    elsewhere = tiles.root / 'elsewhere.db'
    tiles.staged.rename(elsewhere)
    tiles.staged.symlink_to(elsewhere)
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stderr) == (2, TILES_NOT_A_FILE)
    assert snapshot(root=tiles.root) == before


def test_install_tiles_rejects_a_symlinked_stage_directory(
    tiles: TilesSandbox,
) -> None:
    """A stage tiles directory that is a symlink exits 2 before it is read."""
    linked = tiles.root / 'linked'
    linked.symlink_to(tiles.staged.parent)
    env = tiles.env | {'PAPERROUTE_TILES_STAGE': str(linked / 'tiles.db')}
    before = snapshot(root=tiles.root)
    result = run_script(name='install-tiles', args=(), env=env)
    assert (result.returncode, result.stderr) == (2, TILES_DIR_NOT_REAL)
    assert snapshot(root=tiles.root) == before


def test_install_tiles_rejects_a_missing_stage(tiles: TilesSandbox) -> None:
    """With nothing staged the script exits 2 and writes nothing."""
    tiles.staged.unlink()
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stderr) == (2, TILES_NOT_A_FILE)
    assert snapshot(root=tiles.root) == before


@pytest.mark.parametrize(
    ('sudo_user', 'reason'),
    (
        ('nobody', 'the staged tiles are not owned by nobody'),
        ('no-such-user-paperroute', 'unknown user'),
    ),
)
def test_install_tiles_checks_the_stage_owner(
    tiles: TilesSandbox,
    sudo_user: str,
    reason: str,
) -> None:
    """The staged file must belong to the sudo caller."""
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles, sudo_user=sudo_user)
    assert (result.returncode, result.stderr) == (
        2,
        f'install-tiles: {reason}\n',
    )
    assert snapshot(root=tiles.root) == before


def test_install_tiles_skips_when_stamp_matches(tiles: TilesSandbox) -> None:
    """Tiles already installed from this staged file are left alone."""
    (tiles.data / 'tiles.db').write_text('old tiles\n', encoding='utf-8')
    (tiles.data / 'tiles.stamp').write_text(
        f'{stamp_of(path=tiles.staged)}\n',
        encoding='utf-8',
    )
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        'tiles unchanged\n',
        '',
    )
    assert snapshot(root=tiles.root) == before


@pytest.mark.parametrize(
    ('dest', 'stamp'),
    ((True, '0 0'), (False, 'staged'), (True, None), (False, None)),
)
def test_install_tiles_needs_root_when_changed(
    tiles: TilesSandbox,
    dest: bool,
    stamp: str | None,
) -> None:
    """A changed or never-installed file passes the checks, then needs root."""
    if dest:
        (tiles.data / 'tiles.db').write_text('old tiles\n', encoding='utf-8')
    if stamp is not None:
        (tiles.data / 'tiles.stamp').write_text(
            stamp.replace('staged', stamp_of(path=tiles.staged)),
            encoding='utf-8',
        )
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stderr) == (2, TILES_ROOT_MESSAGE)
    assert snapshot(root=tiles.root) == before


def test_install_tiles_checks_free_disk_before_copying(
    tiles: TilesSandbox,
) -> None:
    """Too little free disk exits 1 before anything is written."""
    (tiles.data / 'tiles.db').write_text('old tiles\n', encoding='utf-8')
    (tiles.data / 'tiles.stamp').write_text('0 0\n', encoding='utf-8')
    before = snapshot(root=tiles.root)
    result = run_tiles(
        tiles=TilesSandbox(
            root=tiles.root,
            staged=tiles.staged,
            data=tiles.data,
            env=tiles.env | {'PAPERROUTE_FREE_BYTES': '1'},
        ),
    )
    assert result.returncode == 1
    assert 'not enough free disk' in result.stderr
    assert list(tiles.data.glob('tiles.db.*')) == []
    assert snapshot(root=tiles.root) == before


def test_install_tiles_free_check_runs_df_as_service() -> None:
    """df runs only as the service user, before root's first write."""
    text = script_text(path=TILES_SCRIPT)
    found = commands(path=TILES_SCRIPT, word='df')
    assert len(found) == 1
    assert 'as_service df ' in found[0]
    assert text.index('as_service df ') < text.index('installing needs root')
    assert text.index('as_service df ') < text.index('mktemp')
    assert text.index('installing needs root') < text.index('mktemp')


def test_install_tiles_ignores_free_override_as_root() -> None:
    """The free-space override applies only when the caller isn't root."""
    assert (
        "if [[ $EUID -ne 0 && -n ${PAPERROUTE_FREE_BYTES:-} ]]; then\n"
        '    free=$PAPERROUTE_FREE_BYTES\n'
    ) in script_text(path=TILES_SCRIPT)
    assert len(commands(path=TILES_SCRIPT, word='PAPERROUTE_FREE_BYTES')) == 2


def test_install_tiles_rejects_a_non_numeric_free_override(
    tiles: TilesSandbox,
) -> None:
    """A free-space override that is not a plain number is refused."""
    result = run_tiles(
        tiles=TilesSandbox(
            root=tiles.root,
            staged=tiles.staged,
            data=tiles.data,
            env=tiles.env | {'PAPERROUTE_FREE_BYTES': '1+1'},
        ),
    )
    assert result.returncode == 1
    assert 'cannot read the free disk space' in result.stderr


def test_deploy_checks_free_disk_before_tile_upload() -> None:
    """scripts/deploy reads the Pi's free space before the tile rsync."""
    text = DEPLOY_SCRIPT.read_text(encoding='utf-8')
    rsync = text.index('rsync -a --partial data/tiles.db')
    assert 0 < text.index('df -Pk') < rsync
    assert text.count('^(0|[1-9][0-9]*)$') >= 1
    assert (
        'for value in "$free" "$staged_size" "$staged_mtime" "$size" '
        '"$mtime"; do\n        [[ $value =~ $NUMBER ]]'
    ) in text
    assert text.index('=~ $NUMBER') < rsync
    assert 'not enough free disk on the Pi for the tiles' in text


def test_deploy_only_warns_on_tiles() -> None:
    """scripts/deploy checks the tiles before any ssh and never stops."""
    text = DEPLOY_SCRIPT.read_text(encoding='utf-8')
    lines = text.splitlines()
    check = next(
        index
        for index, line in enumerate(lines)
        if 'check-tiles' in line
    )
    first_ssh = next(
        index
        for index, line in enumerate(lines)
        if re.search(r'\bssh\b', line) and not line.lstrip().startswith('#')
    )
    assert check < first_ssh
    assert lines[check].strip() == (
        'uv run python -m paperroute.build check-tiles data/tiles.db '
        "|| printf 'deploy: warning: tiles do not match this release; "
        "deploying anyway\\n' >&2"
    )
    assert 'exit' not in lines[check]
    assert lines[check - 1].strip() == 'if [[ -f data/tiles.db ]]; then'
    assert lines[check + 1].strip() == 'fi'


def test_deploy_reports_a_failed_tile_size_read() -> None:
    """An ssh failure reading sizes reaches the number check's message."""
    text = DEPLOY_SCRIPT.read_text(encoding='utf-8')
    assert "{ print $4 }') \\\n        || free=\n" in text
    assert "|| echo '0 0'\") \\\n        || remote=\n" in text
    assert text.index('|| remote=') < text.index('=~ $NUMBER')


def test_install_tiles_ignores_overrides_as_root() -> None:
    """The test overrides apply only when the caller isn't root."""
    assert (
        'if [[ $EUID -ne 0 ]]; then\n'
        '    STAGED=${PAPERROUTE_TILES_STAGE:-$STAGED}\n'
        '    DATA=${PAPERROUTE_DATA:-$DATA}\n'
        'fi\n'
        'DEST=$DATA/tiles.db\n'
        'STAMP=$DATA/tiles.stamp\n'
    ) in script_text(path=TILES_SCRIPT)


def test_install_tiles_constants() -> None:
    """The real paths, and the lock install-release takes."""
    values = {
        name: script_value(path=TILES_SCRIPT, name=name)
        for name in ('STAGED', 'DATA', 'LOCK')
    }
    assert values == {
        'STAGED': '/var/cache/paperroute-stage/tiles/tiles.db',
        'DATA': '/var/lib/paperroute',
        'LOCK': script_value(path=BIN / 'install-release', name='LOCK'),
    }


def test_install_tiles_touches_data_only_as_the_service_user() -> None:
    """Root never opens a data path, and reads the stage only as its owner."""
    data_heads = [
        head
        for line in commands(
            path=TILES_SCRIPT,
            word='(?:DATA|DEST|STAMP|tmp)',
        )
        for head in command_heads(line=line, pattern=DATA_PATH)
    ]
    owner_heads = [
        head
        for line in commands(path=TILES_SCRIPT, word='STAGED')
        for head in command_heads(line=line, pattern=STAGED_PATH)
    ]
    assert len(data_heads) >= 8
    assert all(head.startswith(SERVICE_HEADS) for head in data_heads)
    assert any(head.startswith('as_owner cat ') for head in owner_heads)
    assert all(head.startswith(OWNER_HEADS) for head in owner_heads)
    assert not any(
        re.search(pattern=r'(?:^|[;&|(]) *install ', string=line)
        for line in commands(path=TILES_SCRIPT, word='install')
    )
    assert [
        'as_service sqlite3 ' in line
        for line in commands(path=TILES_SCRIPT, word='sqlite3')
    ] == [True]


def test_install_tiles_runs_users_detached_without_the_lock() -> None:
    """Every runuser has no terminal, no stdin and no lock fd."""
    assert commands(path=TILES_SCRIPT, word='runuser') == [
        'setsid -w runuser -u "$OWNER" -- "$@" 2>/dev/null 9>&-',
        'setsid -w runuser -u paperroute -- "$@" 2>/dev/null 9>&-',
    ]
    assert '\nexec </dev/null\n' in script_text(path=TILES_SCRIPT)


def test_install_tiles_keeps_user_output_off_the_terminal() -> None:
    """Every owner or service command's stdout is captured or discarded."""
    found = [
        line
        for line in commands(path=TILES_SCRIPT, word='as_(?:owner|service)')
        if not re.fullmatch(pattern=r'as_\w+\(\) \{', string=line)
    ]
    assert len(found) >= 10
    assert all('$(' in line or '>/dev/null' in line for line in found)


def test_install_tiles_ends_options_before_the_temp_file() -> None:
    """
    The service-chosen temp path follows ``--`` or ``sh -c``.

    sqlite3 takes the path without ``--``; the script has already checked
    that it is an absolute path under the data directory.
    """
    prefixes = [
        head.split('$tmp')[0]
        for line in commands(path=TILES_SCRIPT, word='tmp')
        for head in command_heads(line=line, pattern=TMP_PATH)
        if head.startswith('as_service ')
    ]
    assert len(prefixes) >= 5
    assert 'as_service sqlite3 -readonly "' in prefixes
    assert all(
        ' -- ' in prefix
        or prefix.startswith('as_service sh -c ')
        or prefix == 'as_service sqlite3 -readonly "'
        for prefix in prefixes
    )


def test_install_tiles_locks_cleans_up_and_renames() -> None:
    """The install holds the lock, removes its temp file, and renames."""
    text = script_text(path=TILES_SCRIPT)
    assert 'exec 9>"$LOCK"\nif ! flock -n 9; then\n' in text
    assert 'trap cleanup EXIT\n' in text
    assert '        if ! as_service rm -f -- "$tmp" >/dev/null; then\n' in text


def test_install_tiles_fails_loudly_on_a_directory_at_dest() -> None:
    """mv -T refuses to move the tiles into a directory, and that aborts."""
    text = script_text(path=TILES_SCRIPT)
    rename = (
        'as_service mv -T -f -- "$tmp" "$DEST" >/dev/null \\\n'
        "    || abort 'could not replace the tiles; is tiles.db a directory?'"
    )
    abort = (
        "abort() {\n    printf 'install-tiles: %s\\n' \"$1\" >&2\n"
        '    exit 1\n}\n'
    )
    assert text.startswith('#!/bin/bash\n')
    assert '\nset -euo pipefail\n' in text
    assert abort in text
    assert rename in text
    assert text.index(rename) < text.index("printf 'tiles installed")


def test_install_tiles_stamps_portably() -> None:
    """The stamp is size and mtime, from GNU stat or the BSD fallback."""
    found = commands(path=TILES_SCRIPT, word='stat')
    assert found == [
        "stamp=$(as_owner stat -c '%s %Y' -- \"$STAGED\" 2>/dev/null"
        " || as_owner stat -f '%z %m' -- \"$STAGED\")"
        " || fail 'cannot read the staged tiles'",
    ]


def test_provision_installs_install_tiles() -> None:
    """provision needs the script and installs it next to the other two."""
    assert commands(path=BIN / 'provision-device', word='install-tiles') == [
        'for need in bin/install-files bin/install-release'
        ' bin/install-tiles bin/paperroute-led sudoers/paperroute-deploy'
        ' polkit/50-paperroute-power.rules'
        ' polkit/51-paperroute-network.rules; do',
        'install -m 0755 -o root -g root "$SHARE/deploy/bin/install-tiles"'
        ' /usr/local/sbin/paperroute-install-tiles',
    ]


def test_deploy_installs_tiles_before_the_release() -> None:
    """A failed tiles install stops the deploy before install-release."""
    text = script_text(path=DEPLOY_SCRIPT)
    assert commands(path=DEPLOY_SCRIPT, word='TILES') == [
        'TILES=/usr/local/sbin/paperroute-install-tiles',
        'if ! ssh -- "$host" sudo -n -l "$TILES" >/dev/null 2>&1; then',
        'ssh -- "$host" sudo -n "$TILES" || exit',
    ]
    order = [
        text.index(part)
        for part in (
            '| rsync -a --from0',
            'if [[ -f data/tiles.db ]]; then\n    if ! ssh',
            "printf 'deploy: run scripts/provision first (tiles)\\n' >&2",
            'ssh -- "$host" mkdir -p "$STAGE/tiles"',
            'rsync -a --partial data/tiles.db "$host:$STAGE/tiles/tiles.db"',
            'sudo -n "$TILES" || exit',
            'sudo -n "$INSTALL" "$sha"',
        )
    ]
    assert order == sorted(order)


POLKIT_RULE: Final[Path] = DEPLOY / 'polkit' / '50-paperroute-power.rules'
POWER_ACTIONS: Final[frozenset[str]] = frozenset({
    'org.freedesktop.login1.power-off',
    'org.freedesktop.login1.power-off-multiple-sessions',
    'org.freedesktop.login1.reboot',
    'org.freedesktop.login1.reboot-multiple-sessions',
})


def rule_code(*, path: Path) -> str:
    """
    Read a polkit rule without its comment lines.

    Parameters
    ----------
    path : Path
        Rule file.

    Returns
    -------
    str
        The code lines.
    """
    text = script_text(path=path)
    return '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('//')
    )


def assert_rule_allows_only(*, path: Path, actions: frozenset[str]) -> None:
    """
    Check a rule says YES to exactly these actions for paperroute only.

    Parameters
    ----------
    path : Path
        Rule file.
    actions : frozenset[str]
        The action IDs it may allow.
    """
    code = rule_code(path=path)
    compared = re.findall(pattern=r'action\.id == "([^"]+)"', string=code)
    named = re.findall(pattern=r'org\.freedesktop\.[\w.-]+', string=code)
    assert sorted(compared) == sorted(actions)
    assert set(named) == actions
    assert 'subject.user != "paperroute"' in code
    assert code.count('polkit.Result.YES') == 1
    assert code.count('polkit.Result') == 1
    for banned in ('indexOf', 'startsWith', 'match(', 'test(', 'RegExp'):
        assert banned not in code
    assert re.findall(pattern=r'action\.id\s*[^=\s]', string=code) == []


def test_polkit_rule_allows_only_power() -> None:
    """The rule says YES to the four power actions for paperroute only."""
    assert_rule_allows_only(path=POLKIT_RULE, actions=POWER_ACTIONS)


NETWORK_RULE: Final[Path] = DEPLOY / 'polkit' / '51-paperroute-network.rules'
NETWORK_ACTIONS: Final[frozenset[str]] = frozenset({
    'org.freedesktop.NetworkManager.network-control',
    'org.freedesktop.NetworkManager.settings.modify.system',
    'org.freedesktop.NetworkManager.wifi.scan',
    'org.freedesktop.NetworkManager.enable-disable-wifi',
    'org.freedesktop.NetworkManager.wifi.share.protected',
})


def test_network_polkit_rule_allows_only_nm_actions() -> None:
    """The rule says YES to five NetworkManager actions for paperroute."""
    assert_rule_allows_only(path=NETWORK_RULE, actions=NETWORK_ACTIONS)
    assert script_text(path=NETWORK_RULE).startswith(
        '// Installed as /etc/polkit-1/rules.d/51-paperroute-network.rules',
    )


def test_install_files_installs_polkit_rules() -> None:
    """Both rule files are installed root-owned, power first."""
    assert commands(path=BIN / 'install-files', word='polkit') == [
        'install -D -m 0644 -o root -g root'
        ' "$SRC/polkit/50-paperroute-power.rules"'
        ' /etc/polkit-1/rules.d/50-paperroute-power.rules',
        'install -D -m 0644 -o root -g root'
        ' "$SRC/polkit/51-paperroute-network.rules"'
        ' /etc/polkit-1/rules.d/51-paperroute-network.rules',
    ]


def test_provision_installs_polkitd() -> None:
    """provision installs polkitd and needs the rule file."""
    installs = commands(path=BIN / 'provision-device', word='apt-get')
    assert 'polkitd' in installs[-1].split()
    needed = commands(path=BIN / 'provision-device', word='polkit')
    assert any(
        line.startswith('for need in') and line.endswith(
            ' polkit/51-paperroute-network.rules; do',
        )
        for line in needed
    )


@pytest.mark.parametrize(
    'name', ['paperroute.service', 'paperroute-timetable.service'],
)
def test_units_point_fontconfig_at_the_shipped_file(name: str) -> None:
    """Both Python services read the fonts.conf the release ships."""
    environment = read_unit(name=name)['Service']['Environment'].split()
    assert f'FONTCONFIG_FILE=/opt/paperroute/current/{FONTS_CONF}' in (
        environment
    )


def test_shipped_fonts_conf_is_minimal() -> None:
    """
    fonts.conf includes no system config and names no font folders.

    Skia's own fontconfig is older than the system's, so it must not read
    the system files.
    """
    path = ROOT / FONTS_CONF
    tree = ElementTree.parse(path)
    root = tree.getroot()
    assert root.tag == 'fontconfig'
    assert not list(root.iter('include'))
    assert not list(root.iter('reset-dirs'))
    assert not list(root.iter('dir'))
    assert FONTS_CONF in subprocess.run(
        ['git', 'ls-files', FONTS_CONF],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_journald_keeps_volatile_storage_with_room_for_a_week() -> None:
    """The journal stays in RAM, sparing the SD card, capped at 24 MB."""
    parser = UnitParser(interpolation=None, delimiters=('=',))
    parser.read(filenames=DEPLOY / 'conf' / 'journald.conf', encoding='utf-8')
    found = {
        section: dict(parser[section]) for section in parser.sections()
    }
    assert found == JOURNALD


def test_provision_installs_fontconfig_config() -> None:
    """provision installs fonts.conf, which Skia's font manager reads."""
    installs = commands(path=BIN / 'provision-device', word='apt-get')
    assert 'fontconfig-config' in installs[-1].split()


def test_provision_requires_network_manager_running() -> None:
    """provision starts NetworkManager and waits up to 10 s for it."""
    lines = [
        line.strip()
        for line in script_text(path=BIN / 'provision-device').splitlines()
    ]
    install = lines.index('network-manager dnsmasq-base')
    assert lines[install + 1:install + 7] == [
        'systemctl start NetworkManager',
        'for _ in {1..10}; do',
        'systemctl is-active --quiet NetworkManager && break',
        'sleep 1',
        'done',
        'systemctl is-active --quiet NetworkManager'
        " || fail 'NetworkManager is not running'",
    ]


def test_provision_installs_network_manager() -> None:
    """provision installs NetworkManager and needs the network rule."""
    installs = commands(path=BIN / 'provision-device', word='apt-get')
    words = installs[-1].split()
    assert {'network-manager', 'dnsmasq-base', 'polkitd'} <= set(words)
    needed = commands(path=BIN / 'provision-device', word='polkit')
    assert any(
        line.startswith('for need in') and line.endswith(
            ' polkit/50-paperroute-power.rules'
            ' polkit/51-paperroute-network.rules; do',
        )
        for line in needed
    )


def test_service_keeps_no_new_privileges() -> None:
    """Power goes through polkit, so the unit still forbids new privileges."""
    service = read_unit(name='paperroute.service')['Service']
    assert service['NoNewPrivileges'] == 'yes'


def test_provision_creates_empty_secrets() -> None:
    """The service user makes an empty private secrets file, keeping a key."""
    lines = script_text(path=BIN / 'provision-device').splitlines()
    start = lines.index('make_secrets() {')
    body = lines[start:lines.index('}', start)]
    assert any(SECRETS_LINE in line for line in body)
    log = lines.index('make_service_dir /var/lib/paperroute/log')
    assert lines[log + 1] == 'make_secrets'


def test_shipped_screens_are_panel_frames() -> None:
    """Both pictures load and hold only the panel's four greys."""
    for name in ('off.raw', 'on.raw'):
        frame = load_frame(path=DEPLOY / 'screens' / name)
        assert set(np.unique(frame)) <= {0, 85, 170, 255}


@pytest.mark.parametrize(
    ('action', 'drawn'),
    [('poweroff', True), ('halt', True), ('reboot', False), ('kexec', False)],
)
def test_off_hook_draws_on_poweroff_only(
    tmp_path: Path,
    action: str,
    drawn: bool,
) -> None:
    """The hook draws the off screen for poweroff and halt, and exits 0."""
    record = tmp_path / 'args'
    stub = tmp_path / 'timeout'
    stub.write_text(
        f'#!/bin/sh\necho "$@" >{record}\nexit 1\n',
        encoding='utf-8',
    )
    stub.chmod(0o755)
    result = subprocess.run(
        args=['sh', str(OFF_HOOK), action],
        env=user_env(
            PAPERROUTE_TIMEOUT=str(stub),
            PAPERROUTE_PYTHON='/py',
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert record.exists() is drawn
    if drawn:
        assert record.read_text(encoding='utf-8').split() == [
            '20',
            '/py',
            '-m',
            'paperroute_device.screen',
            '--image',
            '/usr/local/share/paperroute/deploy/screens/off.raw',
            '--state',
            '/var/lib/paperroute/state.json',
        ]


def test_install_files_installs_hook_and_enables_on_screen() -> None:
    """The hook is installed 0755 and the boot screen unit is enabled."""
    path = BIN / 'install-files'
    assert commands(path=path, word='system-shutdown') == [
        'install -D -m 0755 -o root -g root'
        ' "$SRC/system-shutdown/paperroute-off-screen"'
        ' /usr/lib/systemd/system-shutdown/paperroute-off-screen',
    ]
    assert commands(path=path, word='enable') == [
        'systemctl enable paperroute-led-boot paperroute'
        ' paperroute-timetable.timer paperroute-on-screen',
    ]
