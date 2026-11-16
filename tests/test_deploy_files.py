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
SYDNEY_APP: Final[Path] = ROOT / 'apps' / 'paperroute-sydney'
SYDNEY_DEPLOY: Final[Path] = SYDNEY_APP / 'deploy'
SYDNEY_SYSTEMD: Final[Path] = SYDNEY_DEPLOY / 'systemd'
SYDNEY_BIN: Final[Path] = SYDNEY_DEPLOY / 'bin'
SYDNEY_SUDOERS: Final[Path] = SYDNEY_DEPLOY / 'sudoers'
BEFORE_RELEASE: Final[Path] = SYDNEY_APP / 'scripts' / 'before-release'
APP_CONF: Final[Path] = SYDNEY_SYSTEMD / 'paperroute.service.d' / 'app.conf'
FIXTURES: Final[Path] = Path(__file__).parent / 'fixtures'
WITH_UNITS: Final[Path] = FIXTURES / 'app_with_units'
WITHOUT_UNITS: Final[Path] = FIXTURES / 'app_without_units'
CONFIG_TXT: Final[Path] = FIXTURES / 'config.txt'
BIN_SCRIPTS: Final[tuple[str, ...]] = (
    'install-files',
    'install-release',
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
    SYDNEY_BIN / 'install-tiles',
    BEFORE_RELEASE,
)
SUDOERS_RULE: Final[str] = (
    'pashri ALL=(root) NOPASSWD: '
    '/usr/local/sbin/paperroute-install-release, '
    '/usr/local/sbin/paperroute-install-files ""'
)
TILES_RULE: Final[str] = (
    'pashri ALL=(root) NOPASSWD:'
    ' /usr/local/sbin/paperroute-sydney-install-tiles ""'
)
APP_RULE: Final[re.Pattern[str]] = re.compile(
    pattern=(
        r'^\S+ ALL=\(root\) NOPASSWD: '
        r'/usr/local/sbin/paperroute-[a-z][a-z0-9]*-[a-z][a-z0-9-]* ""$'
    ),
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
# The real install, without the owner and group a user cannot set.
OWNERLESS_INSTALL: Final[str] = (
    'install() { local a=(); while (($#)); do case $1 in'
    ' -o | -g) shift 2 ;; *) a+=("$1"); shift ;; esac; done;'
    ' command install "${a[@]}"; }'
)
# runuser and setsid run the command they are given, as this user.
SERVICE_USER_STUBS: Final[str] = (
    'setsid() { shift; "$@"; }\nrunuser() { shift 3; "$@"; }'
)
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
SYDNEY_DATA: Final[str] = '/var/lib/paperroute/apps/sydney'
SECRETS: Final[str] = f'{SYDNEY_DATA}/secrets'
TIMETABLE_ENVIRONMENT: Final[str] = (
    f'PAPERROUTE_KEY_FILE_TFNSW={SECRETS}'
    f' PAPERROUTE_API_KEY_FILE={SECRETS}'
    f' {FONTS_ENVIRONMENT}'
)
APP_ARGS: Final[tuple[str, ...]] = (
    '--timetable',
    f'{SYDNEY_DATA}/timetable.db',
    '--tiles',
    f'{SYDNEY_DATA}/tiles.db',
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
        ' --state /opt/paperroute/data/state.json'
        ' --error-log /opt/paperroute/data/log/errors.jsonl'
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
        f'/usr/bin/env TMPDIR={SYDNEY_DATA}/work'
        f' {VENV_BIN}/paperroute-build refresh'
        f' --data-dir {SYDNEY_DATA}'
        f' --error-log {SYDNEY_DATA}/log/errors.jsonl'
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
                ' --state /opt/paperroute/data/state.json'
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
    folder: Path = BIN,
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
    folder : Path
        The script's folder, ``deploy/bin`` unless given.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    return subprocess.run(
        args=['bash', str(folder / name), *args],
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
        f'--error-log {SYDNEY_DATA}/log/errors.jsonl'
    )
    assert service['ExecStart'].startswith(
        f'/usr/bin/env TMPDIR={SYDNEY_DATA}/work '
    )
    assert (service['Nice'], service['OOMScoreAdjust']) == ('19', '500')


def test_kit_units_reach_data_through_the_link() -> None:
    """No kit unit or the off hook names the service directory."""
    for path in (*(SYSTEMD / name for name in KIT_UNITS), OFF_HOOK):
        assert '/var/lib/paperroute/' not in script_text(path=path)


def test_sydney_units_name_their_own_directory() -> None:
    """Sydney's units use its directory, never the link."""
    for path in SYDNEY_SYSTEMD.rglob('*'):
        if not path.is_file():
            continue
        text = script_text(path=path)
        assert '/opt/paperroute/data' not in text
        for line in text.splitlines():
            for word in re.findall(r'/var/lib/paperroute\S*', line):
                assert (
                    line.strip() == 'ReadWritePaths=/var/lib/paperroute'
                    or word.startswith(f'{SYDNEY_DATA}/')
                    or word == SYDNEY_DATA
                ), (path, line)


def test_sydney_names_its_ready_unit() -> None:
    """The app ships the name of the unit that makes it ready."""
    assert (SYDNEY_DEPLOY / 'ready').read_text(encoding='utf-8') == (
        'paperroute-timetable.service\n'
    )


def sudoers_rules(*, path: Path) -> list[str]:
    """
    Read a sudoers file's lines that are neither blank nor comments.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    list[str]
        Its rules, in order.
    """
    return [
        line
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]


def test_sudoers_allows_exactly_the_two_kit_scripts(tmp_path: Path) -> None:
    """The kit's sudoers file holds one rule for its two root scripts."""
    path = DEPLOY / 'sudoers' / 'paperroute-deploy'
    assert sudoers_rules(path=path) == [SUDOERS_RULE]
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


def secrets_line() -> str:
    """
    Take the one line of provision's ``make_app_dirs`` that makes the key file.

    Returns
    -------
    str
        The line.
    """
    lines = [
        line
        for line in script_function(
            path=BIN / 'provision-device',
            name='make_app_dirs',
        ).splitlines()
        if 'secrets' in line
    ]
    assert len(lines) == 1
    return lines[0]


@pytest.mark.parametrize('path', SHELL_SCRIPTS, ids=lambda path: path.name)
def test_shell_script_never_names_the_key(path: Path) -> None:
    """No script names the key file, so none can handle or echo the key."""
    text = script_text(path=path)
    if path == BIN / 'provision-device':
        text = text.replace(secrets_line(), '')
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
    lines = script_function(
        path=BIN / 'provision-device',
        name='install_tools',
    ).splitlines()
    install = lines.index('    "$SBIN/paperroute-install-files"')
    assert lines.index('    retire_pin') > install


def test_provision_refuses_before_m4_saved_the_pin() -> None:
    """Provision stops before installing anything if state.json is missing."""
    lines = script_text(path=BIN / 'provision-device').splitlines()
    gate = lines.index(
        "[[ ! -e $PIN_FILE || -e /var/lib/paperroute/state.json ]] ||"
        " fail 'deploy M4 first so state.json holds the pin'"
    )
    assert gate < lines.index('copy_deploy')
    assert gate < lines.index('install_tools')


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
    app: str = 'demo',
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run provision-device over a staged copy as the current user.

    Parameters
    ----------
    copy : Path
        The staging directory.
    sudo_user : str | None
        The ``SUDO_USER`` to claim, or None to leave it unset.
    app : str
        The app to provision, demo unless given.
    env : dict[str, str] | None
        Variables to add, such as the folder overrides.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    extra = {} if sudo_user is None else {'SUDO_USER': sudo_user}
    extra |= env or {}
    script = copy / 'kit' / 'bin' / 'provision-device'
    return subprocess.run(
        args=['bash', str(script), str(copy), app],
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
    """runuser in provision gets no terminal and never the install lock."""
    found = commands(path=BIN / 'provision-device', word='runuser')
    assert len(found) == 5
    checked = [
        command
        for command in found
        if command.startswith('if ! setsid -w runuser -u paperroute -- ')
    ]
    assert len(checked) == 4
    for command in checked:
        assert command.endswith(' </dev/null >/dev/null 2>&1 9>&-; then')
    assert set(found) - set(checked) == {
        'left=$(setsid -w runuser -u paperroute -- ls -A -- "$VAR"'
        ' </dev/null 2>/dev/null 9>&-) || left=',
    }
    for name, count in (('make_app_dirs', 2), ('move_data', 3)):
        assert script_function(
            path=BIN / 'provision-device',
            name=name,
        ).count('runuser') == count


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


TILES_SCRIPT: Final[Path] = SYDNEY_BIN / 'install-tiles'
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
        folder=SYDNEY_BIN,
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
    result = run_script(
        name='install-tiles',
        args=(),
        env=env,
        folder=SYDNEY_BIN,
    )
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


def test_before_release_checks_free_disk_before_tile_upload() -> None:
    """Sydney's hook reads the Pi's free space before the tile rsync."""
    text = BEFORE_RELEASE.read_text(encoding='utf-8')
    rsync = text.index('rsync -a --partial data/tiles.db')
    assert 0 < text.index('df -Pk') < rsync
    assert text.count('^(0|[1-9][0-9]*)$') >= 1
    assert (
        'for value in "$free" "$staged_size" "$staged_mtime" "$size" '
        '"$mtime"; do\n    [[ $value =~ $NUMBER ]]'
    ) in text
    assert text.index('=~ $NUMBER') < rsync
    assert 'not enough free disk on the Pi for the tiles' in text


def test_before_release_only_warns_on_tiles() -> None:
    """Sydney's hook checks the tiles before any ssh and never stops."""
    lines = BEFORE_RELEASE.read_text(encoding='utf-8').splitlines()
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
    assert lines[check] == (
        'uv run python -m paperroute_sydney.build check-tiles data/tiles.db '
        "|| printf 'deploy: warning: tiles do not match this release; "
        "deploying anyway\\n' >&2"
    )
    assert 'exit' not in lines[check]
    assert lines[check - 1] == '[[ -f data/tiles.db ]] || exit 0'


def test_before_release_reports_a_failed_tile_size_read() -> None:
    """An ssh failure reading sizes reaches the number check's message."""
    text = BEFORE_RELEASE.read_text(encoding='utf-8')
    assert "{ print $4 }') \\\n    || free=\n" in text
    assert "|| echo '0 0'\") \\\n    || remote=\n" in text
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


def test_provision_installs_only_the_kit_tools() -> None:
    """provision installs the kit's two tools; the tiles are Sydney's."""
    script = BIN / 'provision-device'
    assert script_value(path=script, name='KIT_TOOLS') == (
        "'install-release install-files'"
    )
    assert 'tiles' not in script_text(path=script)
    assert commands(path=script, word='install -m 0755') == [
        'install -m 0755 -o root -g root "$path"'
        ' "$SBIN/paperroute-$app-$name" || exit 1',
        'install -m 0755 -o root -g root "$SHARE/deploy/bin/$name"'
        ' "$SBIN/paperroute-$name"',
    ]


def test_sydney_ships_install_tiles_and_its_rule() -> None:
    """Sydney's deploy/ holds the tiles tool and the one rule for it."""
    assert sorted(path.name for path in SYDNEY_BIN.iterdir()) == [
        'install-tiles',
    ]
    assert sorted(path.name for path in SYDNEY_SUDOERS.iterdir()) == [
        'paperroute-sydney',
    ]


def test_kit_deploy_has_no_tile_tooling() -> None:
    """No kit file, and neither kit script, names the tiles."""
    paths = [
        *(path for path in sorted(DEPLOY.rglob('*')) if path.is_file()),
        DEPLOY_SCRIPT,
        ROOT / 'scripts' / 'provision',
    ]
    named = [
        str(path.relative_to(ROOT))
        for path in paths
        if isinstance(file_content(path=path), str)
        and re.search(pattern=r'(?i)tiles', string=script_text(path=path))
    ]
    assert len(paths) > 10
    assert not named


def test_before_release_installs_tiles_after_staging_them() -> None:
    """A failed tiles install fails the hook; it stages before installing."""
    text = script_text(path=BEFORE_RELEASE)
    assert commands(path=BEFORE_RELEASE, word='TILES') == [
        'TILES=/usr/local/sbin/paperroute-install-tiles',
        'if ! ssh -- "$host" sudo -n -l "$TILES" >/dev/null 2>&1; then',
        'ssh -- "$host" sudo -n "$TILES" || exit',
    ]
    order = [
        text.index(part)
        for part in (
            "check-tiles data/tiles.db",
            'if ! ssh -- "$host" sudo -n -l "$TILES"',
            "printf 'deploy: run scripts/provision first (tiles)\\n' >&2",
            'ssh -- "$host" mkdir -p "$STAGE/tiles"',
            'rsync -a --partial data/tiles.db "$host:$STAGE/tiles/tiles.db"',
            'sudo -n "$TILES" || exit',
        )
    ]
    assert order == sorted(order)
    assert script_value(path=BEFORE_RELEASE, name='STAGE') == (
        script_value(path=DEPLOY_SCRIPT, name='STAGE')
    )


@dataclass(frozen=True)
class DeployRepo:
    """A committed tree for scripts/deploy, with ssh and rsync stubbed."""

    root: Path
    hook: Path
    log: Path
    env: dict[str, str]


@pytest.fixture(name='deploy_repo')
def fixture_deploy_repo(tmp_path: Path) -> DeployRepo:
    """
    Make a tree holding scripts/deploy, a passing check and one app.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    DeployRepo
        The tree, where the app's hook goes, the stub log and the
        environment that puts the stubs first.
    """
    repo = tmp_path / 'repo'
    scripts = repo / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy2(src=DEPLOY_SCRIPT, dst=scripts / 'deploy')
    (scripts / 'check').write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    (scripts / 'check').chmod(0o755)
    app = repo / 'apps' / 'paperroute-demo'
    app.mkdir(parents=True)
    (app / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
    (repo / '.gitignore').write_text('data/\n', encoding='utf-8')
    (repo / 'data').mkdir()
    (repo / 'data' / 'tiles.db').write_text('tiles\n', encoding='utf-8')
    log = tmp_path / 'calls.log'
    stubs = stub_commands(
        root=tmp_path,
        scripts={
            'ssh': f'#!/bin/sh\nprintf "ssh %s\\n" "$*" >>{log}\n',
            'rsync': (
                f'#!/bin/sh\ncat >/dev/null\nprintf "rsync %s\\n" "$*"'
                f' >>{log}\n'
            ),
        },
    )
    return DeployRepo(
        root=repo,
        hook=app / 'scripts' / 'before-release',
        log=log,
        env=user_env(PATH=f'{stubs}:{os.environ["PATH"]}'),
    )


def write_hook(*, repo: DeployRepo, status: int, mode: int) -> None:
    """
    Give the app a before-release hook that logs its arguments.

    Parameters
    ----------
    repo : DeployRepo
        The tree.
    status : int
        The hook's exit status.
    mode : int
        The hook's permission bits.
    """
    repo.hook.parent.mkdir()
    repo.hook.write_text(
        f'printf "hook %s\\n" "$*" >>{repo.log}\nexit {status}\n',
        encoding='utf-8',
    )
    repo.hook.chmod(mode)


def run_deploy(*, repo: DeployRepo) -> tuple[int, list[str], str]:
    """
    Commit the tree and run scripts/deploy for its app.

    Parameters
    ----------
    repo : DeployRepo
        The tree.

    Returns
    -------
    tuple[int, list[str], str]
        Deploy's exit status, the stub calls in order, and the short sha.
    """
    git = [
        'git',
        '-c', 'user.email=test@example.com',
        '-c', 'user.name=Test',
        '-c', 'commit.gpgsign=false',
        '-c', 'core.hooksPath=/dev/null',
    ]
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'x']):
        subprocess.run(
            args=[*git, *args],
            cwd=repo.root,
            check=True,
            timeout=30,
        )
    sha = subprocess.run(
        args=['git', 'rev-parse', '--short', 'HEAD'],
        cwd=repo.root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    result = subprocess.run(
        args=['bash', str(repo.root / 'scripts' / 'deploy'), 'host', 'demo'],
        env=repo.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    calls = (
        repo.log.read_text(encoding='utf-8').splitlines()
        if repo.log.exists()
        else []
    )
    return result.returncode, calls, sha


def test_deploy_runs_the_apps_before_release(
    deploy_repo: DeployRepo,
) -> None:
    """The hook runs with the host after staging, before install-release."""
    write_hook(repo=deploy_repo, status=0, mode=0o755)
    status, calls, sha = run_deploy(repo=deploy_repo)
    assert status == 0
    assert calls[-3].startswith('rsync -a --from0 --files-from=- ./ host:')
    assert calls[-2:] == [
        'hook host',
        f'ssh -- host sudo -n /usr/local/sbin/paperroute-install-release'
        f' {sha} demo',
    ]


def test_deploy_runs_before_release_without_its_executable_bit(
    deploy_repo: DeployRepo,
) -> None:
    """A hook that lost its executable bit still runs, through bash."""
    write_hook(repo=deploy_repo, status=0, mode=0o644)
    _, calls, _ = run_deploy(repo=deploy_repo)
    assert 'hook host' in calls
    assert commands(path=DEPLOY_SCRIPT, word='HOOK') == [
        'HOOK=scripts/before-release',
        'if [[ -f apps/paperroute-$app/$HOOK ]]; then',
        'bash "apps/paperroute-$app/$HOOK" "$host" || exit',
    ]


def test_deploy_stops_when_before_release_fails(
    deploy_repo: DeployRepo,
) -> None:
    """A failing hook ends the deploy with its status, before the install."""
    write_hook(repo=deploy_repo, status=3, mode=0o755)
    status, calls, _ = run_deploy(repo=deploy_repo)
    assert status == 3
    assert calls[-1] == 'hook host'
    assert not any('install-release' in call for call in calls[1:])


def test_deploy_without_before_release_skips_tiles(
    deploy_repo: DeployRepo,
) -> None:
    """An app with no hook deploys without touching data/tiles.db."""
    status, calls, sha = run_deploy(repo=deploy_repo)
    assert status == 0
    assert not any('tiles' in call for call in calls)
    assert calls[-1] == (
        f'ssh -- host sudo -n /usr/local/sbin/paperroute-install-release'
        f' {sha} demo'
    )


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


def make_app_dirs(*, root: Path) -> subprocess.CompletedProcess[str]:
    """
    Run provision's ``make_app_dirs`` for demo over temp folders.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory; ``opt`` and ``var`` go here.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    return provision_functions(
        names=('make_dir', 'make_app_dirs'),
        program=(
            f'{OWNERLESS_INSTALL}\n{SERVICE_USER_STUBS}\n'
            f'OPT={root / "opt"}\nVAR={root / "var"}\nAPP_NAME=demo\n'
            'make_app_dirs\n'
        ),
    )


def test_provision_creates_empty_secrets(tmp_path: Path) -> None:
    """The service user makes an empty private key file, keeping a key."""
    assert 'umask 077' in secrets_line()
    result = make_app_dirs(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    home = tmp_path / 'var' / 'apps' / 'demo'
    key = home / 'secrets'
    assert (key.read_bytes(), key.stat().st_mode & 0o777) == (b'', 0o600)
    dirs = (home.parent, home, home / 'log', home / 'sources', home / 'work')
    assert {path.stat().st_mode & 0o777 for path in dirs} == {0o750}
    assert (tmp_path / 'opt' / 'apps' / 'demo' / 'releases').is_dir()
    key.write_bytes(b'key')
    result = make_app_dirs(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    assert key.read_bytes() == b'key'


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
            '/opt/paperroute/data/state.json',
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


COPY_FUNCTIONS: Final[tuple[str, ...]] = (
    'stage_copy',
    'move_in',
    'copy_tree',
    'copy_deploy',
)
SUDOERS_FUNCTIONS: Final[tuple[str, ...]] = (
    'installed_apps',
    'app_tool_names',
    'allowed_sudoers_line',
    'sudoers_file',
    'sudoers_text',
    'install_sudoers',
)


def rule(app: str) -> str:
    """
    Give an app's sudoers rule for its install-tiles tool.

    Parameters
    ----------
    app : str
        The app.

    Returns
    -------
    str
        The rule, naming the shipped default user.
    """
    return (
        'pashri ALL=(root) NOPASSWD:'
        f' /usr/local/sbin/paperroute-{app}-install-tiles ""'
    )


def app_copy(
    *,
    folder: Path,
    tools: tuple[str, ...] = (),
    lines: str | None = None,
) -> None:
    """
    Write an app's deploy copy: tools in ``bin`` and one sudoers file.

    Parameters
    ----------
    folder : Path
        The copy, such as ``share/apps/demo``.
    tools : tuple[str, ...]
        The tools it ships.
    lines : str | None
        Its one sudoers file, or None for none.
    """
    (folder / 'bin').mkdir(parents=True, exist_ok=True)
    for tool in tools:
        (folder / 'bin' / tool).write_text(f'{tool}\n', encoding='utf-8')
    if lines is not None:
        (folder / 'sudoers').mkdir(exist_ok=True)
        (folder / 'sudoers' / f'paperroute-{folder.name}').write_text(
            lines,
            encoding='utf-8',
        )


def install_sudoers(
    *,
    root: Path,
    user: str,
    app_lines: str | None = None,
    kit_lines: str | None = None,
    tools: str = 'install-tiles\n',
    visudo: str = 'visudo() { return 0; }',
) -> subprocess.CompletedProcess[str]:
    """
    Run provision's ``install_sudoers`` for a deploy user.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory; the share and sudoers.d go here.
    user : str
        The deploy user.
    app_lines : str | None
        Demo's one sudoers file, or None for Sydney's, installed as
        sydney.
    kit_lines : str | None
        The kit's sudoers file, or None for the shipped one.
    tools : str
        The tools the app ships, one per line.
    visudo : str
        The stand-in for visudo, as a shell function.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output; the file lands in ``root/sudoers.d``.
    """
    share = root / 'share'
    (share / 'deploy' / 'sudoers').mkdir(parents=True)
    (share / 'deploy' / 'sudoers' / 'paperroute-deploy').write_text(
        (
            (DEPLOY / 'sudoers' / 'paperroute-deploy').read_text(
                encoding='utf-8',
            )
            if kit_lines is None
            else kit_lines
        ),
        encoding='utf-8',
    )
    app = 'sydney' if app_lines is None else 'demo'
    if app_lines is None:
        shutil.copytree(
            src=SYDNEY_SUDOERS,
            dst=share / 'apps' / app / 'sudoers',
        )
    app_copy(
        folder=share / 'apps' / app,
        tools=tuple(tools.split()),
        lines=app_lines,
    )
    (share / 'active-app').write_text(f'{app}\n', encoding='utf-8')
    sudoers = root / 'sudoers.d' / 'paperroute-deploy'
    sudoers.parent.mkdir()
    program = (
        f'{visudo}\n{MV_STUB}\n'
        f'SHARE={share}\nSUDOERS={sudoers}\n'
        f'SUDOERS_NEW={sudoers.parent}/.paperroute-deploy.new\n'
        f'DEPLOY_USER={user}\ninstall_sudoers\n'
    )
    return provision_functions(
        names=SUDOERS_FUNCTIONS,
        program=program,
    )


def sudoers_files(*, root: Path) -> list[str]:
    """
    List what install_sudoers left in the sandbox's sudoers.d.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.

    Returns
    -------
    list[str]
        The file names, sorted.
    """
    return sorted(path.name for path in (root / 'sudoers.d').iterdir())


@pytest.mark.parametrize('user', ('pashri', 'alice'))
def test_provision_grants_sudo_to_the_deploy_user(
    tmp_path: Path,
    user: str,
) -> None:
    """The installed rules name the user who ran provision, read-only."""
    result = install_sudoers(root=tmp_path, user=user)
    assert (result.returncode, result.stderr) == (0, '')
    sudoers = tmp_path / 'sudoers.d' / 'paperroute-deploy'
    assert sudoers_rules(path=sudoers) == [
        SUDOERS_RULE.replace('pashri ', f'{user} ', 1),
        TILES_RULE.replace('pashri ', f'{user} ', 1),
    ]
    assert sudoers.stat().st_mode & 0o777 == 0o440
    assert sudoers_files(root=tmp_path) == ['paperroute-deploy']


def test_app_sudoers_names_only_provisioned_tools() -> None:
    """Sydney's one rule names a tool its own deploy/bin ships."""
    rules = sudoers_rules(path=SYDNEY_SUDOERS / 'paperroute-sydney')
    assert rules == [TILES_RULE]
    shipped = {path.name for path in SYDNEY_BIN.iterdir()}
    named = {
        found.group(1)
        for line in rules
        for found in re.finditer(
            pattern=r'/usr/local/sbin/paperroute-sydney-([a-z0-9-]+)',
            string=line,
        )
    }
    assert named == {'install-tiles'} <= shipped


@pytest.mark.parametrize(
    ('app_lines', 'tools'),
    (
        (None, 'install-tiles\n'),
        (
            '# The job.\n\n'
            'pashri ALL=(root) NOPASSWD:'
            ' /usr/local/sbin/paperroute-demo-job ""\n',
            'job\n',
        ),
    ),
    ids=('sydney', 'fixture'),
)
def test_no_sudoers_rule_points_into_releases(
    tmp_path: Path,
    app_lines: str | None,
    tools: str,
) -> None:
    """Every rendered rule is the kit's or one installed tool's."""
    result = install_sudoers(
        root=tmp_path,
        user='alice',
        app_lines=app_lines,
        tools=tools,
    )
    assert (result.returncode, result.stderr) == (0, '')
    rules = sudoers_rules(path=tmp_path / 'sudoers.d' / 'paperroute-deploy')
    kit = SUDOERS_RULE.replace('pashri ', 'alice ', 1)
    assert rules[0] == kit
    assert len(rules) == 2
    assert all(line == kit or APP_RULE.match(line) for line in rules)
    assert not any('/opt/paperroute/releases' in line for line in rules)
    shipped = [
        *sudoers_rules(path=DEPLOY / 'sudoers' / 'paperroute-deploy'),
        *sudoers_rules(path=SYDNEY_SUDOERS / 'paperroute-sydney'),
    ]
    assert not any('/opt/paperroute/releases' in line for line in shipped)


def assert_line_refused(*, root: Path, line: str, tools: str) -> None:
    """
    Check install_sudoers refuses an app line and writes no file.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    line : str
        The app's one sudoers line.
    tools : str
        The app tools just installed, one per line.
    """
    result = install_sudoers(
        root=root,
        user='alice',
        app_lines=f'# A comment.\n{line}\n',
        tools=tools,
    )
    rendered = line.replace('pashri ', 'alice ', 1) if line.startswith(
        'pashri ',
    ) else line
    assert (result.returncode, result.stderr) == (
        1,
        f'provision-device: sudoers line not allowed: {rendered}\n',
    )
    assert sudoers_files(root=root) == []


@pytest.mark.parametrize(
    ('line', 'shown'),
    (
        (
            f'{rule("demo")}\r',
            "$'alice ALL=(root) NOPASSWD:"
            " /usr/local/sbin/paperroute-demo-install-tiles \"\"\\r'",
        ),
        ('Defaults\tenv_keep += X', "$'Defaults\\tenv_keep += X'"),
    ),
    ids=('carriage-return', 'tab'),
)
def test_install_sudoers_shows_control_characters(
    tmp_path: Path,
    line: str,
    shown: str,
) -> None:
    """A refused line with a control character is printed quoted."""
    result = install_sudoers(
        root=tmp_path,
        user='alice',
        app_lines=f'{line}\n',
    )
    assert (result.returncode, result.stderr) == (
        1,
        f'provision-device: sudoers line not allowed: {shown}\n',
    )
    assert sudoers_files(root=tmp_path) == []


def test_rendered_sudoers_explains_the_user_once(tmp_path: Path) -> None:
    """Only the kit's comment says provision swaps in the deploy user."""
    result = install_sudoers(root=tmp_path, user='alice')
    assert (result.returncode, result.stderr) == (0, '')
    text = (tmp_path / 'sudoers.d' / 'paperroute-deploy').read_text(
        encoding='utf-8',
    )
    assert text.count('swaps pashri') == 1


def test_install_sudoers_refuses_a_release_path(tmp_path: Path) -> None:
    """A rule naming a release's own script is refused."""
    assert_line_refused(
        root=tmp_path,
        line='pashri ALL=(root) NOPASSWD: /opt/paperroute/releases/x/bin/y ""',
        tools='install-tiles\n',
    )


@pytest.mark.parametrize(
    'line',
    (
        'Defaults !env_reset',
        '@includedir /home/x',
        '@include /home/x/rules',
        '#include /home/x/rules',
        '#includedir /home/x',
        'Cmnd_Alias X = /bin/sh',
        'pashri ALL=(ALL) NOPASSWD: ALL',
        'pashri ALL=(root) NOPASSWD:'
        ' /usr/local/sbin/paperroute-demo-install-tiles'
        ' "", /usr/local/sbin/paperroute-demo-job ""',
        'pashri ALL=(root) NOPASSWD: /usr/local/sbin/paperroute-install-release'
        ' ""',
        rule('demo').replace('pashri ', 'root ', 1),
        rule('demo').replace('pashri ', '#1000 ', 1),
        '# a comment that continues \\',
    ),
)
def test_install_sudoers_refuses_defaults_include_and_all(
    tmp_path: Path,
    line: str,
) -> None:
    """Only a comment, a blank or a one-tool rule passes; all else stops."""
    assert_line_refused(
        root=tmp_path,
        line=line,
        tools='install-tiles\njob\n',
    )


def test_app_tool_needs_no_args_rule(tmp_path: Path) -> None:
    """A rule without the empty-argument marker lets any argument through."""
    assert_line_refused(
        root=tmp_path,
        line=rule('demo').removesuffix(' ""'),
        tools='install-tiles\n',
    )


def test_app_sudoers_must_name_an_installed_tool(tmp_path: Path) -> None:
    """A rule for a tool the app does not ship is refused."""
    assert_line_refused(
        root=tmp_path,
        line=rule('demo'),
        tools='job\n',
    )


def test_install_sudoers_refuses_a_widened_kit_rule(tmp_path: Path) -> None:
    """The kit's own file may hold only the kit rule and comments."""
    result = install_sudoers(
        root=tmp_path,
        user='alice',
        kit_lines=f'{SUDOERS_RULE}\npashri ALL=(ALL) NOPASSWD: ALL\n',
    )
    assert (result.returncode, result.stderr) == (
        1,
        'provision-device: sudoers line not allowed:'
        ' alice ALL=(ALL) NOPASSWD: ALL\n',
    )
    assert sudoers_files(root=tmp_path) == []


@pytest.mark.parametrize('status', (0, 1))
def test_install_sudoers_runs_visudo(tmp_path: Path, status: int) -> None:
    """visudo checks the new file; its failure leaves no file behind."""
    log = tmp_path / 'visudo.log'
    result = install_sudoers(
        root=tmp_path,
        user='alice',
        visudo=f'visudo() {{ printf "%s\\n" "$*" >>{log}; return {status}; }}',
    )
    new = tmp_path / 'sudoers.d' / '.paperroute-deploy.new'
    assert log.read_text(encoding='utf-8') == f'-cf {new}\n'
    if status:
        assert (result.returncode, result.stderr) == (
            1,
            'provision-device: sudoers check failed\n',
        )
        assert sudoers_files(root=tmp_path) == []
    else:
        assert result.returncode == 0
        assert sudoers_files(root=tmp_path) == ['paperroute-deploy']


INSTALL_STUB: Final[str] = (
    'install() { while [[ $1 == -* ]]; do shift 2; done; cp -- "$1" "$2"; }'
)


def run_app_tools(
    *,
    root: Path,
    shipped: dict[str, str],
    installed: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """
    Run provision's ``install_app_tools`` over a share and an sbin folder.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    shipped : dict[str, str]
        Each file in demo's ``bin`` in the share and its text.
    installed : dict[str, str]
        Each file already in sbin and its text.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output; sbin is ``root/sbin``.
    """
    app_bin = root / 'share' / 'apps' / 'demo' / 'bin'
    app_bin.mkdir(parents=True)
    for name, text in shipped.items():
        (app_bin / name).write_text(text, encoding='utf-8')
    (root / 'share' / 'active-app').write_text('demo\n', encoding='utf-8')
    sbin = root / 'sbin'
    sbin.mkdir()
    for name, text in installed.items():
        (sbin / name).write_text(text, encoding='utf-8')
    tree = root / 'tree' / 'app' / 'bin'
    tree.mkdir(parents=True)
    for name in shipped:
        (tree / name).write_text('tree copy\n', encoding='utf-8')
    program = (
        f'{INSTALL_STUB}\nSHARE={root / "share"}\nDIR={root / "tree"}\n'
        f'SBIN={sbin}\ninstall_app_tools\n'
    )
    return provision_functions(
        names=('installed_apps', 'app_tool_names', 'install_app_tools'),
        program=program,
    )


KIT_INSTALLED: Final[dict[str, str]] = {
    'paperroute-install-release': 'kit release\n',
    'paperroute-install-files': 'kit files\n',
}


def test_app_tools_come_from_the_provisioned_copy(tmp_path: Path) -> None:
    """Tools install from the root-owned share, never the staged tree."""
    result = run_app_tools(
        root=tmp_path,
        shipped={'install-tiles': 'share copy\n'},
        installed=KIT_INSTALLED,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')
    assert tree_files(root=tmp_path / 'sbin') == KIT_INSTALLED | {
        'paperroute-demo-install-tiles': 'share copy\n',
    }
    names, install = (
        script_function(path=BIN / 'provision-device', name=name)
        for name in ('app_tool_names', 'install_app_tools')
    )
    assert '$DIR' not in names + install
    assert '"$app"/bin/' in names
    assert 'APP_COPY=$SHARE/apps/$APP_NAME.new check_app_part' in (
        script_function(
            path=BIN / 'provision-device',
            name='take_app_copy',
        )
    )
    assert 'path=$SHARE/apps/$app/bin/$name' in install


@pytest.mark.parametrize('kit', ('install-release', 'install-files'))
def test_app_tool_cannot_replace_a_kit_tool(tmp_path: Path, kit: str) -> None:
    """An app tool named like a kit tool stops provision, installing nothing."""
    result = run_app_tools(
        root=tmp_path,
        shipped={'install-tiles': 'tiles\n', kit: 'app copy\n'},
        installed=KIT_INSTALLED,
    )
    assert (result.returncode, result.stderr) == (
        1,
        f'provision-device: app tool {kit} is not allowed\n',
    )
    assert tree_files(root=tmp_path / 'sbin') == KIT_INSTALLED


@pytest.mark.parametrize('name', ('Bad Name', '..x', '.x', 'x_y', '9x', '-x'))
def test_app_tool_name_is_validated(tmp_path: Path, name: str) -> None:
    """A tool name outside lowercase words and hyphens stops provision."""
    result = run_app_tools(
        root=tmp_path,
        shipped={'install-tiles': 'tiles\n', name: 'odd\n'},
        installed=KIT_INSTALLED,
    )
    assert (result.returncode, result.stderr) == (
        1,
        f'provision-device: app tool {name} is not allowed\n',
    )
    assert tree_files(root=tmp_path / 'sbin') == KIT_INSTALLED


def test_stale_app_tools_are_removed(tmp_path: Path) -> None:
    """A paperroute tool nobody ships goes; kit tools and others stay."""
    result = run_app_tools(
        root=tmp_path,
        shipped={'job': 'new job\n'},
        installed=KIT_INSTALLED | {
            'paperroute-install-tiles': 'old tiles\n',
            'paperroute-job': 'old job\n',
            'paperroute-demo-job': 'old job\n',
            'other-tool': 'other\n',
        },
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert tree_files(root=tmp_path / 'sbin') == KIT_INSTALLED | {
        'paperroute-demo-job': 'new job\n',
        'other-tool': 'other\n',
    }


INSTALL_MODE_STUB: Final[str] = (
    'install() { while [[ $1 == -* ]]; do shift 2; done;'
    ' cp -- "$1" "$2"; chmod 0755 "$2"; }'
)


def install_files_stub(*, root: Path) -> str:
    """
    Give the text of a kit install-files that only logs its run.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory, which holds the log.

    Returns
    -------
    str
        The script.
    """
    return f'#!/bin/sh\necho install-files >>{root / "calls.log"}\n'


def provision_tools(
    *,
    root: Path,
    shipped: dict[str, str],
    app_lines: str,
) -> subprocess.CompletedProcess[str]:
    """
    Run provision's app checks, then its tool and sudoers installs.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory; ``share``, ``sbin`` and
        ``sudoers.d`` go here, the last two holding an earlier install.
    shipped : dict[str, str]
        Each file in demo's ``bin`` in the share and its text.
    app_lines : str
        Demo's one sudoers file.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output; calls are logged to ``calls.log``.
    """
    share = root / 'share'
    shutil.copytree(src=DEPLOY / 'sudoers', dst=share / 'deploy' / 'sudoers')
    kit_bin = share / 'deploy' / 'bin'
    kit_bin.mkdir()
    (kit_bin / 'install-release').write_text('new release\n', encoding='utf-8')
    (kit_bin / 'install-files').write_text(
        install_files_stub(root=root),
        encoding='utf-8',
    )
    (kit_bin / 'install-files').chmod(0o755)
    demo = share / 'apps' / 'demo'
    (demo / 'bin').mkdir(parents=True)
    for name, text in shipped.items():
        (demo / 'bin' / name).write_text(text, encoding='utf-8')
    app_copy(folder=demo, lines=app_lines)
    (share / 'active-app').write_text('demo\n', encoding='utf-8')
    sbin = root / 'sbin'
    sbin.mkdir()
    for name, text in KIT_INSTALLED.items():
        (sbin / name).write_text(text, encoding='utf-8')
    sudoers = root / 'sudoers.d' / 'paperroute-deploy'
    sudoers.parent.mkdir()
    sudoers.write_text('old rules\n', encoding='utf-8')
    log = root / 'calls.log'
    program = (
        f'{INSTALL_MODE_STUB}\n'
        f'{MV_STUB}\nmake_dir() {{ :; }}\n'
        f'retire_pin() {{ echo retire_pin >>{log}; }}\n'
        f'visudo() {{ echo visudo >>{log}; }}\n'
        f'SHARE={share}\nSBIN={sbin}\nSUDOERS={sudoers}\n'
        f'SUDOERS_NEW={sudoers.parent}/.paperroute-deploy.new\n'
        'DEPLOY_USER=alice\ncheck_app_part\ninstall_tools\n'
    )
    return provision_functions(
        names=(
            *SUDOERS_FUNCTIONS,
            'check_app_part',
            'install_app_tools',
            'install_tools',
        ),
        program=program,
    )


@pytest.mark.parametrize(
    ('shipped', 'app_lines', 'message'),
    (
        (
            {'install-tiles': 'new tiles\n'},
            f'{rule("demo")}\nDefaults !env_reset\n',
            'sudoers line not allowed: Defaults !env_reset',
        ),
        (
            {'install-tiles': 'new tiles\n', 'Bad Name': 'odd\n'},
            f'{rule("demo")}\n',
            'app tool Bad Name is not allowed',
        ),
    ),
    ids=('sudoers-line', 'tool-name'),
)
def test_a_refused_app_part_installs_nothing(
    tmp_path: Path,
    shipped: dict[str, str],
    app_lines: str,
    message: str,
) -> None:
    """A refused line or tool name leaves sbin and the old sudoers as is."""
    result = provision_tools(
        root=tmp_path,
        shipped=shipped,
        app_lines=app_lines,
    )
    assert (result.returncode, result.stderr) == (
        1,
        f'provision-device: {message}\n',
    )
    assert tree_files(root=tmp_path / 'sbin') == KIT_INSTALLED
    assert tree_files(root=tmp_path / 'sudoers.d') == {
        'paperroute-deploy': 'old rules\n',
    }
    assert not (tmp_path / 'calls.log').exists()


def test_an_allowed_app_part_installs_tools_then_sudoers(
    tmp_path: Path,
) -> None:
    """The kit's tools, the app's, install-files, then the sudoers file."""
    result = provision_tools(
        root=tmp_path,
        shipped={'install-tiles': 'new tiles\n'},
        app_lines=f'{rule("demo")}\n',
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert tree_files(root=tmp_path / 'sbin') == {
        'paperroute-install-release': 'new release\n',
        'paperroute-install-files': install_files_stub(root=tmp_path),
        'paperroute-demo-install-tiles': 'new tiles\n',
    }
    assert sudoers_rules(
        path=tmp_path / 'sudoers.d' / 'paperroute-deploy',
    ) == [
        SUDOERS_RULE.replace('pashri ', 'alice ', 1),
        rule('demo').replace('pashri ', 'alice ', 1),
    ]
    assert (tmp_path / 'calls.log').read_text(encoding='utf-8') == (
        'install-files\nretire_pin\nvisudo\n'
    )


@pytest.mark.parametrize(
    ('tool', 'status', 'stderr'),
    (
        ('Bad Name', 1, 'provision-device: app tool Bad Name is not allowed\n'),
        ('install-job', 0, ''),
    ),
    ids=('refused', 'allowed'),
)
def test_provision_moves_the_app_in_only_once_checked(
    *,
    tmp_path: Path,
    tool: str,
    status: int,
    stderr: str,
) -> None:
    """A refused app part leaves the old copy and no staged one behind."""
    staging = copy_deploy(tmp_path=tmp_path, app=WITH_UNITS)
    (staging / 'app' / 'bin').mkdir(exist_ok=True)
    (staging / 'app' / 'bin' / tool).write_text('new\n', encoding='utf-8')
    share = tmp_path / 'share'
    (share / 'apps' / 'demo').mkdir(parents=True)
    (share / 'apps' / 'demo' / 'old').write_text('old\n', encoding='utf-8')
    result = provision_functions(
        names=(
            'make_dir',
            *COPY_FUNCTIONS,
            *SUDOERS_FUNCTIONS[:-1],
            'check_app_part',
            'take_app_copy',
        ),
        program=(
            f'chown() {{ :; }}\n{OWNERLESS_INSTALL}\n{MV_STUB}\n'
            f'SHARE={share}\nDIR={staging}\nAPP_NAME=demo\n'
            'DEPLOY_USER=alice\ncopy_deploy\ntake_app_copy\n'
        ),
    )
    assert (result.returncode, result.stderr) == (status, stderr)
    assert sorted(path.name for path in share.iterdir()) == ['apps', 'deploy']
    assert sorted(path.name for path in (share / 'apps').iterdir()) == [
        'demo',
    ]
    if status:
        assert tree_files(root=share / 'apps' / 'demo') == {'old': 'old\n'}
    else:
        assert tree_files(root=share / 'apps' / 'demo') == tree_files(
            root=staging / 'app',
        )


def test_provision_checks_the_app_part_before_any_root_change() -> None:
    """The checks run right after the share copy, before state or tools."""
    lines = script_text(path=BIN / 'provision-device').splitlines()
    order = [
        lines.index(line)
        for line in (
            'copy_deploy',
            'take_app_copy',
            'record_active',
            'make_app_dirs',
            'install_tools',
        )
    ]
    assert order == sorted(order)
    assert script_value(path=BIN / 'provision-device', name='SBIN') == (
        '/usr/local/sbin'
    )


def test_provision_names_no_deploy_user() -> None:
    """Only the shipped sudoers default names pashri; provision does not."""
    text = script_text(path=BIN / 'provision-device')
    assert 'pashri' not in text.replace('${line/#pashri /', '')
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
    'fail',
    'active_app',
    'point_links',
    'in_list',
    'adopt_city_zones',
    'wanted_units',
    'shipped_drop_ins',
    'app_timers',
    'install_units',
    'remove_strays',
    'enable_units',
)
# How install-files' main finds the active app's copy.
ACTIVE_LINES: Final[str] = (
    'ACTIVE=$(active_app) || exit\nAPP_SRC=$SHARE/apps/$ACTIVE'
)
KIT_ENABLED: Final[str] = (
    'enable paperroute-led-boot paperroute paperroute-on-screen'
)


@dataclass(frozen=True)
class Device:
    """A provisioned share, opt, var and systemd folders, and stubs."""

    share: Path
    opt: Path
    var: Path
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
    Make a share as provision leaves it, with demo active, and empty
    opt, var and systemd folders.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    app : Path
        The app's folder, installed as demo; with no ``deploy/`` its part
        is empty.

    Returns
    -------
    Device
        The folders, the command log and the environment.
    """
    share = root / 'share'
    shutil.copytree(src=DEPLOY, dst=share / 'deploy')
    if (app / 'deploy').is_dir():
        shutil.copytree(src=app / 'deploy', dst=share / 'apps' / 'demo')
    else:
        (share / 'apps' / 'demo').mkdir(parents=True)
    (share / 'active-app').write_text('demo\n', encoding='utf-8')
    etc, opt, var = root / 'etc', root / 'opt', root / 'var'
    for folder in (etc, opt, var):
        folder.mkdir()
    log = root / 'systemctl.log'
    stubs = stub_commands(
        root=root,
        scripts={
            'systemctl': f'#!/bin/sh\nprintf "%s\\n" "$*" >>{log}\n',
            'install': FAKE_INSTALL,
        },
    )
    env = user_env(PATH=f'{stubs}:{os.environ["PATH"]}')
    return Device(share=share, opt=opt, var=var, etc=etc, log=log, env=env)


def install_units(*, device: Device) -> subprocess.CompletedProcess[str]:
    """
    Run install-files' link and unit steps over a device.

    Parameters
    ----------
    device : Device
        The folders and stubs.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    app_re = script_value(path=BIN / 'install-files', name='APP_RE')
    program = (
        f'{MV_STUB}\nAPP_RE={app_re}\nSHARE={device.share}\n'
        f'OPT={device.opt}\nVAR={device.var}\nETC={device.etc}\n'
        f'SRC=$SHARE/deploy\n{ACTIVE_LINES}\n'
        'point_links\ninstall_units\nremove_strays\nenable_units\n'
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


def test_install_files_installs_only_the_active_apps_units(
    tmp_path: Path,
) -> None:
    """Another installed app's units go; the active app's are installed."""
    device = make_device(root=tmp_path, app=WITH_UNITS)
    first = device.share / 'apps' / 'first' / 'systemd'
    first.mkdir(parents=True)
    (first / 'paperroute-one.timer').write_text('[Timer]\n', encoding='utf-8')
    (device.etc / 'paperroute-one.timer').write_text(
        '[Timer]\n',
        encoding='utf-8',
    )
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert not (device.etc / 'paperroute-one.timer').exists()
    assert (device.etc / 'paperroute-job.timer').is_file()
    assert 'disable --now paperroute-one.timer' in logged(
        device=device,
    ).splitlines()


def test_install_files_points_the_links_at_the_active_app(
    tmp_path: Path,
) -> None:
    """The current and data links name the active app's folders."""
    device = make_device(root=tmp_path, app=WITH_UNITS)
    (device.opt / 'current').symlink_to(device.opt / 'apps/first/current')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    assert os.readlink(device.opt / 'current') == str(
        device.opt / 'apps' / 'demo' / 'current',
    )
    assert os.readlink(device.opt / 'data') == str(
        device.var / 'apps' / 'demo',
    )
    assert sorted(path.name for path in device.opt.iterdir()) == [
        'current',
        'data',
    ]
    lines = script_text(path=BIN / 'install-files').splitlines()
    steps = [
        lines.index(step)
        for step in (
            *ACTIVE_LINES.splitlines(),
            'point_links',
            'install_units',
        )
    ]
    assert steps == sorted(steps)


@pytest.mark.parametrize('record', (None, '', 'Demo\n', '../x\n'))
def test_install_files_needs_an_active_app(
    tmp_path: Path,
    record: str | None,
) -> None:
    """With no valid active-app record, nothing is installed."""
    device = make_device(root=tmp_path, app=WITH_UNITS)
    active = device.share / 'active-app'
    active.unlink()
    if record is not None:
        active.write_text(record, encoding='utf-8')
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (
        2,
        'install-files: no active app; run scripts/provision\n',
    )
    assert tree_files(root=device.etc) == {}
    assert not list(device.opt.iterdir())


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
        for name in (
            'APP_RE',
            'CONTRACT_RE',
            'TIMER_RE',
            'TOOL_RE',
            'KIT_TOOLS',
            'APP_RULE_RE',
        )
    )
    return run_functions(
        names=('fail', *names),
        program=f'{regexes}{program}',
        env=user_env() if env is None else env,
        script=script,
    )


PROVISION_STEPS: Final[tuple[str, ...]] = (
    'copy_deploy',
    'take_app_copy',
    'write_contract',
    'write_manifest',
    'record_active',
    'make_app_dirs',
    'install_tools',
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
        names=('make_dir', *COPY_FUNCTIONS),
        program=(
            f'chown() {{ :; }}\n{OWNERLESS_INSTALL}\n{MV_STUB}\n'
            f'SHARE={share}\nDIR={staging}\nAPP_NAME=demo\n'
            'copy_deploy\nmove_in apps/demo\ncopy_deploy\nmove_in apps/demo\n'
        ),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert sorted(path.name for path in share.iterdir()) == ['apps', 'deploy']
    assert sorted(path.name for path in (share / 'apps').iterdir()) == [
        'demo',
    ]
    assert snapshot(root=share / 'deploy') == snapshot(root=DEPLOY)
    assert snapshot(root=share / 'apps' / 'demo') == snapshot(
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
        program=(
            f'SHARE={device.share}\nAPP_NAME=demo\n'
            'write_contract\nwrite_manifest\n'
        ),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert (device.share / 'units-contract').read_text(encoding='utf-8') == (
        '1\n'
    )
    assert (device.share / 'apps' / 'demo' / 'timers').read_text(
        encoding='utf-8',
    ) == timers
    lines = script_text(path=BIN / 'provision-device').splitlines()
    steps = [lines.index(step) for step in PROVISION_STEPS]
    assert steps == sorted(steps)
    assert lines[steps[0] - 1] == 'make_dir root 0755 "$SHARE"'


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
        (
            'paperroute-on-screen.service.d/x.conf',
            'the app may not change systemd/paperroute-on-screen.service',
        ),
        (
            'paperroute-led-boot.service.d/x.conf',
            'the app may not change systemd/paperroute-led-boot.service',
        ),
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


@pytest.mark.parametrize(
    'text',
    (
        '',
        'paperroute.service\n',
        'paperroute-other.service\n',
        'paperroute-job.service\npaperroute-job.service\n',
        'x y\n',
    ),
)
def test_provision_checks_the_ready_file(tmp_path: Path, text: str) -> None:
    """The ready file names exactly one service the app ships."""
    copy = copy_deploy(tmp_path=tmp_path)
    (copy / 'app' / 'ready').write_text(text, encoding='utf-8')
    result = run_provision(copy=copy, sudo_user=getpass.getuser())
    assert result.returncode == 2
    assert 'ready file must name one of its own services' in result.stderr


@pytest.mark.parametrize('name', ('install', 'set', 'switch'))
def test_provision_refuses_a_kit_word_as_app(
    tmp_path: Path,
    name: str,
) -> None:
    """An app may not take a name the kit's own tools start with."""
    copy = copy_deploy(tmp_path=tmp_path)
    result = run_provision(copy=copy, sudo_user=getpass.getuser(), app=name)
    assert result.stderr == (
        'provision-device: that name is kept for the device\n'
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
    systemd = device.share / 'apps' / 'demo' / 'systemd'
    systemd.mkdir()
    (systemd / 'paperroute_Job.timer').write_text('', encoding='utf-8')
    result = provision_functions(
        names=('write_manifest',),
        program=f'SHARE={device.share}\nAPP_NAME=demo\nwrite_manifest\n',
    )
    assert (result.returncode, result.stderr) == (
        2,
        "provision-device: the app's timer paperroute_Job.timer has a bad"
        ' name\n',
    )
    assert not (device.share / 'apps' / 'demo' / 'timers').exists()


def provision_apps(
    *,
    root: Path,
    staging: Path,
) -> subprocess.CompletedProcess[str]:
    """
    Run provision's share, record, folder and install steps for demo.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory; ``share``, ``opt``, ``var``,
        ``sbin`` and ``sudoers.d`` go here.
    staging : Path
        The staging directory, whose kit install-files is replaced with
        one that only logs.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    (staging / 'kit' / 'bin' / 'install-files').write_text(
        install_files_stub(root=root),
        encoding='utf-8',
    )
    sudoers = root / 'sudoers.d' / 'paperroute-deploy'
    sudoers.parent.mkdir()
    folders = '\n'.join(
        f'{name.upper()}={root / name}'
        for name in ('share', 'opt', 'var', 'sbin')
    )
    program = (
        f'chown() {{ :; }}\n{OWNERLESS_INSTALL}\n{SERVICE_USER_STUBS}\n'
        f'visudo() {{ :; }}\nretire_pin() {{ :; }}\n{MV_STUB}\n{folders}\n'
        f'SUDOERS={sudoers}\n'
        f'SUDOERS_NEW={sudoers.parent}/.paperroute-deploy.new\n'
        f'DIR={staging}\nAPP_NAME=demo\nDEPLOY_USER=pashri\n'
        'make_dir root 0755 "$SHARE"\n'
        + '\n'.join(PROVISION_STEPS)
    )
    return provision_functions(
        names=(
            'make_dir',
            *COPY_FUNCTIONS,
            'write_contract',
            'write_manifest',
            'active_app',
            *SUDOERS_FUNCTIONS,
            'check_app_part',
            'take_app_copy',
            'record_active',
            'make_app_dirs',
            'install_app_tools',
            'install_tools',
        ),
        program=program,
    )


def first_app(
    *,
    root: Path,
    tools: tuple[str, ...] = (),
    lines: str | None = None,
) -> Path:
    """
    Install an app called first, active, as an earlier provision left it.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.
    tools : tuple[str, ...]
        The tools first ships.
    lines : str | None
        Its one sudoers file, or None for none.

    Returns
    -------
    Path
        The share.
    """
    share = root / 'share'
    (share / 'apps' / 'first' / 'systemd').mkdir(parents=True)
    app_copy(folder=share / 'apps' / 'first', tools=tools, lines=lines)
    (share / 'active-app').write_text('first\n', encoding='utf-8')
    return share


def test_provision_adds_a_second_app_beside_the_first(tmp_path: Path) -> None:
    """A second app's copy goes beside the first, which stays active."""
    share = first_app(root=tmp_path)
    result = provision_apps(
        root=tmp_path,
        staging=copy_deploy(tmp_path=tmp_path),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert (share / 'apps' / 'first' / 'systemd').is_dir()
    assert (share / 'apps' / 'demo' / 'timers').read_text(
        encoding='utf-8',
    ) == 'paperroute-job.timer\n'
    assert (share / 'active-app').read_text(encoding='utf-8') == 'first\n'


def test_first_provision_makes_the_app_active(tmp_path: Path) -> None:
    """With no app active yet, the provisioned app becomes the active one."""
    result = provision_apps(
        root=tmp_path,
        staging=copy_deploy(tmp_path=tmp_path),
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert (tmp_path / 'share' / 'active-app').read_text(
        encoding='utf-8',
    ) == 'demo\n'


def test_app_tools_are_installed_under_the_apps_name(tmp_path: Path) -> None:
    """Two apps may ship the same tool; an unprefixed old one goes."""
    first_app(root=tmp_path, tools=('install-tiles',))
    staging = copy_deploy(tmp_path=tmp_path)
    app_copy(folder=staging / 'app', tools=('install-tiles',))
    (tmp_path / 'sbin').mkdir()
    (tmp_path / 'sbin' / 'paperroute-install-tiles').write_text(
        'old\n',
        encoding='utf-8',
    )
    result = provision_apps(root=tmp_path, staging=staging)
    assert (result.returncode, result.stderr) == (0, '')
    assert sorted(
        path.name
        for path in (tmp_path / 'sbin').iterdir()
        if 'tiles' in path.name
    ) == ['paperroute-demo-install-tiles', 'paperroute-first-install-tiles']


def test_sudoers_holds_every_installed_apps_rules(tmp_path: Path) -> None:
    """The sudoers file holds the kit's rule, then each app's in order."""
    first_app(
        root=tmp_path,
        tools=('install-tiles',),
        lines=f'{rule("first")}\n',
    )
    staging = copy_deploy(tmp_path=tmp_path)
    app_copy(
        folder=staging / 'app',
        tools=('install-tiles',),
        lines=f'{rule("demo")}\n',
    )
    result = provision_apps(root=tmp_path, staging=staging)
    assert (result.returncode, result.stderr) == (0, '')
    assert sudoers_rules(
        path=tmp_path / 'sudoers.d' / 'paperroute-deploy',
    ) == [SUDOERS_RULE, rule('demo'), rule('first')]


@pytest.mark.parametrize(
    'tool',
    ('first-install-tiles', 'install-tiles', 'demo-missing'),
)
def test_app_sudoers_may_name_only_its_own_tools(
    tmp_path: Path,
    tool: str,
) -> None:
    """An app's rule may name only a tool that app itself ships."""
    first_app(root=tmp_path, tools=('install-tiles',))
    staging = copy_deploy(tmp_path=tmp_path)
    app_copy(
        folder=staging / 'app',
        tools=('install-tiles',),
        lines=f'{rule("demo").replace("demo-install-tiles", tool)}\n',
    )
    result = provision_apps(root=tmp_path, staging=staging)
    assert result.returncode == 1
    assert 'sudoers line not allowed' in result.stderr
    assert not (tmp_path / 'share' / 'apps' / 'demo').exists()


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


@pytest.mark.parametrize(
    ('installed', 'status', 'stderr'),
    (
        (
            'sydney\n',
            2,
            'install-release: the device runs sydney, not clock. Run'
            ' scripts/provision HOST clock first.\n',
        ),
        ('clock\n', 0, ''),
        (None, 0, ''),
    ),
    ids=('another', 'same', 'unrecorded'),
)
def test_install_release_refuses_another_app(
    *,
    tmp_path: Path,
    installed: str | None,
    status: int,
    stderr: str,
) -> None:
    """A release of an app provision did not install is refused first."""
    if installed is not None:
        (tmp_path / 'installed-app').write_text(installed, encoding='utf-8')
    result = release_functions(
        names=('installed_app_ok',),
        program=f'SHARE={tmp_path} APP_NAME=clock\ninstalled_app_ok\n',
    )
    assert (result.returncode, result.stderr) == (status, stderr)
    body = script_function(path=BIN / 'install-release', name='validate')
    calls = body.splitlines()
    assert '    installed_app_ok' in calls
    assert calls.index('    installed_app_ok') < calls.index(
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


ADOPT_FUNCTIONS: Final[tuple[str, ...]] = (
    'single_app_name',
    'active_app',
    'make_dir',
    'hold_install_lock',
    'old_layout_app',
    'refuse_other_app',
    'old_timers',
    'check_old_services',
    'move_releases',
    'move_data',
    'adopt_single_app',
    'start_board',
)
# Logs all but is-active, which prints BUSY_UNIT's state as activating.
ADOPT_SYSTEMCTL: Final[str] = '''#!/bin/sh
if [ "$1" = is-active ]; then
    if [ "$2" = "${BUSY_UNIT:-}" ]; then
        echo activating
        exit 0
    fi
    echo inactive
    exit 3
fi
printf '%s\\n' "$*" >>"$ADOPT_LOG"
[ "$1" != start ] || exit "${START_STATUS:-0}"
'''
# Logs, drops -T (BSD mv has none) and fails on a path named MV_FAILS.
ADOPT_MV: Final[str] = '''#!/bin/bash
printf 'mv %s\\n' "$*" >>"$ADOPT_LOG"
args=()
for arg in "$@"; do
    [[ $arg == -T ]] && continue
    [[ -n ${MV_FAILS:-} && ${arg##*/} == "$MV_FAILS" ]] && exit 1
    args+=("$arg")
done
PATH=${PATH#*:} exec mv "${args[@]}"
'''
# The service user's commands run as this user, without setsid's -w.
ADOPT_RUNUSER: Final[str] = '#!/bin/sh\nshift 3\nexec "$@"\n'
ADOPT_SETSID: Final[str] = '#!/bin/sh\nshift\nexec "$@"\n'
OLD_DATA: Final[tuple[str, ...]] = (
    'state.json',
    'state.json.bad-1',
    'timetable.db',
    'tiles.db',
    'tiles.stamp',
    'secrets',
    'log/errors.jsonl',
    'sources/a',
    '.cache/b',
)
HOME_NAMES: Final[list[str]] = [
    '.cache',
    'log',
    'secrets',
    'sources',
    'state.json',
    'state.json.bad-1',
    'tiles.db',
    'tiles.stamp',
    'timetable.db',
]
REFUSED_OTHER_APP: Final[str] = (
    'provision-device: this device holds sydney in the old layout;'
    ' provision sydney first\n'
)


@dataclass(frozen=True)
class Layout:
    """A device's opt, var and share folders."""

    opt: Path
    var: Path
    share: Path


def layout_at(root: Path) -> Layout:
    """
    Name a device's folders under a directory.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory.

    Returns
    -------
    Layout
        Its ``opt``, ``var`` and ``share`` folders.
    """
    return Layout(opt=root / 'opt', var=root / 'var', share=root / 'share')


def old_layout(tmp_path: Path) -> Layout:
    """
    Make a device that holds sydney in the single-app layout.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    Layout
        The device's folders.
    """
    layout = layout_at(root=tmp_path)
    release = layout.opt / 'releases' / SHA
    release.mkdir(parents=True)
    (release / 'APP').write_text('paperroute-sydney\n', encoding='utf-8')
    (release / 'VERSION').write_text(f'{SHA}\n', encoding='utf-8')
    (layout.opt / 'current').symlink_to(release)
    for name in OLD_DATA:
        (layout.var / name).parent.mkdir(parents=True, exist_ok=True)
        (layout.var / name).write_text(f'{name}\n', encoding='utf-8')
    old_share(share=layout.share)
    return layout


def old_share(*, share: Path) -> None:
    """
    Write sydney's single-app copy, timer list and record into a share.

    Parameters
    ----------
    share : Path
        The share folder.
    """
    systemd = share / 'app' / 'systemd'
    systemd.mkdir(parents=True)
    for name in ('paperroute-job.service', 'paperroute-job.timer'):
        (systemd / name).write_text('[Unit]\n', encoding='utf-8')
    (share / 'app-timers').write_text(
        'paperroute-job.timer\n',
        encoding='utf-8',
    )
    (share / 'installed-app').write_text('sydney\n', encoding='utf-8')


def new_layout(tmp_path: Path) -> Layout:
    """
    Make a device that holds sydney, active, in the per-app layout.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    Layout
        The device's folders.
    """
    layout = layout_at(root=tmp_path)
    app = layout.opt / 'apps' / 'sydney'
    board = app / 'releases' / SHA / '.venv' / 'bin' / 'paperroute-board'
    board.parent.mkdir(parents=True)
    board.write_text('#!/bin/sh\n', encoding='utf-8')
    board.chmod(0o755)
    (app / 'current').symlink_to(app / 'releases' / SHA)
    (layout.opt / 'current').symlink_to(app / 'current')
    (layout.var / 'apps' / 'sydney').mkdir(parents=True)
    (layout.var / 'apps' / 'sydney' / 'state.json').write_text(
        '{}\n',
        encoding='utf-8',
    )
    (layout.share / 'apps' / 'sydney' / 'systemd').mkdir(parents=True)
    (layout.share / 'active-app').write_text('sydney\n', encoding='utf-8')
    return layout


def layout_snapshot(layout: Layout) -> list[dict[str, str | bytes]]:
    """
    Describe every path under a device's folders.

    Parameters
    ----------
    layout : Layout
        The device's folders.

    Returns
    -------
    list[dict[str, str | bytes]]
        The snapshot of ``opt``, ``var`` and ``share``.
    """
    return [
        snapshot(root=folder)
        for folder in (layout.opt, layout.var, layout.share)
    ]


def run_adopt(
    *,
    root: Path,
    calls: str = 'adopt_single_app',
    app: str = APP_NAME,
    **env: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """
    Run provision's adopt and board steps over a device under stubs.

    Parameters
    ----------
    root : Path
        Pytest's temporary directory, holding the device's folders.
    calls : str
        The steps to run, adopt_single_app unless given.
    app : str
        The app being provisioned, sydney unless given.
    **env : str
        Variables for the stubs: BUSY_UNIT, START_STATUS, MV_FAILS.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], list[str]]
        The exit status and output, and the systemctl and mv calls.
    """
    layout = layout_at(root=root)
    log = root / 'adopt.log'
    stubs = stub_commands(
        root=root,
        scripts={
            'systemctl': ADOPT_SYSTEMCTL,
            'mv': ADOPT_MV,
            'runuser': ADOPT_RUNUSER,
            'setsid': ADOPT_SETSID,
            'install': FAKE_INSTALL,
        },
    )
    result = provision_functions(
        names=ADOPT_FUNCTIONS,
        program=(
            f'SHARE={layout.share}\nOPT={layout.opt}\nVAR={layout.var}\n'
            f'APP_NAME={app}\n{calls}\n'
        ),
        env=user_env(
            PATH=f'{stubs}:{os.environ["PATH"]}',
            ADOPT_LOG=str(log),
            **env,
        ),
    )
    calls_made = (
        log.read_text(encoding='utf-8').splitlines() if log.exists() else []
    )
    return result, calls_made


def test_adopt_moves_releases_data_and_record(tmp_path: Path) -> None:
    """Sydney's releases, data and record move into the per-app layout."""
    layout = old_layout(tmp_path=tmp_path)
    state = (layout.var / 'state.json').read_bytes()
    result, log = run_adopt(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    home = layout.var / 'apps' / 'sydney'
    assert (home / 'state.json').read_bytes() == state
    assert sorted(path.name for path in layout.var.iterdir()) == ['apps']
    assert sorted(path.name for path in home.iterdir()) == HOME_NAMES
    assert os.readlink(layout.opt / 'apps' / 'sydney' / 'current') == str(
        layout.opt / 'apps' / 'sydney' / 'releases' / SHA,
    )
    assert not (layout.opt / 'releases').exists()
    assert (layout.share / 'active-app').read_text(encoding='utf-8') == (
        'sydney\n'
    )
    assert sorted(path.name for path in layout.share.iterdir()) == [
        'active-app',
        'adopting',
    ]
    assert log[0] == 'stop paperroute-job.timer'
    moves = [index for index, line in enumerate(log) if line.startswith('mv ')]
    assert log.index('stop paperroute') < moves[0]


def test_adopt_is_a_no_op_on_the_new_layout(tmp_path: Path) -> None:
    """A device already in the per-app layout is left as it is."""
    layout = new_layout(tmp_path=tmp_path)
    before = layout_snapshot(layout=layout)
    result, log = run_adopt(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    assert layout_snapshot(layout=layout) == before
    assert not log


def test_adopt_refuses_another_app_on_the_old_layout(tmp_path: Path) -> None:
    """Another app is refused before anything is stopped, moved or written."""
    layout = old_layout(tmp_path=tmp_path)
    before = layout_snapshot(layout=layout)
    result, log = run_adopt(root=tmp_path, app='demo')
    assert (result.returncode, result.stderr) == (2, REFUSED_OTHER_APP)
    assert not log
    assert layout_snapshot(layout=layout) == before
    assert not (layout.share / 'apps' / 'demo').exists()
    assert not (layout.share / 'active-app').exists()
    assert not (layout.share / 'adopting').exists()


@pytest.mark.parametrize(
    ('app', 'stderr'),
    (
        ('demo', REFUSED_OTHER_APP),
        (
            'sydney',
            'provision-device: checks passed; provisioning needs root\n',
        ),
    ),
    ids=('other', 'same'),
)
def test_provision_refuses_another_app_before_copying(
    tmp_path: Path,
    app: str,
    stderr: str,
) -> None:
    """Provision refuses another app with the checks, before any copy."""
    layout = old_layout(tmp_path=tmp_path)
    copy = copy_deploy(tmp_path=tmp_path)
    before = layout_snapshot(layout=layout)
    result = run_provision(
        copy=copy,
        sudo_user=getpass.getuser(),
        app=app,
        env={
            'PAPERROUTE_SHARE': str(layout.share),
            'PAPERROUTE_OPT': str(layout.opt),
            'PAPERROUTE_VAR': str(layout.var),
        },
    )
    assert (result.returncode, result.stderr) == (2, stderr)
    assert layout_snapshot(layout=layout) == before
    assert not (layout.share / 'apps').exists()


def test_adopt_refuses_when_it_cannot_name_the_app(tmp_path: Path) -> None:
    """Old releases with no app named anywhere stop the adoption."""
    layout = old_layout(tmp_path=tmp_path)
    (layout.share / 'installed-app').unlink()
    (layout.opt / 'current').unlink()
    before = layout_snapshot(layout=layout)
    result, log = run_adopt(root=tmp_path)
    assert (result.returncode, result.stderr) == (
        2,
        'provision-device: cannot tell which app is installed\n',
    )
    assert not log
    assert layout_snapshot(layout=layout) == before


def test_adopt_drops_the_old_copy_when_the_same_app_is_provisioned(
    tmp_path: Path,
) -> None:
    """The copy just provisioned is kept and the old one removed."""
    layout = old_layout(tmp_path=tmp_path)
    (layout.share / 'apps' / 'sydney').mkdir(parents=True)
    (layout.share / 'apps' / 'sydney' / 'new').write_text(
        'new\n',
        encoding='utf-8',
    )
    result, _ = run_adopt(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    assert (layout.share / 'apps' / 'sydney' / 'new').is_file()
    assert not (layout.share / 'app').exists()
    assert not (layout.share / 'app-timers').exists()


def test_adopt_stops_at_a_failed_move(tmp_path: Path) -> None:
    """A failed data move stops the adoption with the old record kept."""
    layout = old_layout(tmp_path=tmp_path)
    result, _ = run_adopt(root=tmp_path, MV_FAILS='state.json')
    assert result.returncode == 1
    assert result.stderr == (
        f'provision-device: could not move {layout.var} into apps/sydney;'
        ' nothing was removed\n'
    )
    assert (layout.share / 'installed-app').is_file()
    assert (layout.var / 'state.json').is_file()
    assert not (layout.var / 'apps' / 'sydney' / 'state.json').exists()
    assert (layout.share / 'adopting').is_file()


def test_adopt_names_what_it_left_behind(tmp_path: Path) -> None:
    """A name already in the app's folder is left and named."""
    layout = old_layout(tmp_path=tmp_path)
    home = layout.var / 'apps' / 'sydney'
    home.mkdir(parents=True)
    (home / 'state.json').write_text('{}\n', encoding='utf-8')
    result, _ = run_adopt(root=tmp_path)
    assert result.returncode == 0
    assert result.stderr == (
        f'provision-device: left in {layout.var}: state.json\n'
    )


def test_adopt_finishes_an_interrupted_run(tmp_path: Path) -> None:
    """A run stopped after the data moved finishes the releases and record."""
    layout = old_layout(tmp_path=tmp_path)
    state = (layout.var / 'state.json').read_bytes()
    home = layout.var / 'apps' / 'sydney'
    home.mkdir(parents=True)
    for path in layout.var.iterdir():
        if path.name != 'apps':
            path.rename(home / path.name)
    (layout.share / 'active-app').write_text('sydney\n', encoding='utf-8')
    result, _ = run_adopt(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    assert (home / 'state.json').read_bytes() == state
    assert (layout.opt / 'apps' / 'sydney' / 'releases' / SHA).is_dir()
    assert not (layout.share / 'installed-app').exists()


def test_adopt_never_overwrites_moved_state(tmp_path: Path) -> None:
    """A state file already moved is never replaced by a later one."""
    layout = old_layout(tmp_path=tmp_path)
    home = layout.var / 'apps' / 'sydney'
    home.mkdir(parents=True)
    (home / 'state.json').write_bytes(b'kept')
    (layout.var / 'state.json').write_bytes(b'late')
    result, _ = run_adopt(root=tmp_path)
    assert result.returncode == 0
    assert (home / 'state.json').read_bytes() == b'kept'
    assert (layout.var / 'state.json').read_bytes() == b'late'


def test_adopt_waits_for_a_running_app_service(tmp_path: Path) -> None:
    """A running app service stops the adoption and its timers restart."""
    layout = old_layout(tmp_path=tmp_path)
    before = layout_snapshot(layout=layout)
    result, log = run_adopt(root=tmp_path, BUSY_UNIT='paperroute-job.service')
    assert (result.returncode, result.stderr) == (
        1,
        'provision-device: paperroute-job.service is running;'
        ' try again when it has finished\n',
    )
    assert layout_snapshot(layout=layout) == before
    assert log == ['stop paperroute-job.timer', 'start paperroute-job.timer']


def test_adopt_reads_the_app_from_the_live_release(tmp_path: Path) -> None:
    """With no record, the live release names the app."""
    layout = old_layout(tmp_path=tmp_path)
    (layout.share / 'installed-app').unlink()
    result, _ = run_adopt(root=tmp_path)
    assert (result.returncode, result.stderr) == (0, '')
    assert (layout.share / 'active-app').read_text(encoding='utf-8') == (
        'sydney\n'
    )


def test_a_rerun_after_a_failed_provision_starts_the_board(
    tmp_path: Path,
) -> None:
    """The board an earlier run stopped is started, then the marker goes."""
    layout = new_layout(tmp_path=tmp_path)
    (layout.share / 'adopting').touch()
    result, log = run_adopt(
        root=tmp_path,
        calls='adopt_single_app\nstart_board',
    )
    assert (result.returncode, result.stderr) == (0, '')
    assert log == ['start --no-block paperroute']
    assert not (layout.share / 'adopting').exists()


def test_a_provision_with_no_marker_does_not_start_a_stopped_board(
    tmp_path: Path,
) -> None:
    """With no marker, the board is left as it is."""
    new_layout(tmp_path=tmp_path)
    result, log = run_adopt(root=tmp_path, calls='start_board')
    assert (result.returncode, result.stderr) == (0, '')
    assert not log


def test_start_board_waits_for_a_release(tmp_path: Path) -> None:
    """With no runnable release, the board is not started yet."""
    layout = new_layout(tmp_path=tmp_path)
    (layout.share / 'adopting').touch()
    (layout.opt / 'current').unlink()
    (layout.opt / 'current').symlink_to(layout.opt / 'missing')
    result, log = run_adopt(root=tmp_path, calls='start_board')
    assert (result.returncode, result.stderr) == (0, '')
    assert not log
    assert (layout.share / 'adopting').exists()


def test_start_board_keeps_the_marker_when_the_start_fails(
    tmp_path: Path,
) -> None:
    """A failed start keeps the marker, so the next run tries again."""
    layout = new_layout(tmp_path=tmp_path)
    (layout.share / 'adopting').touch()
    run_adopt(root=tmp_path, calls='start_board', START_STATUS='1')
    assert (layout.share / 'adopting').exists()


def test_provision_adopts_after_the_copy_check_and_starts_the_board_last(
) -> None:
    """Another app is refused first; the device moves once the lock is held."""
    lines = script_text(path=BIN / 'provision-device').splitlines()
    order = [
        lines.index(step)
        for step in (
            'refuse_other_app',
            'copy_deploy',
            'take_app_copy',
            'hold_install_lock',
            'adopt_single_app',
            'write_contract',
            'install_tools',
            'start_board',
        )
    ]
    assert order == sorted(order)


def test_provision_refuses_while_the_install_lock_is_held(
    tmp_path: Path,
) -> None:
    """Provision stops while an install or a switch holds the lock."""
    stubs = stub_commands(
        root=tmp_path,
        scripts={'flock': '#!/bin/sh\nexit 1\n'},
    )
    result = provision_functions(
        names=('hold_install_lock',),
        program=f'LOCK={tmp_path / "lock"}\nhold_install_lock\n',
        env=user_env(PATH=f'{stubs}:{os.environ["PATH"]}'),
    )
    assert (result.returncode, result.stderr) == (
        1,
        'provision-device: an install or a switch is running\n',
    )
    assert script_value(path=BIN / 'provision-device', name='LOCK') == (
        '/run/paperroute-install.lock'
    )
