"""Tests for deploy files: install release zones."""

import os
import shutil
from pathlib import Path
from typing import Final

import pytest

from tests.deploy_files.helpers import (
    APP_NAME,
    BIN,
    SET_ZONES,
    SWITCH_STUB,
    SYDNEY_APP,
    SYDNEY_SYSTEMD,
    WITH_UNITS,
    run_functions,
    run_script,
    script_function,
    script_text,
    script_value,
    stage_tree,
    user_env,
)
from tests.deploy_files.layout_helpers import SwitchBox, record, run_switch
from tests.deploy_files.script_helpers import (
    Device,
    install_units,
    make_device,
    release_functions,
    tree_files,
)

ZONE_RE: Final[str] = "ZONE_RE='^[A-Za-z]+(/[A-Za-z0-9_+-]+)*$'"


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
    if status == 0 and not Path(
        '/usr/share/zoneinfo/Australia/Sydney',
    ).is_file():
        pytest.skip('this machine has no zoneinfo for Australia/Sydney')
    tree = zone_stage(root=tmp_path, output=output)
    program = (
        f'OWNER=me TREE={tree} OPT=/opt APP=paperroute-{APP_NAME}\n'
        'has_timers() { :; }\n'
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


APPLY_ZONE: Final[str] = (
    '\nif has_timers \\\n'
    '    && ! reason=$(paperroute-set-timer-zones apply "$APP_NAME" "$ZONE"'
    ' 2>&1)\nthen\n'
)


def test_install_release_skips_unchanged_zone(tmp_path: Path) -> None:
    """With no reload due, set_timer_zone reloads only on a change."""
    body = script_function(path=SET_ZONES, name='set_timer_zone')
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
        script=SET_ZONES,
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


def test_install_release_sets_timer_zone_after_health() -> None:
    """The timers' zone changes only once the new release is healthy."""
    text = script_text(path=BIN / 'install-release')
    body = script_function(path=SET_ZONES, name='set_timer_zone')
    call = text.index(APPLY_ZONE)
    assert text.count('paperroute-set-timer-zones apply') == 1
    assert text.rindex('healthy') < call
    assert "printf '[Timer]\\nOnCalendar=\\n'" in body
    assert 'systemctl daemon-reload' in body
    assert 'restart paperroute-' not in text
    assert script_value(path=SET_ZONES, name='ETC') == '/etc/systemd/system'


def test_install_release_reports_unset_timer_zone() -> None:
    """A failed zone write says the release is live, why, and exits 1."""
    text = script_text(path=BIN / 'install-release')
    call = text.index(APPLY_ZONE)
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
        script=SET_ZONES,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('failed: mkdir')
    assert not log.exists()


def test_set_timer_zone_retries_failed_reload(tmp_path: Path) -> None:
    """A failed reload keeps the drop-in; systemd's flag retries it."""
    body = script_function(path=SET_ZONES, name='set_timer_zone')
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
        script=SET_ZONES,
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
    (tmp_path / 'apps' / 'demo').mkdir(parents=True)
    (tmp_path / 'apps' / 'demo' / 'timers').write_text(
        manifest,
        encoding='utf-8',
    )
    log = tmp_path / 'systemctl.log'
    result = release_functions(
        names=('manifest_timers', *ZONE_FUNCTIONS),
        program=(
            f'SHARE={tmp_path} ETC={etc} APP_NAME=demo ZONE=Australia/Sydney\n'
            f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
            'TIMERS=$(manifest_timers) || exit\nset_timer_zones\n'
        ),
        script=SET_ZONES,
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
        f'has_timers() {{ return 1; }}\nZONE=\n{ZONE_RE}\n'
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
        script=SET_ZONES,
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
    systemd = tmp_path / 'apps' / 'demo' / 'systemd'
    systemd.mkdir(parents=True)
    (systemd / 'paperroute-job.timer').write_text(
        f'[Timer]\nOnCalendar={calendar}\n',
        encoding='utf-8',
    )
    result = release_functions(
        names=('calendar_of', 'check_calendars'),
        program=(
            f'SHARE={tmp_path} APP_NAME=demo TIMERS=paperroute-job.timer\n'
            'check_calendars\n'
        ),
        script=SET_ZONES,
    )
    assert result.returncode == status
    assert result.stderr == (
        'set-timer-zones: paperroute-job.timer already names a time zone;'
        " the app's timer must leave it to the device.\n"
        if status
        else ''
    )


def test_zone_check_reads_the_apps_own_timers(tmp_path: Path) -> None:
    """check reads the named app's timers, not another app's."""
    for app, calendar in (
        ('demo', '*-*-* 03:30:00 Europe/London'),
        ('first', '*-*-* 03:30:00'),
    ):
        systemd = tmp_path / 'apps' / app / 'systemd'
        systemd.mkdir(parents=True)
        (systemd / f'paperroute-{app}.timer').write_text(
            f'[Timer]\nOnCalendar={calendar}\n',
            encoding='utf-8',
        )
        (systemd.parent / 'timers').write_text(
            f'paperroute-{app}.timer\n',
            encoding='utf-8',
        )
    results = [
        run_script(
            name='set-timer-zones',
            args=('check', app),
            env=user_env(PAPERROUTE_SHARE=str(tmp_path)),
        )
        for app in ('first', 'demo')
    ]
    assert [result.returncode for result in results] == [0, 1]
    assert results[1].stderr == (
        'set-timer-zones: paperroute-demo.timer already names a time zone;'
        " the app's timer must leave it to the device.\n"
    )


@pytest.mark.parametrize(
    'args',
    (
        (),
        ('apply', 'demo'),
        ('apply', 'Demo', 'UTC'),
        ('apply', 'demo', 'No/Such_Zone'),
        ('undo', 'demo'),
    ),
)
def test_set_timer_zones_rejects_bad_arguments(
    tmp_path: Path,
    args: tuple[str, ...],
) -> None:
    """A missing, malformed or unknown argument exits 2."""
    (tmp_path / 'apps' / 'demo').mkdir(parents=True)
    (tmp_path / 'apps' / 'demo' / 'timers').write_text('', encoding='utf-8')
    result = run_script(
        name='set-timer-zones',
        args=args,
        env=user_env(PAPERROUTE_SHARE=str(tmp_path)),
    )
    assert result.returncode == 2
    assert result.stderr.startswith('set-timer-zones: ')


def test_install_release_leaves_zones_to_the_tool() -> None:
    """install-release reads no calendar and writes no zone drop-in."""
    text = script_text(path=BIN / 'install-release')
    assert 'calendar_of' not in text
    assert 'set_timer_zone()' not in text


def test_failed_zone_step_leaves_no_other_apps_zone(
    switch_box: SwitchBox,
) -> None:
    """A shared timer keeps no zone from the app that went."""
    (switch_box.share / 'apps' / 'first' / 'timers').write_text(
        'paperroute-job.timer\n',
        encoding='utf-8',
    )
    zone = switch_box.etc / 'paperroute-job.timer.d' / 'app-zone.conf'
    zone.parent.mkdir()
    zone.write_text('[Timer]\n', encoding='utf-8')
    result, _ = run_switch(
        box=switch_box,
        call='apply_locked',
        ZONES_STATUS='1',
    )
    assert result.returncode == 1
    assert result.stderr.endswith(
        'switch-app: demo is showing, but the timer zone was not set\n',
    )
    assert not list(switch_box.etc.rglob('app-zone.conf'))
    assert record(box=switch_box) == 'demo\n'
