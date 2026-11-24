"""Shared helpers for the deploy files tests: script helpers."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tests.deploy_files.helpers import (
    BIN,
    DEPLOY,
    MV_STUB,
    OWNERLESS_INSTALL,
    ROOT,
    SERVICE_USER_STUBS,
    SET_ZONES,
    SYDNEY_SUDOERS,
    run_functions,
    script_text,
    script_value,
    user_env,
)

DEPLOY_SCRIPT: Final[Path] = ROOT / 'scripts' / 'deploy'


@dataclass(frozen=True)
class TilesSandbox:
    """A staged tiles file and a data directory that override the real ones."""

    root: Path
    staged: Path
    data: Path
    env: dict[str, str]


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
    (kit_bin / 'set-timer-zones').write_text('new zones\n', encoding='utf-8')
    (kit_bin / 'switch-app').write_text('new switch\n', encoding='utf-8')
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


RELEASE_REGEXES: Final[tuple[str, ...]] = ('TIMER_RE', 'NAMED_ZONE_RE')


def release_functions(
    *,
    names: tuple[str, ...],
    program: str,
    env: dict[str, str] | None = None,
    script: Path = BIN / 'install-release',
) -> subprocess.CompletedProcess[str]:
    """
    Run a release script's named functions, with ``fail`` and the regexes.

    Parameters
    ----------
    names : tuple[str, ...]
        The functions to take from the script.
    program : str
        Variables, stubs and calls, run after the definitions.
    env : dict[str, str] | None
        The environment, this process's without ``SUDO_USER`` unless given.
    script : Path
        The script, install-release unless given; the regexes always come
        from set-timer-zones.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    regexes = ''.join(
        f'{name}={script_value(path=SET_ZONES, name=name)}\n'
        for name in RELEASE_REGEXES
    )
    return run_functions(
        names=('fail', *names),
        program=f'{regexes}{program}',
        env=user_env() if env is None else env,
        script=script,
    )
