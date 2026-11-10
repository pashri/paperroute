"""Tests for the device files and the provision and deploy scripts."""

import configparser
import getpass
import os
import re
import shlex
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
SYDNEY_DEPLOY: Final[Path] = ROOT / 'apps' / 'paperroute-sydney' / 'deploy'
SYDNEY_SYSTEMD: Final[Path] = SYDNEY_DEPLOY / 'systemd'
APP_CONF: Final[Path] = SYDNEY_SYSTEMD / 'paperroute.service.d' / 'app.conf'
FIXTURES: Final[Path] = Path(__file__).parent / 'fixtures'
WITH_UNITS: Final[Path] = FIXTURES / 'app_with_units'
WITHOUT_UNITS: Final[Path] = FIXTURES / 'app_without_units'
CONFIG_TXT: Final[Path] = FIXTURES / 'config.txt'
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
    ROOT / 'scripts' / 'golden-linux',
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
APP_NAME: Final[str] = 'sydney'
BAD_APPS: Final[tuple[str, ...]] = ('', 'Sydney', '../x', 'syd ney')
USAGE: Final[str] = 'install-release: usage: install-release SHA APP\n'
ZONE_RE: Final[str] = "ZONE_RE='^[A-Za-z]+(/[A-Za-z0-9_+-]+)*$'"
SWITCH_STUB: Final[str] = 'switch_to() { ln -sfn -- "$1" "$CURRENT"; }'
# BSD mv has no -T.
MV_STUB: Final[str] = 'mv() { [[ $1 == -T ]] && shift; command mv "$@"; }'
CITY_NAMES: Final[re.Pattern[str]] = re.compile(
    pattern=r'(?i)sydney|tfnsw|\bnsw',
)
APP_WORDS: Final[re.Pattern[str]] = re.compile(
    pattern=r'(?i)\b(timetable|tfnsw|sydney)\b',
)
KMS_OFF: Final[str] = 's/^dtoverlay=vc4-kms-v3d/#&/'
ROOT_SAFE_ENVIRONMENT: Final[frozenset[str]] = frozenset(
    {
        'PAPERROUTE_APP_ARGS',
        'PAPERROUTE_KEY_FILE_TFNSW',
        'PAPERROUTE_API_KEY_FILE',
        'FONTCONFIG_FILE',
    }
)
LED_PATTERNS: Final[dict[str, dict[str, str]]] = {
    'boot': {'trigger': 'timer', 'delay_on': '100', 'delay_off': '100'},
    'busy': {
        'trigger': 'timer',
        'delay_on': '1000',
        'delay_off': '1000',
    },
    'on': {'trigger': 'default-on'},
}
LED_USAGE: Final[str] = 'usage: paperroute-led boot|busy|restore|on\n'
VENV_BIN: Final[str] = '/opt/paperroute/current/.venv/bin'
FONTS_CONF: Final[str] = (
    'packages/paperroute-device/src/paperroute_device/fontconfig/fonts.conf'
)
FONTS_ENVIRONMENT: Final[str] = (
    f'FONTCONFIG_FILE=/opt/paperroute/current/{FONTS_CONF}'
)
SECRETS: Final[str] = '/var/lib/paperroute/secrets'
TIMETABLE_ENVIRONMENT: Final[str] = (
    f'PAPERROUTE_KEY_FILE_TFNSW={SECRETS}'
    f' PAPERROUTE_API_KEY_FILE={SECRETS}'
    f' {FONTS_ENVIRONMENT}'
)
APP_ARGS: Final[tuple[str, ...]] = (
    '--timetable',
    '/var/lib/paperroute/timetable.db',
    '--tiles',
    '/var/lib/paperroute/tiles.db',
    '--rebuild',
)
APP_CONF_ENVIRONMENT: Final[dict[str, str]] = {
    'PAPERROUTE_APP_ARGS': ' '.join(APP_ARGS),
    'PAPERROUTE_KEY_FILE_TFNSW': SECRETS,
    'PAPERROUTE_API_KEY_FILE': SECRETS,
}
JOURNALD: Final[dict[str, dict[str, str]]] = {
    'Journal': {'Storage': 'volatile', 'RuntimeMaxUse': '24M'},
}
APP_SERVICE: Final[dict[str, str]] = {
    'Type': 'notify',
    'NotifyAccess': 'main',
    'User': 'paperroute',
    'Group': 'paperroute',
    'SupplementaryGroups': 'spi gpio',
    'Environment': FONTS_ENVIRONMENT,
    'ExecStart': (
        f'{VENV_BIN}/paperroute-board run --display web --panel --knobs'
        ' --state /var/lib/paperroute/state.json'
        ' --error-log /var/lib/paperroute/log/errors.jsonl'
        ' --power --wifi'
        ' --version-file /opt/paperroute/current/VERSION'
        ' $PAPERROUTE_APP_ARGS'
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
    'Environment': TIMETABLE_ENVIRONMENT,
    'ExecStartPre': '+/usr/local/bin/paperroute-led busy',
    'ExecStart': (
        '/usr/bin/env TMPDIR=/var/lib/paperroute/work'
        f' {VENV_BIN}/paperroute-build refresh'
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
KIT_UNITS: Final[dict[str, dict[str, dict[str, str]]]] = {
    'paperroute.service': {
        'Unit': {
            'Description': 'paperroute',
        },
        'Service': APP_SERVICE,
        'Install': {'WantedBy': 'multi-user.target'},
    },
    'paperroute-on-screen.service': {
        'Unit': {
            'ConditionPathExists': '/dev/spidev0.0',
            'StartLimitIntervalSec': '60',
            'StartLimitBurst': '4',
        },
        'Service': {
            'Type': 'oneshot',
            'User': 'paperroute',
            'Group': 'paperroute',
            'SupplementaryGroups': 'spi gpio',
            'NoNewPrivileges': 'yes',
            'TimeoutStartSec': '30',
            'Restart': 'on-failure',
            'RestartSec': '3',
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
SYDNEY_UNITS: Final[dict[str, dict[str, dict[str, str]]]] = {
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
            'OnCalendar': '*-*-* 03:30:00',
            'Persistent': 'true',
            'RandomizedDelaySec': '0',
        },
        'Install': {'WantedBy': 'timers.target'},
    },
}
UNITS: Final[dict[str, dict[str, dict[str, str]]]] = (
    KIT_UNITS | SYDNEY_UNITS
)
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
        The unit's file name under ``deploy/systemd``, or under Sydney's
        ``deploy/systemd`` for one of its units.

    Returns
    -------
    dict[str, dict[str, str]]
        Each section's keys and values.
    """
    folder = SYSTEMD if name in KIT_UNITS else SYDNEY_SYSTEMD
    parser = UnitParser(
        strict=False,
        interpolation=None,
        delimiters=('=',),
    )
    parser.read(filenames=folder / name, encoding='utf-8')
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
    assignments = shlex.split(unit['Service'].get('Environment', ''))
    return {assignment.split('=')[0] for assignment in assignments}


def drop_in_environment(*, path: Path) -> dict[str, str]:
    """
    Read every ``Environment=`` line of a drop-in as systemd does.

    systemd splits each value on unquoted whitespace, and double quotes
    keep an assignment whole.

    Parameters
    ----------
    path : Path
        The drop-in.

    Returns
    -------
    dict[str, str]
        Each variable and its value.
    """
    values = [
        line.removeprefix('Environment=')
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.startswith('Environment=')
    ]
    return dict(
        assignment.split('=', 1)
        for value in values
        for assignment in shlex.split(value)
    )


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
    app = stage / SHA / 'apps' / f'paperroute-{APP_NAME}'
    app.mkdir(parents=True)
    (app / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
    return stage / SHA


@pytest.mark.parametrize('name', sorted(UNITS))
def test_unit_has_exact_values(name: str) -> None:
    """Each unit holds exactly the planned sections, keys and values."""
    assert read_unit(name=name) == UNITS[name]


def test_units_are_the_planned_set() -> None:
    """deploy/systemd holds the kit's units and nothing else."""
    assert sorted(path.name for path in SYSTEMD.iterdir()) == sorted(
        KIT_UNITS,
    )


def test_app_units_are_the_planned_set() -> None:
    """Sydney's deploy/systemd holds its two units and the board drop-in."""
    assert sorted(
        str(path.relative_to(SYDNEY_SYSTEMD))
        for path in SYDNEY_SYSTEMD.rglob('*')
        if path.is_file()
    ) == sorted([*SYDNEY_UNITS, 'paperroute.service.d/app.conf'])


def test_kit_deploy_names_no_app() -> None:
    """The kit's units and root scripts name no timetable, city or agency."""
    paths = [
        *sorted(SYSTEMD.iterdir()),
        *(BIN / name for name in ('install-files', 'install-release')),
        BIN / 'paperroute-led',
        ROOT / 'scripts' / 'provision',
    ]
    named = [
        str(path.relative_to(ROOT))
        for path in paths
        if APP_WORDS.search(script_text(path=path))
    ]
    assert len(paths) == 7
    assert not named


def test_app_conf_environment_parses_as_systemd_does() -> None:
    """The quoted assignment stays whole, then splits into five words."""
    assert drop_in_environment(path=APP_CONF) == APP_CONF_ENVIRONMENT
    words = APP_CONF_ENVIRONMENT['PAPERROUTE_APP_ARGS'].split()
    assert tuple(words) == APP_ARGS
    assert [
        line
        for line in APP_CONF.read_text(encoding='utf-8').splitlines()
        if line and not line.startswith('#')
    ] == [
        '[Service]',
        'Environment="PAPERROUTE_APP_ARGS='
        f'{APP_CONF_ENVIRONMENT["PAPERROUTE_APP_ARGS"]}"',
        f'Environment=PAPERROUTE_KEY_FILE_TFNSW={SECRETS}',
        f'Environment=PAPERROUTE_API_KEY_FILE={SECRETS}',
    ]


def test_app_conf_keeps_the_old_key_variable_for_contract_one() -> None:
    """Under contract 1 an old release still finds its key file."""
    assert (DEPLOY / 'CONTRACT').read_text(encoding='utf-8') == '1\n'
    assert drop_in_environment(path=APP_CONF)['PAPERROUTE_API_KEY_FILE'] == (
        SECRETS
    )
    service = read_unit(name='paperroute.service')['Service']
    assert 'PAPERROUTE' not in service['Environment']


def test_app_unit_is_notify_with_watchdog() -> None:
    """The app reports readiness and is restarted when it stops pinging."""
    service = read_unit(name='paperroute.service')['Service']
    assert (service['Type'], service['WatchdogSec']) == ('notify', '30')
    assert service['AmbientCapabilities'] == 'CAP_NET_BIND_SERVICE'
    assert service['CapabilityBoundingSet'] == 'CAP_NET_BIND_SERVICE'


def test_service_unit_ends_with_the_app_arguments() -> None:
    """The board takes the app's flags last; the nightly timer stays."""
    service = read_unit(name='paperroute.service')['Service']
    timer = read_unit(name='paperroute-timetable.timer')['Timer']
    assert service['ExecStart'].endswith(
        ' --power --wifi'
        ' --version-file /opt/paperroute/current/VERSION'
        ' $PAPERROUTE_APP_ARGS'
    )
    assert '--rebuild' not in service['ExecStart']
    assert timer['OnCalendar'] == '*-*-* 03:30:00'


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
        args=(value, APP_NAME),
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


def script_function(*, path: Path, name: str) -> str:
    """
    Take one function's definition from a script.

    Parameters
    ----------
    path : Path
        The script.
    name : str
        The function.

    Returns
    -------
    str
        From ``name() {`` to its closing brace.
    """
    found = re.search(
        pattern=rf'^{name}\(\) \{{$.*?^\}}$',
        string=script_text(path=path),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert found is not None, name
    return found.group(0)


def run_functions(
    *,
    names: tuple[str, ...],
    program: str,
    env: dict[str, str],
    script: Path = BIN / 'install-release',
) -> subprocess.CompletedProcess[str]:
    """
    Run a script's named functions under a program of stubs.

    Parameters
    ----------
    names : tuple[str, ...]
        The functions to take from the script.
    program : str
        Variables, stubs and calls, run after the definitions.
    env : dict[str, str]
        The environment to run it in.
    script : Path
        The script, install-release unless given.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    definitions = '\n'.join(
        script_function(path=script, name=name)
        for name in names
    )
    return subprocess.run(
        args=[
            'bash',
            '-c',
            f'set -euo pipefail\n{definitions}\n{program}',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def zone_stage(*, root: Path, output: str) -> Path:
    """
    Stage a built tree whose board prints the given zone output.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    output : str
        What the stub ``paperroute-board zone`` prints.

    Returns
    -------
    Path
        The tree.
    """
    tree = stage_tree(stage=root / 'stage')
    board = tree / '.venv' / 'bin' / 'paperroute-board'
    board.parent.mkdir(parents=True)
    printed = root / 'zone.txt'
    printed.write_text(output, encoding='utf-8')
    board.write_text(
        f'#!/bin/sh\n[ "$1" = zone ] || exit 9\ncat {printed}\n',
        encoding='utf-8',
    )
    board.chmod(0o755)
    stubs = root / 'stubs'
    stubs.mkdir()
    uv = stubs / 'uv'
    uv.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >>{root / "uv.log"}\n',
        encoding='utf-8',
    )
    uv.chmod(0o755)
    return tree


@pytest.mark.parametrize(
    ('output', 'status'),
    (
        ('Australia/Sydney\n[Service]\n', 1),
        ('Australia/Sydney\n[Service]', 1),
        ('../../../etc/passwd', 1),
        ('Australia/Nowhere_Atlantis', 1),
        ('', 1),
        ('Australia/Sydney\n', 0),
    ),
)
def test_install_release_checks_the_zone(
    tmp_path: Path,
    output: str,
    status: int,
) -> None:
    """build() asks the release its zone and stops before switching."""
    text = script_text(path=BIN / 'install-release')
    switch = text.index('\nswitch_to "$R/$SHA"\n')
    assert text.index(ZONE_RE) < switch
    assert text.index('-f "/usr/share/zoneinfo/$ZONE"') < switch
    assert text.index('\nbuild\n') < switch
    build = script_function(path=BIN / 'install-release', name='build')
    assert '[[ ! $ZONE =~ $ZONE_RE || ' in build
    assert '-f "/usr/share/zoneinfo/$ZONE"' in build
    if status == 0 and not Path('/usr/share/zoneinfo/Australia/Sydney').is_file():
        pytest.skip('this machine has no zoneinfo for Australia/Sydney')
    tree = zone_stage(root=tmp_path, output=output)
    program = (
        f'OWNER=me TREE={tree} OPT=/opt APP=paperroute-{APP_NAME}\n'
        'TIMERS=paperroute-timetable.timer\n'
        f'{ZONE_RE}\n'
        f'CURRENT={tmp_path / "current"} R={tmp_path / "releases"}\n'
        'runuser() { shift 3; "$@"; }\n'
        f'{SWITCH_STUB}\n'
        'build\nswitch_to "$R/abc1234"\nprintf "%s\\n" "$ZONE"\n'
    )
    result = run_functions(
        names=('build',),
        program=program,
        env=user_env(PATH=f'{tmp_path / "stubs"}:{os.environ["PATH"]}'),
    )
    assert result.returncode == status, result.stderr
    assert len(
        (tmp_path / 'uv.log').read_text(encoding='utf-8').splitlines()
    ) == 2
    if status:
        assert result.stderr == (
            'install-release: the release names no installed time zone\n'
        )
        assert not (tmp_path / 'current').is_symlink()
    else:
        assert result.stdout == 'Australia/Sydney\n'
        assert (tmp_path / 'current').is_symlink()


def timer_etc(*, root: Path, timers: dict[str, str]) -> Path:
    """
    Make a stand-in for /etc/systemd/system holding installed timers.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    timers : dict[str, str]
        Each timer's file name and its ``OnCalendar=`` value.

    Returns
    -------
    Path
        The directory.
    """
    etc = root / 'etc'
    etc.mkdir()
    for name, calendar in timers.items():
        (etc / name).write_text(
            f'[Timer]\nOnCalendar={calendar}\nPersistent=true\n'
            '\n[Install]\nWantedBy=timers.target\n',
            encoding='utf-8',
        )
    return etc


ZONE_FUNCTIONS: Final[tuple[str, ...]] = (
    'calendar_of',
    'set_timer_zone',
    'set_timer_zones',
)


def test_install_release_skips_unchanged_zone(tmp_path: Path) -> None:
    """With no reload due, set_timer_zone reloads only on a change."""
    body = script_function(path=BIN / 'install-release', name='set_timer_zone')
    assert body.index('cmp -s') < body.index('systemctl daemon-reload')
    timer = 'paperroute-timetable.timer'
    etc = timer_etc(root=tmp_path, timers={timer: '*-*-* 03:30:00'})
    conf = etc / f'{timer}.d' / 'app-zone.conf'
    log = tmp_path / 'systemctl.log'
    program = (
        f'ETC={etc}\n'
        'systemctl() {\n'
        '    if [[ $1 == show ]]; then printf "no\\n"; return 0; fi\n'
        f'    printf "%s\\n" "$*" >>{log}\n'
        '}\n'
        f'set_timer_zone {timer} Australia/Sydney\n'
        f'set_timer_zone {timer} Australia/Sydney\n'
        f'set_timer_zone {timer} Australia/Perth\n'
    )
    result = run_functions(
        names=ZONE_FUNCTIONS,
        program=program,
        env=user_env(),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert conf.read_text(encoding='utf-8') == (
        '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 03:30:00 Australia/Perth\n'
    )
    assert conf.stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in conf.parent.iterdir()) == [
        'app-zone.conf',
    ]
    assert log.read_text(encoding='utf-8') == (
        'daemon-reload\ndaemon-reload\n'
    )


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


def copy_deploy_script(*, root: Path) -> Path:
    """
    Copy scripts/deploy into a tree whose scripts/check leaves a marker.

    Parameters
    ----------
    root : Path
        The tree to make.

    Returns
    -------
    Path
        The copied deploy script.
    """
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    deploy = scripts / 'deploy'
    shutil.copy2(src=DEPLOY_SCRIPT, dst=deploy)
    check = scripts / 'check'
    check.write_text(
        f'#!/bin/sh\n: >{root / "checked"}\nexit 1\n',
        encoding='utf-8',
    )
    check.chmod(0o755)
    return deploy


@pytest.mark.parametrize(
    ('args', 'message'),
    (
        ((), 'usage: scripts/deploy HOST APP\n'),
        (('host',), 'usage: scripts/deploy HOST APP\n'),
        (('host', 'Sydney'), 'usage: scripts/deploy HOST APP\n'),
        (('host', '../x'), 'usage: scripts/deploy HOST APP\n'),
        (('host', 'sydney', 'x'), 'usage: scripts/deploy HOST APP\n'),
        (('host', 'nosuch'), 'deploy: no app apps/paperroute-nosuch\n'),
    ),
)
def test_deploy_requires_an_app(
    tmp_path: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    """Deploy refuses a missing, malformed or unknown app before checking."""
    deploy = copy_deploy_script(root=tmp_path)
    result = subprocess.run(
        args=['bash', str(deploy), *args],
        env=user_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert (result.returncode, result.stderr) == (2, message)
    assert not (tmp_path / 'checked').exists()


def test_deploy_checks_a_known_app(tmp_path: Path) -> None:
    """An app that exists gets as far as scripts/check."""
    deploy = copy_deploy_script(root=tmp_path)
    app = tmp_path / 'apps' / f'paperroute-{APP_NAME}'
    app.mkdir(parents=True)
    (app / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
    result = subprocess.run(
        args=['bash', str(deploy), 'host', APP_NAME],
        env=user_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert (tmp_path / 'checked').exists()


def test_deploy_files_name_no_city() -> None:
    """No text file under deploy/ names a city or its transport agency."""
    texts = {
        path: file_content(path=path)
        for path in sorted(DEPLOY.rglob('*'))
        if path.is_file()
    }
    named = [
        str(path.relative_to(DEPLOY))
        for path, text in texts.items()
        if isinstance(text, str)
        and CITY_NAMES.search(text)
    ]
    assert sum(isinstance(text, str) for text in texts.values()) > 10
    assert not named


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
    checked = 0
    for name in UNITS:
        unit = read_unit(name=name)
        service = unit.get('Service', {})
        if not any(key in service for key in ROOT_EXEC_KEYS):
            continue
        checked += 1
        assert environment_names(unit=unit) <= ROOT_SAFE_ENVIRONMENT
        assert service.get('EnvironmentFile', '/etc/').startswith('/etc/')
    assert checked == 2
    assert set(drop_in_environment(path=APP_CONF)) <= ROOT_SAFE_ENVIRONMENT
    assert 'EnvironmentFile' not in APP_CONF.read_text(encoding='utf-8')


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
    """No systemd unit or drop-in loads the retired pin file."""
    units = [
        path
        for folder in (SYSTEMD, SYDNEY_SYSTEMD)
        for path in sorted(folder.rglob('*'))
        if path.is_file()
    ]
    assert len(units) == 6
    for unit in units:
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
    assert (result.returncode, result.stderr) == (2, LED_USAGE)
    assert led_state(led=led) == {}


def test_led_busy_replaces_rebuild(tmp_path: Path) -> None:
    """busy is the slow blink; the old rebuild mode is refused."""
    led = led_dir(tmp_path=tmp_path)
    result = run_script(
        name='paperroute-led',
        args=('rebuild',),
        env=user_env(PAPERROUTE_LED_DIR=str(led)),
    )
    assert (result.returncode, result.stderr) == (2, LED_USAGE)
    assert led_state(led=led) == {}
    assert read_unit(name='paperroute-timetable.service')['Service'][
        'ExecStartPre'
    ] == '+/usr/local/bin/paperroute-led busy'


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


def copy_deploy(tmp_path: Path, app: Path = WITH_UNITS) -> Path:
    """
    Stage deploy/ and an app's deploy/ the way scripts/provision does.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.
    app : Path
        The app's folder; with no ``deploy/`` its part is empty.

    Returns
    -------
    Path
        The staging directory, holding ``kit/`` and ``app/``.
    """
    copy = tmp_path / 'paperroute-provision'
    shutil.copytree(src=DEPLOY, dst=copy / 'kit')
    if (app / 'deploy').is_dir():
        shutil.copytree(src=app / 'deploy', dst=copy / 'app')
    else:
        (copy / 'app').mkdir()
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
        The staging directory.
    sudo_user : str | None
        The ``SUDO_USER`` to claim, or None to leave it unset.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    extra = {} if sudo_user is None else {'SUDO_USER': sudo_user}
    script = copy / 'kit' / 'bin' / 'provision-device'
    return subprocess.run(
        args=['bash', str(script), str(copy), 'demo'],
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
        ('Pat.L', 'the deploy user must be a plain lowercase name'),
        ('root', 'the deploy user cannot be root'),
        ('paperroute', 'the deploy user cannot be the service user'),
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
    (copy / 'kit' / 'conf' / 'link').symlink_to('/etc/passwd')
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
    assert len(found) == 4
    assert all(
        re.search(pattern=r' 9>&-(?:\)|$)', string=command)
        for command in found
    )


def test_install_release_makes_a_relocatable_venv() -> None:
    """One relocatable venv comes before the one sync, both offline."""
    text = script_text(path=BIN / 'install-release').replace('\\\n', ' ')
    venvs = commands(path=BIN / 'install-release', word='uv venv')
    syncs = commands(path=BIN / 'install-release', word='uv sync')
    assert (len(venvs), len(syncs)) == (1, 1)
    assert '--relocatable' in venvs[0].split()
    assert venvs[0].endswith(
        ' uv venv --relocatable --project "$TREE" "$TREE/.venv" 9>&-'
    )
    for command in (*venvs, *syncs):
        assert ' UV_PYTHON_INSTALL_DIR="$OPT/python" ' in command
        assert ' UV_PYTHON_DOWNLOADS=never ' in command
    assert text.index(' uv venv ') < text.index(' uv sync ')


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
        '"$APP"',
        '--no-default-groups',
        '--frozen',
        '--no-editable',
    } <= set(words)
    assert words[words.index('--package') + 1] == '"$APP"'
    assert 'APP=paperroute-$APP_NAME' in script_text(
        path=BIN / 'install-release',
    )
    assert '--no-dev' not in words


def test_deploy_stages_only_the_named_app() -> None:
    """Deploy copies the workspace packages and the named app alone."""
    found = commands(path=DEPLOY_SCRIPT, word='git ls-files')
    assert len(found) == 1
    words = found[0].split()
    assert {'packages', '"apps/paperroute-$app"'} <= set(words)
    assert 'src' not in words
    assert 'apps' not in words
    assert commands(path=DEPLOY_SCRIPT, word='INSTALL')[-1] == (
        'ssh -- "$host" sudo -n "$INSTALL" "$sha" "$app" || status=$?'
    )


def test_deploy_stages_the_contract() -> None:
    """The release carries the kit's units contract, and no other kit file."""
    found = commands(path=DEPLOY_SCRIPT, word='git ls-files')
    assert found == [
        'git ls-files -z packages "apps/paperroute-$app" pyproject.toml'
        ' uv.lock deploy/CONTRACT'
        ' | rsync -a --from0 --files-from=- ./ "$host:$STAGE/$sha/"',
    ]
    simulated = commands(
        path=ROOT / 'scripts' / 'simulate-pi-build',
        word='git ls-files',
    )
    assert len(simulated) == 1
    assert ' uv.lock deploy/CONTRACT ' in simulated[0]


def test_install_release_records_the_app() -> None:
    """take_over writes the app's name beside VERSION, before chown."""
    body = script_function(path=BIN / 'install-release', name='take_over')
    version = '    printf \'%s\\n\' "$SHA" >"$INCOMING/tree/VERSION"\n'
    app = '    printf \'%s\\n\' "$APP" >"$INCOMING/tree/APP"\n'
    assert body.count(app) == 1
    assert body.index(version) + len(version) == body.index(app)
    assert body.index(app) < body.index('chown -hR')


def test_install_release_sets_timer_zone_after_health() -> None:
    """The timers' zone changes only once the new release is healthy."""
    text = script_text(path=BIN / 'install-release')
    body = script_function(path=BIN / 'install-release', name='set_timer_zone')
    call = text.index('\nif ! reason=$(set_timer_zones 2>&1); then\n')
    assert text.count('set_timer_zones 2>&1') == 1
    assert text.rindex('healthy') < call
    assert "printf '[Timer]\\nOnCalendar=\\n'" in body
    assert 'systemctl daemon-reload' in body
    assert 'restart paperroute-' not in text
    assert script_value(path=BIN / 'install-release', name='ETC') == (
        '/etc/systemd/system'
    )


def test_install_release_reports_unset_timer_zone() -> None:
    """A failed zone write says the release is live, why, and exits 1."""
    text = script_text(path=BIN / 'install-release')
    call = text.index('\nif ! reason=$(set_timer_zones 2>&1); then\n')
    said = text.index(
        "'install-release: %s is live, but the timer zone was not set: %s\\n'",
    )
    failed = text.index('    exit 1\nfi\nif [[ -n $reason ]]')
    assert call < said < failed
    assert text.index('"$SHA" "${reason:-no reason given}" >&2') < failed


def test_set_timer_zone_fails_at_first_step(tmp_path: Path) -> None:
    """Under `if`, a failed step still ends set_timer_zone, with a reason."""
    timer = 'paperroute-job.timer'
    etc = timer_etc(root=tmp_path, timers={timer: '*-*-* 03:30:00'})
    (etc / f'{timer}.d').write_text('', encoding='utf-8')
    log = tmp_path / 'systemctl.log'
    program = (
        f'ETC={etc}\n'
        f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
        f'if ! reason=$(set_timer_zone {timer} Australia/Perth 2>&1); then\n'
        '    printf "failed: %s\\n" "$reason"\n'
        'fi\n'
    )
    result = run_functions(
        names=ZONE_FUNCTIONS,
        program=program,
        env=user_env(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('failed: mkdir')
    assert not log.exists()


def test_set_timer_zone_retries_failed_reload(tmp_path: Path) -> None:
    """A failed reload keeps the drop-in; systemd's flag retries it."""
    body = script_function(path=BIN / 'install-release', name='set_timer_zone')
    assert (
        'systemctl show -p NeedDaemonReload --value "$timer") == no ]]'
    ) in body
    assert 'rm -f -- "$conf"' not in body
    timer = 'paperroute-timetable.timer'
    etc = timer_etc(root=tmp_path, timers={timer: '*-*-* 03:30:00'})
    conf = etc / f'{timer}.d' / 'app-zone.conf'
    log = tmp_path / 'systemctl.log'
    need = tmp_path / 'need'
    program = (
        f'ETC={etc}\n'
        'systemctl() {\n'
        f'    printf "%s\\n" "$*" >>{log}\n'
        '    if [[ $1 == show ]]; then\n'
        f'        cat {need}\n'
        f'    elif [[ -e {need} ]]; then\n'
        f'        printf "no\\n" >{need}\n'
        '    else\n'
        f'        printf "yes\\n" >{need}\n'
        '        printf "Failed to reload daemon\\n" >&2\n'
        '        return 1\n'
        '    fi\n'
        '}\n'
        f'if ! reason=$(set_timer_zone {timer} Australia/Perth 2>&1); then\n'
        '    printf "failed: %s\\n" "$reason"\n'
        'fi\n'
        f'set_timer_zone {timer} Australia/Perth\n'
        f'set_timer_zone {timer} Australia/Perth\n'
    )
    result = run_functions(
        names=ZONE_FUNCTIONS,
        program=program,
        env=user_env(),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert result.stdout == 'failed: Failed to reload daemon\n'
    assert conf.read_text(encoding='utf-8') == (
        '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 03:30:00 Australia/Perth\n'
    )
    show = f'show -p NeedDaemonReload --value {timer}\n'
    assert log.read_text(encoding='utf-8') == (
        f'daemon-reload\n{show}daemon-reload\n{show}'
    )


def test_install_release_shows_reload_warnings() -> None:
    """Output from a zone change that worked still reaches the terminal."""
    text = script_text(path=BIN / 'install-release')
    shown = (
        '\nif [[ -n $reason ]]; then\n'
        "    printf '%s\\n' \"$reason\" >&2\n"
        'fi\nprune\n'
    )
    assert text.count(shown) == 1
    assert text.index('"${reason:-no reason given}"') < text.index(shown)


def test_install_release_starts_without_timetable() -> None:
    """The app starts and is checked whether or not a timetable exists."""
    text = script_text(path=BIN / 'install-release')
    assert 'timetable.db' not in text
    assert 'paperroute-timetable.service' not in text
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
        'uv run python -m "paperroute_$app.build" check-tiles data/tiles.db '
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
            '/usr/bin/setpriv',
            '--reuid=paperroute',
            '--regid=paperroute',
            '--groups=spi,gpio',
            '--',
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
        ' paperroute-on-screen $timers',
    ]


def test_screens_run_as_the_app_user_with_its_groups() -> None:
    """The boot unit and the shutdown hook drop root as the app does."""
    app = read_unit(name='paperroute.service')['Service']
    screen = read_unit(name='paperroute-on-screen.service')['Service']
    keys = ('User', 'Group', 'SupplementaryGroups')
    assert {key: screen[key] for key in keys} == {key: app[key] for key in keys}
    hook = script_text(path=OFF_HOOK).replace('\\\n', ' ')
    groups = ','.join(app['SupplementaryGroups'].split())
    drop = (
        f'/usr/bin/setpriv --reuid={app["User"]} --regid={app["Group"]}'
        f' --groups={groups} --'
    )
    assert ' '.join(hook.split()).count(drop) == 1


def link_tree(*, root: Path) -> tuple[Path, Path]:
    """
    Make a release tree and a shared Python, with links that may stay.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.

    Returns
    -------
    tuple[Path, Path]
        The tree and the shared Python directory.
    """
    python = root / 'python'
    (python / 'cpython' / 'bin').mkdir(parents=True)
    (python / 'cpython' / 'bin' / 'python3').write_text('', encoding='utf-8')
    tree = root / 'releases' / 'tree'
    bin_dir = tree / '.venv' / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'python').symlink_to(python / 'cpython' / 'bin' / 'python3')
    (bin_dir / 'python3').symlink_to('python')
    (tree / '.venv' / 'lib64').symlink_to('bin')
    (tree / 'top').symlink_to('.')
    return tree, python


def run_check_links(
    *,
    tree: Path,
    python: Path,
) -> subprocess.CompletedProcess[str]:
    """
    Run install-release's ``check_links`` over a tree.

    Parameters
    ----------
    tree : Path
        The release tree.
    python : Path
        The shared Python directory.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    return run_functions(
        names=('check_links',),
        program=f'check_links {tree} {python}\n',
        env=user_env(),
    )


def test_check_links_passes_links_inside(tmp_path: Path) -> None:
    """Links into the tree or the shared Python pass."""
    tree, python = link_tree(root=tmp_path)
    result = run_check_links(tree=tree, python=python)
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')


@pytest.mark.parametrize(
    'target',
    ('../../../../evil', '/etc/passwd', '/nonexistent/dir/x', '../../..'),
)
def test_check_links_names_a_link_outside(
    tmp_path: Path,
    target: str,
) -> None:
    """A link out of the tree, or a dangling one, fails and is named."""
    tree, python = link_tree(root=tmp_path)
    (tmp_path / 'evil').write_text('', encoding='utf-8')
    (tree / '.venv' / 'bin' / 'payload').symlink_to(target)
    result = run_check_links(tree=tree, python=python)
    assert (result.returncode, result.stdout) == (1, '.venv/bin/payload\n')


def test_check_links_fails_when_find_cannot_read(tmp_path: Path) -> None:
    """A tree find cannot read fails the check instead of passing."""
    tree, python = link_tree(root=tmp_path)
    locked = tree / 'locked'
    locked.mkdir()
    locked.chmod(0)
    try:
        result = run_check_links(tree=tree, python=python)
    finally:
        locked.chmod(0o700)
    assert (result.returncode, result.stdout) == (1, 'find failed\n')


def test_check_links_names_a_dangling_link_inside(tmp_path: Path) -> None:
    """A link that stays in the tree but points at nothing fails."""
    tree, python = link_tree(root=tmp_path)
    (tree / '.venv' / 'bin' / 'gone').symlink_to('missing')
    result = run_check_links(tree=tree, python=python)
    assert (result.returncode, result.stdout) == (1, '.venv/bin/gone\n')


def test_install_release_checks_links_after_take_over() -> None:
    """Links are checked once root owns the tree, before it is released."""
    body = script_function(path=BIN / 'install-release', name='take_over')
    check = body.index('check_links "$INCOMING/tree" "$OPT/python"')
    assert body.index('chown -hR') < body.index('chmod -R') < check
    assert check < body.index('mv -T -- "$INCOMING/tree" "$R/$SHA"')
    assert 'the release links outside itself' in body[check:]


def test_simulate_pi_build_checks_links() -> None:
    """The simulated build passes the same link check the Pi runs."""
    text = script_text(path=ROOT / 'scripts' / 'simulate-pi-build')
    assert text.count('check_links "$moved" "$python_dir"') == 1


def install_sudoers(*, root: Path, user: str) -> Path:
    """
    Run provision's ``install_sudoers`` for a deploy user.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory; the share and sudoers.d go here.
    user : str
        The deploy user.

    Returns
    -------
    Path
        The installed sudoers file.
    """
    share = root / 'share'
    (share / 'deploy').mkdir(parents=True)
    shutil.copytree(src=DEPLOY / 'sudoers', dst=share / 'deploy' / 'sudoers')
    sudoers = root / 'sudoers.d' / 'paperroute-deploy'
    sudoers.parent.mkdir()
    body = script_function(
        path=BIN / 'provision-device',
        name='install_sudoers',
    )
    program = (
        'set -euo pipefail\n'
        'visudo() { return 0; }\n'
        # BSD mv has no -T.
        'mv() { [[ $1 == -T ]] && shift; command mv "$@"; }\n'
        f'SHARE={share}\nSUDOERS={sudoers}\n'
        f'SUDOERS_NEW={sudoers.parent}/.paperroute-deploy.new\n'
        f'DEPLOY_USER={user}\n{body}\ninstall_sudoers\n'
    )
    subprocess.run(args=['bash', '-c', program], check=True, timeout=30)
    return sudoers


@pytest.mark.parametrize('user', ('pashri', 'alice'))
def test_provision_grants_sudo_to_the_deploy_user(
    tmp_path: Path,
    user: str,
) -> None:
    """The installed rule names the user who ran provision, read-only."""
    sudoers = install_sudoers(root=tmp_path, user=user)
    rules = [
        line
        for line in sudoers.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert rules == [SUDOERS_RULE.replace('pashri ', f'{user} ', 1)]
    assert sudoers.stat().st_mode & 0o777 == 0o440
    assert sorted(path.name for path in sudoers.parent.iterdir()) == [
        'paperroute-deploy',
    ]


def test_provision_names_no_deploy_user() -> None:
    """Only the shipped sudoers default names pashri; provision does not."""
    text = script_text(path=BIN / 'provision-device')
    assert 'pashri' not in text.replace("s/^pashri /", '')
    assert commands(path=BIN / 'provision-device', word='paperroute-stage') == [
        'make_dir "$DEPLOY_USER" 0700 /var/cache/paperroute-stage',
    ]
    assert 'DEPLOY_USER=$SUDO_USER' in text.splitlines()


FAKE_INSTALL: Final[str] = '''#!/bin/bash
set -eu
dirs=no
parents=no
while (($#)); do
    case $1 in
        -m | -o | -g) shift 2 ;;
        -d) dirs=yes; shift ;;
        -D) parents=yes; shift ;;
        *) break ;;
    esac
done
if [[ $dirs == yes ]]; then
    mkdir -p "$@"
    exit 0
fi
args=("$@")
dest=${args[$# - 1]}
unset "args[$# - 1]"
if [[ $parents == yes ]]; then
    mkdir -p "$(dirname "$dest")"
fi
cp "${args[@]}" "$dest"
'''
INSTALL_FILES_FUNCTIONS: Final[tuple[str, ...]] = (
    'in_list',
    'adopt_city_zones',
    'wanted_units',
    'shipped_drop_ins',
    'app_timers',
    'install_units',
    'remove_strays',
    'enable_units',
)
SYDNEY_APP: Final[Path] = ROOT / 'apps' / 'paperroute-sydney'
KIT_ENABLED: Final[str] = (
    'enable paperroute-led-boot paperroute paperroute-on-screen'
)


@dataclass(frozen=True)
class Device:
    """A provisioned share, a systemd folder and stubbed commands."""

    share: Path
    etc: Path
    log: Path
    env: dict[str, str]


def stub_commands(*, root: Path, scripts: dict[str, str]) -> Path:
    """
    Write executable stand-ins for commands into a folder.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    scripts : dict[str, str]
        Each command's name and its script.

    Returns
    -------
    Path
        The folder, to put first on ``PATH``.
    """
    stubs = root / 'stubs'
    stubs.mkdir(exist_ok=True)
    for name, text in scripts.items():
        (stubs / name).write_text(text, encoding='utf-8')
        (stubs / name).chmod(0o755)
    return stubs


def make_device(*, root: Path, app: Path) -> Device:
    """
    Make a share as provision leaves it and an empty systemd folder.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    app : Path
        The app's folder; with no ``deploy/`` its part is empty.

    Returns
    -------
    Device
        The folders, the command log and the environment.
    """
    share = root / 'share'
    shutil.copytree(src=DEPLOY, dst=share / 'deploy')
    if (app / 'deploy').is_dir():
        shutil.copytree(src=app / 'deploy', dst=share / 'app')
    else:
        (share / 'app').mkdir()
    etc = root / 'etc'
    etc.mkdir()
    log = root / 'systemctl.log'
    stubs = stub_commands(
        root=root,
        scripts={
            'systemctl': f'#!/bin/sh\nprintf "%s\\n" "$*" >>{log}\n',
            'install': FAKE_INSTALL,
        },
    )
    env = user_env(PATH=f'{stubs}:{os.environ["PATH"]}')
    return Device(share=share, etc=etc, log=log, env=env)


def install_units(*, device: Device) -> subprocess.CompletedProcess[str]:
    """
    Run install-files' unit steps over a device.

    Parameters
    ----------
    device : Device
        The share, systemd folder and stubs.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    program = (
        f'SHARE={device.share}\nSRC=$SHARE/deploy\nAPP_SRC=$SHARE/app\n'
        f'ETC={device.etc}\n'
        'install_units\nremove_strays\nenable_units\n'
    )
    return run_functions(
        names=INSTALL_FILES_FUNCTIONS,
        program=program,
        env=device.env,
        script=BIN / 'install-files',
    )


def tree_files(root: Path) -> dict[str, str]:
    """
    Read every file under a folder as text.

    Parameters
    ----------
    root : Path
        The folder.

    Returns
    -------
    dict[str, str]
        Each file's relative path and text.
    """
    return {
        str(path.relative_to(root)): path.read_text(encoding='utf-8')
        for path in sorted(root.rglob('*'))
        if path.is_file()
    }


def logged(device: Device) -> str:
    """
    Read what the stub systemctl was asked to do.

    Parameters
    ----------
    device : Device
        The device whose log to read.

    Returns
    -------
    str
        The calls, one per line, or empty when there were none.
    """
    if not device.log.exists():
        return ''
    return device.log.read_text(encoding='utf-8')


@pytest.mark.parametrize(
    'app',
    (WITH_UNITS, SYDNEY_APP),
    ids=('demo', 'sydney'),
)
def test_install_files_installs_the_drop_in(tmp_path: Path, app: Path) -> None:
    """The kit's units, the app's units and its drop-in land in place."""
    device = make_device(root=tmp_path, app=app)
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    expected = tree_files(root=DEPLOY / 'systemd') | tree_files(
        root=app / 'deploy' / 'systemd',
    )
    assert tree_files(root=device.etc) == expected
    assert 'paperroute.service.d/app.conf' in expected


@pytest.mark.parametrize(
    ('app', 'calls'),
    (
        (
            WITH_UNITS,
            f'daemon-reload\n{KIT_ENABLED} paperroute-job.timer\n'
            'start paperroute-job.timer\n',
        ),
        (WITHOUT_UNITS, f'daemon-reload\n{KIT_ENABLED}\n'),
    ),
    ids=('with', 'without'),
)
def test_install_files_enables_only_shipped_timers(
    tmp_path: Path,
    app: Path,
    calls: str,
) -> None:
    """Only the app's own timers are enabled and started."""
    device = make_device(root=tmp_path, app=app)
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert logged(device=device) == calls
    assert not list(device.etc.glob('paperroute-timetable*'))
    lines = script_text(path=BIN / 'install-files').splitlines()
    steps = [lines.index(step) for step in INSTALL_FILES_FUNCTIONS[-3:]]
    assert steps == sorted(steps)


def test_install_files_removes_units_outside_the_set(tmp_path: Path) -> None:
    """An older provision's timetable units go when the app ships none."""
    device = make_device(root=tmp_path, app=WITHOUT_UNITS)
    for name in ('paperroute-timetable.service', 'paperroute-timetable.timer'):
        shutil.copyfile(src=SYDNEY_SYSTEMD / name, dst=device.etc / name)
    zone = device.etc / 'paperroute-timetable.timer.d' / 'app-zone.conf'
    zone.parent.mkdir()
    zone.write_text('[Timer]\n', encoding='utf-8')
    (device.etc / 'ssh.service').write_text('', encoding='utf-8')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert sorted(tree_files(root=device.etc)) == sorted(
        [*KIT_UNITS, 'ssh.service'],
    )
    assert not zone.parent.exists()
    assert logged(device=device).splitlines()[:2] == [
        'disable --now paperroute-timetable.service',
        'disable --now paperroute-timetable.timer',
    ]


def test_install_files_keeps_the_zone_drop_in(tmp_path: Path) -> None:
    """The live zone drop-in of an unchanged timer survives a provision."""
    device = make_device(root=tmp_path, app=SYDNEY_APP)
    timer = 'paperroute-timetable.timer'
    shutil.copyfile(src=SYDNEY_SYSTEMD / timer, dst=device.etc / timer)
    drop_ins = device.etc / f'{timer}.d'
    drop_ins.mkdir()
    (drop_ins / 'app-zone.conf').write_text('zone\n', encoding='utf-8')
    (drop_ins / 'other.conf').write_text('', encoding='utf-8')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert tree_files(root=drop_ins) == {'app-zone.conf': 'zone\n'}


def test_install_files_drops_the_zone_drop_in_when_the_timer_changes(
    tmp_path: Path,
) -> None:
    """A changed calendar is never overridden by a stale zone drop-in."""
    device = make_device(root=tmp_path, app=SYDNEY_APP)
    timer = 'paperroute-timetable.timer'
    (device.etc / timer).write_text(
        '[Timer]\nOnCalendar=*-*-* 02:00:00\n',
        encoding='utf-8',
    )
    zone = device.etc / f'{timer}.d' / 'app-zone.conf'
    zone.parent.mkdir()
    zone.write_text('zone\n', encoding='utf-8')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert not zone.exists()
    assert (device.etc / timer).read_text(encoding='utf-8') == (
        (SYDNEY_SYSTEMD / timer).read_text(encoding='utf-8')
    )


def test_install_files_removes_a_stale_app_conf(tmp_path: Path) -> None:
    """The board's app drop-in goes when the new app ships none."""
    device = make_device(root=tmp_path, app=WITHOUT_UNITS)
    conf = device.etc / 'paperroute.service.d' / 'app.conf'
    conf.parent.mkdir()
    conf.write_text('[Service]\n', encoding='utf-8')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert not conf.parent.exists()
    assert sorted(tree_files(root=device.etc)) == sorted(KIT_UNITS)


# The timer 688b82b installed, which an upgrading Pi still holds.
OLD_TIMETABLE_TIMER: Final[str] = (
    '[Timer]\n'
    "# UTC here; install-release adds the city's zone in a drop-in\n"
    'OnCalendar=*-*-* 03:30:00\n'
    'Persistent=true\n'
    'RandomizedDelaySec=0\n'
    '\n'
    '[Install]\n'
    'WantedBy=timers.target\n'
)
CITY_ZONE: Final[str] = (
    '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 03:30:00 Australia/Sydney\n'
)


def upgrading_device(*, root: Path) -> tuple[Device, Path]:
    """
    Make a Sydney device as a provision from 688b82b left it.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.

    Returns
    -------
    tuple[Device, Path]
        The device and its timetable timer's drop-in folder.
    """
    device = make_device(root=root, app=SYDNEY_APP)
    timer = device.etc / 'paperroute-timetable.timer'
    timer.write_text(OLD_TIMETABLE_TIMER, encoding='utf-8')
    drop_ins = device.etc / 'paperroute-timetable.timer.d'
    drop_ins.mkdir()
    (drop_ins / 'city-zone.conf').write_text(CITY_ZONE, encoding='utf-8')
    return device, drop_ins


def test_install_files_keeps_the_city_zone_as_app_zone(tmp_path: Path) -> None:
    """An upgrade keeps the timer in its zone until the deploy rewrites it."""
    device, drop_ins = upgrading_device(root=tmp_path)
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert tree_files(root=drop_ins) == {'app-zone.conf': CITY_ZONE}
    assert (SYDNEY_SYSTEMD / 'paperroute-timetable.timer').read_text(
        encoding='utf-8',
    ) == OLD_TIMETABLE_TIMER


def test_install_files_removes_city_zone_conf(tmp_path: Path) -> None:
    """With an app-zone.conf in place, the old city-zone.conf just goes."""
    device, drop_ins = upgrading_device(root=tmp_path)
    (drop_ins / 'app-zone.conf').write_text('zone\n', encoding='utf-8')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert tree_files(root=drop_ins) == {'app-zone.conf': 'zone\n'}


def provision_repo(*, root: Path, app: Path) -> Path:
    """
    Make a repo holding scripts/provision, deploy/ and one app, demo.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    app : Path
        The fixture whose ``deploy/``, if any, becomes demo's.

    Returns
    -------
    Path
        The copied scripts/provision.
    """
    repo = root / 'repo'
    (repo / 'scripts').mkdir(parents=True)
    script = repo / 'scripts' / 'provision'
    shutil.copy2(src=ROOT / 'scripts' / 'provision', dst=script)
    shutil.copytree(src=DEPLOY, dst=repo / 'deploy')
    demo = repo / 'apps' / 'paperroute-demo'
    demo.mkdir(parents=True)
    (demo / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
    if (app / 'deploy').is_dir():
        shutil.copytree(src=app / 'deploy', dst=demo / 'deploy')
    return script


def run_provision_script(
    *,
    root: Path,
    script: Path,
    args: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    """
    Run scripts/provision with rsync and ssh stubbed.

    The stub rsync copies what it is given into ``root/staged``, and
    both stubs log their arguments.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    script : Path
        The copied scripts/provision.
    args : tuple[str, ...]
        Its arguments.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    staged = root / 'staged'
    stubs = stub_commands(
        root=root,
        scripts={
            'rsync': (
                f'#!/bin/sh\nprintf "%s\\n" "$*" >>{root / "rsync.log"}\n'
                f'mkdir -p {staged}\ncp -R "$3". {staged}/\n'
            ),
            'ssh': f'#!/bin/sh\nprintf "%s\\n" "$*" >>{root / "ssh.log"}\n',
        },
    )
    return subprocess.run(
        args=['bash', str(script), *args],
        env=user_env(PATH=f'{stubs}:{os.environ["PATH"]}'),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    'app',
    (WITH_UNITS, WITHOUT_UNITS),
    ids=('with', 'without'),
)
def test_provision_stages_kit_and_app(tmp_path: Path, app: Path) -> None:
    """The kit goes as kit/, the app's deploy/ (or nothing) as app/."""
    script = provision_repo(root=tmp_path, app=app)
    result = run_provision_script(
        root=tmp_path,
        script=script,
        args=('me@host', 'demo'),
    )
    assert (result.returncode, result.stderr) == (0, '')
    staged = tmp_path / 'staged'
    assert sorted(path.name for path in staged.iterdir()) == ['app', 'kit']
    assert snapshot(root=staged / 'kit') == snapshot(root=DEPLOY)
    expected = (
        snapshot(root=app / 'deploy') if (app / 'deploy').is_dir() else {}
    )
    assert snapshot(root=staged / 'app') == expected
    rsync = (tmp_path / 'rsync.log').read_text(encoding='utf-8')
    assert rsync.startswith('-a --delete ')
    assert rsync.endswith('/ me@host:.paperroute-provision/\n')
    assert (tmp_path / 'ssh.log').read_text(encoding='utf-8') == (
        '-t -- me@host sudo bash'
        ' "$HOME/.paperroute-provision/kit/bin/provision-device"'
        ' "$HOME/.paperroute-provision" demo\n'
    )


def test_provision_reboots_when_asked(tmp_path: Path) -> None:
    """--reboot after the app reboots the Pi once provisioning is done."""
    script = provision_repo(root=tmp_path, app=WITHOUT_UNITS)
    result = run_provision_script(
        root=tmp_path,
        script=script,
        args=('me@host', 'demo', '--reboot'),
    )
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / 'ssh.log').read_text(encoding='utf-8').splitlines()
    assert calls[1:] == ['-t -- me@host sudo systemctl reboot']


@pytest.mark.parametrize(
    'args',
    (
        (),
        ('me@host',),
        ('me@host', '--reboot'),
        ('me@host', 'Demo'),
        ('me@host', '../x'),
        ('me@host', 'nosuch'),
        ('me@host', 'demo', '--now'),
        ('me@host', 'demo', '--reboot', 'x'),
        ('-oProxy=x', 'demo'),
    ),
)
def test_provision_requires_an_app(
    tmp_path: Path,
    args: tuple[str, ...],
) -> None:
    """A missing, malformed or unknown app exits 2 before copying."""
    script = provision_repo(root=tmp_path, app=WITH_UNITS)
    result = run_provision_script(root=tmp_path, script=script, args=args)
    assert (result.returncode, result.stderr) == (
        2,
        'usage: scripts/provision HOST APP [--reboot]\n',
    )
    assert not (tmp_path / 'rsync.log').exists()
    assert not (tmp_path / 'ssh.log').exists()


def provision_functions(
    *,
    names: tuple[str, ...],
    program: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run provision-device's named functions, with ``fail`` and its regexes.

    Parameters
    ----------
    names : tuple[str, ...]
        The functions to take from provision-device.
    program : str
        Variables, stubs and calls, run after the definitions.
    env : dict[str, str] | None
        The environment, this process's without ``SUDO_USER`` unless given.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    script = BIN / 'provision-device'
    regexes = ''.join(
        f'{name}={script_value(path=script, name=name)}\n'
        for name in ('APP_RE', 'CONTRACT_RE', 'TIMER_RE')
    )
    return run_functions(
        names=('fail', *names),
        program=f'{regexes}{program}',
        env=user_env() if env is None else env,
        script=script,
    )


def file_modes(root: Path) -> set[tuple[str, int]]:
    """
    List the permission bits of every entry under a folder.

    Parameters
    ----------
    root : Path
        The folder.

    Returns
    -------
    set[tuple[str, int]]
        Each relative path and its mode bits.
    """
    return {
        (str(path.relative_to(root)), path.stat().st_mode & 0o777)
        for path in root.rglob('*')
    }


def test_provision_keeps_the_kit_copy_in_place(tmp_path: Path) -> None:
    """The kit lands at share/deploy, where the screen units look."""
    staging = copy_deploy(tmp_path=tmp_path, app=WITH_UNITS)
    share = tmp_path / 'share'
    share.mkdir()
    result = provision_functions(
        names=('copy_tree', 'copy_deploy'),
        program=(
            f'chown() {{ :; }}\n{MV_STUB}\nSHARE={share}\nDIR={staging}\n'
            'copy_deploy\ncopy_deploy\n'
        ),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert sorted(path.name for path in share.iterdir()) == ['app', 'deploy']
    assert snapshot(root=share / 'deploy') == snapshot(root=DEPLOY)
    assert snapshot(root=share / 'app') == snapshot(
        root=WITH_UNITS / 'deploy',
    )
    assert {
        mode
        for name, mode in file_modes(root=share / 'deploy')
        if not name.startswith('bin/')
    } == {0o755, 0o644}
    assert {
        mode
        for name, mode in file_modes(root=share / 'deploy')
        if name.startswith('bin/')
    } == {0o755}
    screen = '/usr/local/share/paperroute/deploy/screens/on.raw'
    assert screen in read_unit(name='paperroute-on-screen.service')[
        'Service'
    ]['ExecStart']
    assert screen.replace('on.raw', 'off.raw') in script_text(path=OFF_HOOK)
    assert script_value(path=BIN / 'provision-device', name='SHARE') == (
        '/usr/local/share/paperroute'
    )
    assert (share / 'deploy' / 'screens' / 'on.raw').read_bytes() == (
        (DEPLOY / 'screens' / 'on.raw').read_bytes()
    )


@pytest.mark.parametrize(
    ('app', 'timers'),
    (
        (WITH_UNITS, 'paperroute-job.timer\n'),
        (WITHOUT_UNITS, ''),
        (SYDNEY_APP, 'paperroute-timetable.timer\n'),
    ),
    ids=('with', 'without', 'sydney'),
)
def test_provision_writes_contract_and_manifest(
    tmp_path: Path,
    app: Path,
    timers: str,
) -> None:
    """The share records the kit's contract and the app's timers."""
    device = make_device(root=tmp_path, app=app)
    result = provision_functions(
        names=('write_contract', 'write_manifest'),
        program=f'SHARE={device.share}\nwrite_contract\nwrite_manifest\n',
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert (device.share / 'units-contract').read_text(encoding='utf-8') == (
        '1\n'
    )
    assert (device.share / 'app-timers').read_text(encoding='utf-8') == (
        timers
    )
    lines = script_text(path=BIN / 'provision-device').splitlines()
    steps = [
        lines.index(step)
        for step in (
            'copy_deploy',
            'write_contract',
            'write_manifest',
            'set_state_aside',
            '/usr/local/sbin/paperroute-install-files',
        )
    ]
    assert steps == sorted(steps)


@pytest.mark.parametrize(
    ('entry', 'reason'),
    (
        ('ssh.service', 'the app may not ship systemd/ssh.service'),
        (
            'paperroute-on-screen.service',
            'the app may not replace systemd/paperroute-on-screen.service',
        ),
        (
            'paperroute_job.timer',
            'the app may not ship systemd/paperroute_job.timer',
        ),
        (
            'paperroute-x.timer.d/zone.conf',
            'systemd/paperroute-x.timer.d is for a unit nobody ships',
        ),
        (
            'paperroute.service.d/app-zone.conf',
            'the app may not ship systemd/paperroute.service.d/app-zone.conf',
        ),
        (
            'paperroute.service.d/notes.txt',
            'the app may not ship systemd/paperroute.service.d/notes.txt',
        ),
        ('ssh.service.d/x.conf', 'the app may not ship systemd/ssh.service.d'),
    ),
)
def test_provision_checks_the_app_units(
    tmp_path: Path,
    entry: str,
    reason: str,
) -> None:
    """The app may ship only paperroute units and drop-ins for them."""
    copy = copy_deploy(tmp_path=tmp_path)
    path = copy / 'app' / 'systemd' / entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('', encoding='utf-8')
    result = run_provision(copy=copy, sudo_user=getpass.getuser())
    assert (result.returncode, result.stderr) == (
        2,
        f'provision-device: {reason}\n',
    )


@pytest.mark.parametrize('text', ('', '0\n', 'one\n', '1 2\n'))
def test_provision_checks_the_contract(tmp_path: Path, text: str) -> None:
    """The kit's CONTRACT must hold a positive number."""
    copy = copy_deploy(tmp_path=tmp_path)
    (copy / 'kit' / 'CONTRACT').write_text(text, encoding='utf-8')
    result = run_provision(copy=copy, sudo_user=getpass.getuser())
    assert (result.returncode, result.stderr) == (
        2,
        'provision-device: kit/CONTRACT must hold a units contract number\n',
    )


def test_provision_needs_the_app_part(tmp_path: Path) -> None:
    """A staging directory without app/ is refused."""
    copy = copy_deploy(tmp_path=tmp_path)
    shutil.rmtree(copy / 'app')
    result = run_provision(copy=copy, sudo_user=getpass.getuser())
    assert (result.returncode, result.stderr) == (
        2,
        'provision-device: DIR has no app/\n',
    )


def test_manifest_timer_names_are_validated(tmp_path: Path) -> None:
    """provision refuses a badly named timer and writes no manifest."""
    device = make_device(root=tmp_path, app=WITHOUT_UNITS)
    (device.share / 'app' / 'systemd').mkdir()
    (device.share / 'app' / 'systemd' / 'paperroute_Job.timer').write_text(
        '',
        encoding='utf-8',
    )
    result = provision_functions(
        names=('write_manifest',),
        program=f'SHARE={device.share}\nwrite_manifest\n',
    )
    assert (result.returncode, result.stderr) == (
        2,
        "provision-device: the app's timer paperroute_Job.timer has a bad"
        ' name\n',
    )
    assert not (device.share / 'app-timers').exists()


RELEASE_REGEXES: Final[tuple[str, ...]] = ('TIMER_RE', 'NAMED_ZONE_RE')


def release_functions(
    *,
    names: tuple[str, ...],
    program: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run install-release's named functions, with ``fail`` and its regexes.

    Parameters
    ----------
    names : tuple[str, ...]
        The functions to take from install-release.
    program : str
        Variables, stubs and calls, run after the definitions.
    env : dict[str, str] | None
        The environment, this process's without ``SUDO_USER`` unless given.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    script = BIN / 'install-release'
    regexes = ''.join(
        f'{name}={script_value(path=script, name=name)}\n'
        for name in RELEASE_REGEXES
    )
    return run_functions(
        names=('fail', *names),
        program=f'{regexes}{program}',
        env=user_env() if env is None else env,
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
    """install-release refuses a manifest line that is not a timer name."""
    manifest = tmp_path / 'app-timers'
    manifest.write_text(f'paperroute-job.timer\n{line}\n', encoding='utf-8')
    result = release_functions(
        names=('manifest_timers',),
        program=f'SHARE={tmp_path}\nTIMERS=$(manifest_timers) || exit\n',
    )
    assert (result.returncode, result.stderr) == (
        2,
        'install-release: the timer manifest names a bad timer\n',
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
        for step in ('validate', 'contract_ok', 'build', 'check_calendars')
    ]
    assert steps == sorted(steps)
    assert steps[-1] < lines.index('    take_over')


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


@pytest.mark.parametrize('manifest', ('paperroute-job.timer\n', ''))
def test_install_release_sets_zone_only_for_manifest_timers(
    tmp_path: Path,
    manifest: str,
) -> None:
    """Only timers the manifest names get a zone drop-in."""
    etc = timer_etc(
        root=tmp_path,
        timers={
            'paperroute-job.timer': '*-*-* 03:30:00',
            'paperroute-other.timer': '*-*-* 04:00:00',
        },
    )
    (tmp_path / 'app-timers').write_text(manifest, encoding='utf-8')
    log = tmp_path / 'systemctl.log'
    result = release_functions(
        names=('manifest_timers', *ZONE_FUNCTIONS),
        program=(
            f'SHARE={tmp_path} ETC={etc} ZONE=Australia/Sydney\n'
            f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
            'TIMERS=$(manifest_timers) || exit\nset_timer_zones\n'
        ),
    )
    assert (result.returncode, result.stderr) == (0, '')
    zoned = sorted(str(path.relative_to(etc)) for path in etc.glob('*.d/*'))
    if manifest:
        assert zoned == ['paperroute-job.timer.d/app-zone.conf']
    else:
        assert not zoned
        assert not log.exists()


def test_install_release_asks_no_zone_without_timers(tmp_path: Path) -> None:
    """With an empty manifest build never asks the release for a zone."""
    tree = zone_stage(root=tmp_path, output='not a zone')
    program = (
        f'OWNER=me TREE={tree} OPT=/opt APP=paperroute-{APP_NAME}\n'
        f'TIMERS= ZONE=\n{ZONE_RE}\n'
        'runuser() { shift 3; "$@"; }\n'
        'build\nprintf "[%s]\\n" "$ZONE"\n'
    )
    result = run_functions(
        names=('build',),
        program=program,
        env=user_env(PATH=f'{tmp_path / "stubs"}:{os.environ["PATH"]}'),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        '[]\n',
        '',
    )


@pytest.mark.parametrize(
    ('calendars', 'restated'),
    (
        (None, ('Mon *-*-* 06:00:00 Australia/Sydney',)),
        (
            ('Mon *-*-* 06:00:00', 'Sat *-*-* 09:00 '),
            (
                'Mon *-*-* 06:00:00 Australia/Sydney',
                'Sat *-*-* 09:00 Australia/Sydney',
            ),
        ),
    ),
    ids=('fixture', 'two'),
)
def test_zone_drop_in_restates_the_timers_calendar(
    tmp_path: Path,
    calendars: tuple[str, ...] | None,
    restated: tuple[str, ...],
) -> None:
    """The drop-in clears the calendar, then restates each line zoned."""
    etc = tmp_path / 'etc'
    etc.mkdir()
    timer = etc / 'paperroute-job.timer'
    if calendars is None:
        shutil.copyfile(
            src=WITH_UNITS / 'deploy' / 'systemd' / timer.name,
            dst=timer,
        )
    else:
        timer.write_text(
            '[Timer]\n'
            + ''.join(f'OnCalendar={value}\n' for value in calendars)
            + '\n[Install]\nWantedBy=timers.target\n',
            encoding='utf-8',
        )
    result = release_functions(
        names=ZONE_FUNCTIONS,
        program=(
            f'ETC={etc}\nsystemctl() {{ :; }}\n'
            'set_timer_zone paperroute-job.timer Australia/Sydney\n'
        ),
    )
    assert (result.returncode, result.stderr) == (0, '')
    conf = etc / 'paperroute-job.timer.d' / 'app-zone.conf'
    assert conf.read_text(encoding='utf-8') == (
        '[Timer]\nOnCalendar=\n'
        + ''.join(f'OnCalendar={value}\n' for value in restated)
    )


@pytest.mark.parametrize(
    ('calendar', 'status'),
    (
        ('Mon *-*-* 06:00:00 Australia/Perth', 1),
        ('*-*-* 03:30:00 UTC', 1),
        ('*-*-* 03:30 America/Argentina/Buenos_Aires', 1),
        ('*-*-* 03:30:00', 0),
    ),
)
def test_zone_drop_in_refuses_a_timer_that_names_a_zone(
    tmp_path: Path,
    calendar: str,
    status: int,
) -> None:
    """A timer naming its own zone stops the deploy before take-over."""
    etc = timer_etc(root=tmp_path, timers={'paperroute-job.timer': calendar})
    result = release_functions(
        names=('calendar_of', 'check_calendars'),
        program=f'ETC={etc} TIMERS=paperroute-job.timer\ncheck_calendars\n',
    )
    assert result.returncode == status
    assert result.stderr == (
        'install-release: paperroute-job.timer already names a time zone;'
        " the app's timer must leave it to the device.\n"
        if status
        else ''
    )


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


@pytest.mark.parametrize(
    ('record', 'release', 'moved'),
    (
        ('sydney\n', None, 'sydney'),
        ('demo\n', 'paperroute-sydney\n', None),
        (None, 'paperroute-sydney\n', 'sydney'),
        (None, 'paperroute-demo\n', None),
        (None, None, None),
        ('../x\n', None, None),
    ),
)
def test_provision_sets_state_aside_for_another_app(
    *,
    tmp_path: Path,
    record: str | None,
    release: str | None,
    moved: str | None,
) -> None:
    """Another app's state file is renamed and kept before the new app."""
    share = tmp_path / 'share'
    current = tmp_path / 'current'
    data = tmp_path / 'data'
    for folder in (share, current, data):
        folder.mkdir()
    if record is not None:
        (share / 'installed-app').write_text(record, encoding='utf-8')
    if release is not None:
        (current / 'APP').write_text(release, encoding='utf-8')
    state = data / 'state.json'
    state.write_text('{}', encoding='utf-8')
    log = tmp_path / 'systemctl.log'
    result = provision_functions(
        names=('installed_app', 'set_state_aside'),
        program=(
            f'SHARE={share} CURRENT={current} STATE={state} APP_NAME=demo\n'
            f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
            f'date() {{ printf "1700000000\\n"; }}\n{MV_STUB}\n'
            'set_state_aside\n'
        ),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert (share / 'installed-app').read_text(encoding='utf-8') == 'demo\n'
    if moved is None:
        assert sorted(path.name for path in data.iterdir()) == ['state.json']
        assert not log.exists()
    else:
        assert sorted(path.name for path in data.iterdir()) == [
            f'state.json.{moved}-1700000000',
        ]
        assert log.read_text(encoding='utf-8') == 'stop paperroute\n'
