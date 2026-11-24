"""Shared helpers for the deploy files tests: layout helpers."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tests.deploy_files.helpers import (
    APP_NAME,
    BIN,
    SHA,
    run_functions,
    script_value,
    snapshot,
    user_env,
)
from tests.deploy_files.script_helpers import (
    FAKE_INSTALL,
    provision_functions,
    stub_commands,
)

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


SWITCH_APP: Final[Path] = BIN / 'switch-app'


SWITCH_FUNCTIONS: Final[tuple[str, ...]] = (
    'fail',
    'refuse',
    'active_app',
    'ready_unit',
    'app_timers',
    'busy',
    'scheduled',
    'quiet',
    'activate',
    'go_back',
    'apply_switch',
    'check_switch',
    'apply_locked',
)


# Answers is-active from UNIT_STATES (UNIT=STATE words) and logs the rest;
# the first start of the board fails while SWITCH_ROOT/start-fails exists,
# and the command SYSTEMCTL_FAILS names always fails.
SWITCH_SYSTEMCTL: Final[str] = '''#!/bin/bash
if [[ $1 == is-active ]]; then
    quiet=no
    if [[ $2 == --quiet ]]; then
        quiet=yes
        shift
    fi
    state=inactive
    for pair in ${UNIT_STATES:-}; do
        if [[ ${pair%%=*} == "$2" ]]; then
            state=${pair#*=}
        fi
    done
    [[ $quiet == yes ]] || printf '%s\\n' "$state"
    [[ $state == active || $state == reloading ]]
    exit
fi
printf 'systemctl %s\\n' "$*" >>"$SWITCH_ROOT/switch.log"
if [[ $* == 'start paperroute' && -e $SWITCH_ROOT/start-fails ]]; then
    rm -f "$SWITCH_ROOT/start-fails"
    exit 1
fi
[[ $* != "${SYSTEMCTL_FAILS:-}" ]]
'''


# Logs the record; fails once, or always, as SWITCH_ROOT/install-fails says.
SWITCH_INSTALL_FILES: Final[str] = '''#!/bin/bash
printf 'paperroute-install-files (active: %s)\\n' \\
    "$(cat "$SWITCH_ROOT/share/active-app")" >>"$SWITCH_ROOT/switch.log"
fails=$SWITCH_ROOT/install-fails
if [[ -e $fails ]]; then
    [[ $(cat "$fails") == always ]] || rm -f "$fails"
    exit 1
fi
'''


SWITCH_RUNUSER: Final[str] = '''#!/bin/bash
printf 'runuser %s\\n' "$*" >>"$SWITCH_ROOT/switch.log"
shift 3
exec "$@"
'''


SWITCH_SET_ZONES: Final[str] = '''#!/bin/bash
printf 'paperroute-set-timer-zones %s\\n' "$*" >>"$SWITCH_ROOT/switch.log"
exit "${ZONES_STATUS:-0}"
'''


SWITCH_RUN: Final[str] = '''#!/bin/bash
printf 'systemd-run %s\\n' "$*" >>"$SWITCH_ROOT/switch.log"
'''


@dataclass(frozen=True)
class SwitchBox:
    """A device with apps first (active) and demo, and logging stubs."""

    root: Path
    share: Path
    opt: Path
    etc: Path
    board: Path
    env: dict[str, str]


def add_release(*, opt: Path, app: str, zone: str) -> Path:
    """
    Give an app a current release whose board prints a zone.

    Parameters
    ----------
    opt : Path
        The device's opt folder.
    app : str
        The app.
    zone : str
        What ``paperroute-board zone`` prints.

    Returns
    -------
    Path
        The board, reached through the app's current link.
    """
    home = opt / 'apps' / app
    board = home / 'releases' / SHA / '.venv' / 'bin' / 'paperroute-board'
    board.parent.mkdir(parents=True)
    board.write_text(f'#!/bin/sh\necho {zone}\n', encoding='utf-8')
    board.chmod(0o755)
    (home / 'current').symlink_to(home / 'releases' / SHA)
    return home / 'current' / '.venv' / 'bin' / 'paperroute-board'


def run_switch(
    *,
    box: SwitchBox,
    call: str,
    app: str = 'demo',
    **env: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """
    Run one of switch-app's bodies over the device under stubs.

    Parameters
    ----------
    box : SwitchBox
        The device.
    call : str
        check_switch or apply_locked.
    app : str
        The app asked for, demo unless given.
    **env : str
        Variables for the stubs: UNIT_STATES, FLOCK_STATUS, ZONES_STATUS.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], list[str]]
        The exit status and output, and the logged commands.
    """
    regexes = ''.join(
        f'{name}={script_value(path=SWITCH_APP, name=name)}\n'
        for name in ('APP_RE', 'TIMER_RE', 'READY_RE')
    )
    result = run_functions(
        names=SWITCH_FUNCTIONS,
        program=(
            f'{regexes}SHARE={box.share}\nOPT={box.opt}\nETC={box.etc}\n'
            f'LOCK={box.root / "lock"}\nAPP={app}\n{call}\n'
        ),
        env=box.env | env,
        script=SWITCH_APP,
    )
    log = box.root / 'switch.log'
    if not log.exists():
        return result, []
    return result, log.read_text(encoding='utf-8').splitlines()


def record(box: SwitchBox) -> str:
    """
    Read the device's active-app record.

    Parameters
    ----------
    box : SwitchBox
        The device.

    Returns
    -------
    str
        The record's text.
    """
    return (box.share / 'active-app').read_text(encoding='utf-8')
