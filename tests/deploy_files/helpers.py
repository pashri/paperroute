"""Shared helpers for the deploy files tests: helpers."""

import configparser
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).parents[2]


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


FIXTURES: Final[Path] = Path(__file__).parents[1] / 'fixtures'


WITH_UNITS: Final[Path] = FIXTURES / 'app_with_units'


WITHOUT_UNITS: Final[Path] = FIXTURES / 'app_without_units'


CONFIG_TXT: Final[Path] = FIXTURES / 'config.txt'


SET_ZONES: Final[Path] = BIN / 'set-timer-zones'


OFF_HOOK: Final[Path] = DEPLOY / 'system-shutdown' / 'paperroute-off-screen'


SUDOERS_RULE: Final[str] = (
    'pashri ALL=(root) NOPASSWD: '
    '/usr/local/sbin/paperroute-install-release, '
    '/usr/local/sbin/paperroute-install-files ""'
)


TILES_RULE: Final[str] = (
    'pashri ALL=(root) NOPASSWD:'
    ' /usr/local/sbin/paperroute-sydney-install-tiles ""'
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
    'paperroute-switch@.service': {
        'Unit': {'Description': 'paperroute app switch'},
        'Service': {
            'Type': 'oneshot',
            'ExecStart': '/usr/local/sbin/paperroute-switch-app %i',
        },
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
    """Stage, opt and share directories that override the real ones."""

    root: Path
    stage: Path
    opt: Path
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
    *,
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


def run_bash(*, args: list[str]) -> subprocess.CompletedProcess[str]:
    """
    Run a command as the current user with a clean environment.

    Parameters
    ----------
    args : list[str]
        The command and its arguments.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished process, with its output captured.
    """
    return subprocess.run(
        args=args,
        env=user_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


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
