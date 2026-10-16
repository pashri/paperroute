"""Tests for the device files and the provision and deploy scripts."""

import configparser
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

ROOT: Final[Path] = Path(__file__).parents[1]
DEPLOY: Final[Path] = ROOT / 'deploy'
BIN: Final[Path] = DEPLOY / 'bin'
SYSTEMD: Final[Path] = DEPLOY / 'systemd'
CONFIG_TXT: Final[Path] = Path(__file__).parent / 'fixtures' / 'config.txt'
BIN_SCRIPTS: Final[tuple[str, ...]] = (
    'install-files',
    'install-release',
    'paperroute-led',
    'provision-device',
)
SHELL_SCRIPTS: Final[tuple[Path, ...]] = (
    ROOT / 'scripts' / 'provision',
    ROOT / 'scripts' / 'deploy',
    *(BIN / name for name in BIN_SCRIPTS),
)
SUDOERS_RULE: Final[str] = (
    'pashri ALL=(root) NOPASSWD: '
    '/usr/local/sbin/paperroute-install-release, '
    '/usr/local/sbin/paperroute-install-files'
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
PYTHON: Final[str] = '/opt/paperroute/current/.venv/bin/python'
APP_SERVICE: Final[dict[str, str]] = {
    'Type': 'notify',
    'NotifyAccess': 'main',
    'User': 'paperroute',
    'Group': 'paperroute',
    'EnvironmentFile': '/var/lib/paperroute/m2a.env',
    'Environment': (
        'PAPERROUTE_API_KEY_FILE=/var/lib/paperroute/secrets'
    ),
    'ExecStart': (
        f'{PYTHON} -m paperroute run --display web'
        ' --timetable /var/lib/paperroute/timetable.db'
        ' --error-log /var/lib/paperroute/log/errors.jsonl'
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
}
TIMETABLE_SERVICE: Final[dict[str, str]] = {
    'Type': 'oneshot',
    'User': 'paperroute',
    'Group': 'paperroute',
    'Environment': (
        'PAPERROUTE_API_KEY_FILE=/var/lib/paperroute/secrets'
        ' TMPDIR=/var/lib/paperroute/work'
    ),
    'ExecStartPre': '+/usr/local/bin/paperroute-led rebuild',
    'ExecStart': (
        f'{PYTHON} -m paperroute.build refresh'
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
}
UNITS: Final[dict[str, dict[str, dict[str, str]]]] = {
    'paperroute.service': {
        'Unit': {
            'Description': 'paperroute',
            'After': 'network-online.target time-sync.target',
            'Wants': 'network-online.target time-sync.target',
            'ConditionPathExists': '/var/lib/paperroute/timetable.db',
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
    *args: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """
    Run a script from ``deploy/bin`` with bash as the current user.

    Parameters
    ----------
    name : str
        The script's file name.
    *args : str
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


def snapshot(root: Path) -> dict[str, str]:
    """
    Describe every path under a directory, without following links.

    Parameters
    ----------
    root : Path
        The directory.

    Returns
    -------
    dict[str, str]
        Each relative path mapped to its link target, its text,
        ``'dir'`` or ``'other'``.
    """
    return {
        str(path.relative_to(root)): (
            f'link:{os.readlink(path)}'
            if path.is_symlink()
            else 'dir' if path.is_dir()
            else path.read_text() if path.is_file()
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
    env = {
        key: value
        for key, value in os.environ.items()
        if key != 'SUDO_USER'
    }
    env |= {
        'PAPERROUTE_STAGE': str(stage),
        'PAPERROUTE_RELEASES': str(releases),
    }
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
    (tree / 'module.py').write_text('x = 1\n')
    (stage / SHA / 'pyproject.toml').write_text('[project]\n')
    return stage / SHA


@pytest.mark.parametrize('name', sorted(UNITS))
def test_unit_has_exact_values(name: str) -> None:
    """Each unit holds exactly the planned sections, keys and values."""
    assert read_unit(name=name) == UNITS[name]


def test_units_are_the_planned_set() -> None:
    """deploy/systemd holds the four units and nothing else."""
    assert sorted(path.name for path in SYSTEMD.iterdir()) == sorted(UNITS)


def test_app_unit_is_notify_with_watchdog() -> None:
    """The app reports readiness and is restarted when it stops pinging."""
    service = read_unit(name='paperroute.service')['Service']
    assert (service['Type'], service['WatchdogSec']) == ('notify', '30')
    assert service['AmbientCapabilities'] == 'CAP_NET_BIND_SERVICE'
    assert service['CapabilityBoundingSet'] == 'CAP_NET_BIND_SERVICE'


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
    assert 'TMPDIR=/var/lib/paperroute/work' in service['Environment']
    assert (service['Nice'], service['OOMScoreAdjust']) == ('19', '500')


def test_sudoers_allows_exactly_the_two_scripts(tmp_path: Path) -> None:
    """The sudoers file holds one rule for the two root scripts."""
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
    result = run_script('install-release', value, env=sandbox.env)
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
    result = run_script('install-files', value, env=sandbox.env)
    assert (result.returncode, result.stderr) == (
        2,
        'install-files: takes no arguments\n',
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_extra_arguments(sandbox: Sandbox) -> None:
    """install-release takes exactly one argument."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script('install-release', SHA, SHA, env=sandbox.env)
    assert result.returncode == 2
    assert result.stderr == 'install-release: usage: install-release SHA\n'
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_symlinked_stage(sandbox: Sandbox) -> None:
    """A staged tree that is a symlink exits 2."""
    elsewhere = stage_tree(stage=sandbox.root / 'elsewhere')
    (sandbox.stage / SHA).symlink_to(elsewhere)
    before = snapshot(root=sandbox.root)
    result = run_script('install-release', SHA, env=sandbox.env)
    assert result.returncode == 2
    assert result.stderr == (
        'install-release: the staged tree is not a real directory\n'
    )
    assert snapshot(root=sandbox.root) == before


def test_install_release_rejects_missing_stage(sandbox: Sandbox) -> None:
    """A sha with nothing staged exits 2."""
    before = snapshot(root=sandbox.root)
    result = run_script('install-release', SHA, env=sandbox.env)
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
    result = run_script('install-release', SHA, env=sandbox.env)
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
    result = run_script('install-release', SHA, env=env)
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
    result = run_script('install-release', SHA, env=env)
    assert result.returncode == 2
    assert result.stderr == 'install-release: unknown user\n'
    assert snapshot(root=sandbox.root) == before


def test_install_release_valid_stage_needs_root(sandbox: Sandbox) -> None:
    """A valid stage passes every check, then stops for want of root."""
    stage_tree(stage=sandbox.stage)
    before = snapshot(root=sandbox.root)
    result = run_script('install-release', SHA, env=sandbox.env)
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
    """deploy/bin holds the four scripts and nothing else."""
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
    text = script_text(path=path)
    assert 'tfnsw_api_key' not in text
    assert 'secrets' not in text
