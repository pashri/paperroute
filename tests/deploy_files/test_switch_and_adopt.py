"""Tests for deploy files: switch and adopt."""

import os
from pathlib import Path
from typing import Final

import pytest

from tests.deploy_files.helpers import (
    BIN,
    DEPLOY,
    SHA,
    read_unit,
    run_script,
    script_text,
)
from tests.deploy_files.layout_helpers import (
    REFUSED_OTHER_APP,
    SwitchBox,
    layout_snapshot,
    new_layout,
    old_layout,
    record,
    run_adopt,
    run_switch,
)
from tests.deploy_files.script_helpers import provision_functions, rule_code

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
            'hold_install_lock',
            'copy_deploy',
            'take_app_copy',
            'adopt_single_app',
            'write_contract',
            'install_tools',
            'start_board',
        )
    ]
    assert order == sorted(order)


@pytest.mark.parametrize(
    ('pin', 'state', 'stderr'),
    (
        (False, None, ''),
        (True, 'state.json', ''),
        (True, 'apps/sydney/state.json', ''),
        (
            True,
            None,
            'provision-device: deploy M4 first so state.json holds the pin\n',
        ),
    ),
    ids=('no-pin', 'old-state', 'moved-state', 'no-state'),
)
def test_pin_check_accepts_state_moved_by_an_interrupted_run(
    *,
    tmp_path: Path,
    pin: bool,
    state: str | None,
    stderr: str,
) -> None:
    """A re-run after the data moved passes the M4 pin check."""
    layout = new_layout(tmp_path=tmp_path)
    (layout.var / 'apps' / 'sydney' / 'state.json').unlink()
    (layout.share / 'adopting').touch()
    pin_file = tmp_path / 'm2a.env'
    if pin:
        pin_file.write_text('PIN=1\n', encoding='utf-8')
    if state is not None:
        (layout.var / state).write_text('{}\n', encoding='utf-8')
    result = provision_functions(
        names=('active_app', 'check_pin_saved'),
        program=(
            f'PIN_FILE={pin_file}\nSHARE={layout.share}\nVAR={layout.var}\n'
            'check_pin_saved\n'
        ),
    )
    assert (result.returncode, result.stderr) == (2 if stderr else 0, stderr)


SWITCH_RULE: Final[Path] = DEPLOY / 'polkit' / '52-paperroute-switch.rules'


SCHEDULE_LINE: Final[str] = (
    'systemd-run --quiet --collect --on-active=3'
    ' --unit=paperroute-switch-apply'
    ' /usr/local/sbin/paperroute-switch-app --apply demo'
)


def test_switch_check_schedules_the_apply(switch_box: SwitchBox) -> None:
    """A switch that can go ahead is scheduled, and nothing else runs."""
    result, log = run_switch(box=switch_box, call='check_switch')
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        'switch-app: switching to demo\n',
        '',
    )
    assert log == [SCHEDULE_LINE]
    assert record(box=switch_box) == 'first\n'


@pytest.mark.parametrize(
    ('app', 'release', 'env', 'reason'),
    (
        ('first', True, {}, 'first is already showing'),
        ('demo', False, {}, 'demo has no release; deploy it first'),
        ('demo', True, {'FLOCK_STATUS': '1'}, 'an install is running'),
        (
            'demo',
            True,
            {'UNIT_STATES': 'paperroute-one.service=activating'},
            'first is still being made ready',
        ),
    ),
    ids=('showing', 'no-release', 'installing', 'making-ready'),
)
def test_switch_check_refuses(
    *,
    switch_box: SwitchBox,
    app: str,
    release: bool,
    env: dict[str, str],
    reason: str,
) -> None:
    """Each refusal exits 1 with its reason and changes nothing."""
    if not release:
        switch_box.board.unlink()
    result, log = run_switch(
        box=switch_box,
        call='check_switch',
        app=app,
        **env,
    )
    assert (result.returncode, result.stderr) == (1, f'switch-app: {reason}\n')
    assert not log
    assert record(box=switch_box) == 'first\n'


def test_switch_refuses_while_a_switch_is_scheduled(
    switch_box: SwitchBox,
) -> None:
    """A second switch is refused while the first is still to apply."""
    result, log = run_switch(
        box=switch_box,
        call='check_switch',
        UNIT_STATES='paperroute-switch-apply.timer=active',
    )
    assert (result.returncode, result.stderr) == (
        1,
        'switch-app: a switch is already under way\n',
    )
    assert not log


@pytest.mark.parametrize('app', ('', 'Demo', '../x', 'nosuch'))
def test_switch_rejects_a_bad_or_unknown_app(
    switch_box: SwitchBox,
    app: str,
) -> None:
    """A bad name, or an app that is not installed, exits 2."""
    result = run_script(
        name='switch-app',
        args=(app,),
        env=switch_box.env | {'PAPERROUTE_SHARE': str(switch_box.share)},
    )
    assert result.returncode == 2
    assert result.stderr.startswith('switch-app: ')


def test_switch_apply_stops_the_board_before_the_record_changes(
    switch_box: SwitchBox,
) -> None:
    """The old app's units and the board stop, then the new units go in."""
    result, log = run_switch(box=switch_box, call='apply_locked')
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        'switch-app: demo is showing\n',
        '',
    )
    assert log == [
        'systemctl stop paperroute-one.service',
        'systemctl stop paperroute',
        'paperroute-install-files (active: demo)',
        f'runuser -u paperroute -- {switch_box.board} zone',
        'paperroute-set-timer-zones apply demo Europe/London',
        'systemctl start paperroute',
        'systemctl start --no-block paperroute-job.service',
    ]
    assert record(box=switch_box) == 'demo\n'


def test_switch_apply_goes_back_when_the_app_does_not_start(
    switch_box: SwitchBox,
) -> None:
    """A board that does not come up puts the previous app back."""
    (switch_box.root / 'start-fails').touch()
    result, log = run_switch(box=switch_box, call='apply_locked')
    assert result.returncode == 1
    assert record(box=switch_box) == 'first\n'
    assert log[-5:] == [
        'systemctl stop paperroute-job.timer',
        'systemctl stop paperroute-job.service',
        'systemctl stop paperroute',
        'paperroute-install-files (active: first)',
        'systemctl start paperroute',
    ]
    assert result.stderr.endswith(
        'switch-app: demo did not start; back on first\n',
    )


def test_switch_apply_carries_on_when_the_ready_unit_does_not_start(
    switch_box: SwitchBox,
) -> None:
    """A ready unit that does not start is reported; the app still shows."""
    result, _ = run_switch(
        box=switch_box,
        call='apply_locked',
        SYSTEMCTL_FAILS='start --no-block paperroute-job.service',
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        'switch-app: demo is showing\n',
        'switch-app: could not start paperroute-job.service\n',
    )
    assert record(box=switch_box) == 'demo\n'


def test_switch_apply_goes_back_when_install_files_fails(
    switch_box: SwitchBox,
) -> None:
    """Units that do not install put the previous app back."""
    (switch_box.root / 'install-fails').write_text('once', encoding='utf-8')
    result, _ = run_switch(box=switch_box, call='apply_locked')
    assert result.returncode == 1
    assert record(box=switch_box) == 'first\n'


def test_switch_apply_stops_the_outgoing_apps_units_first(
    switch_box: SwitchBox,
) -> None:
    """The outgoing app's timers and ready unit stop, and lose their zone."""
    (switch_box.share / 'active-app').write_text('demo\n', encoding='utf-8')
    zone = switch_box.etc / 'paperroute-job.timer.d' / 'app-zone.conf'
    zone.parent.mkdir()
    zone.write_text('[Timer]\n', encoding='utf-8')
    result, log = run_switch(box=switch_box, call='apply_locked', app='first')
    assert (result.returncode, result.stderr) == (0, '')
    assert log[:3] == [
        'systemctl stop paperroute-job.timer',
        'systemctl stop paperroute-job.service',
        'systemctl stop paperroute',
    ]
    assert log.index('paperroute-install-files (active: first)') == 3
    assert not zone.exists()


def test_switch_apply_gives_up_when_the_lock_stays_held(
    switch_box: SwitchBox,
) -> None:
    """An install that keeps the lock leaves the device as it was."""
    result, log = run_switch(
        box=switch_box,
        call='apply_locked',
        FLOCK_STATUS='1',
    )
    assert (result.returncode, result.stderr) == (
        1,
        'switch-app: an install is still running; not switched\n',
    )
    assert not log
    assert record(box=switch_box) == 'first\n'


def test_switch_apply_skips_zones_for_an_app_without_timers(
    switch_box: SwitchBox,
) -> None:
    """An app with no timers gets no zone step."""
    (switch_box.share / 'active-app').write_text('demo\n', encoding='utf-8')
    result, log = run_switch(box=switch_box, call='apply_locked', app='first')
    assert (result.returncode, result.stderr) == (0, '')
    assert not [line for line in log if 'zone' in line]


def test_switch_apply_rechecks_ready(switch_box: SwitchBox) -> None:
    """A make-ready step started since the check stops the apply."""
    result, log = run_switch(
        box=switch_box,
        call='apply_locked',
        UNIT_STATES='paperroute-one.service=activating',
    )
    assert (result.returncode, result.stderr) == (
        1,
        'switch-app: first is still being made ready\n',
    )
    assert 'systemctl stop paperroute' not in log


def test_switch_unit_runs_the_check_for_its_instance() -> None:
    """The templated unit runs the check for the app it is named after."""
    assert read_unit(name='paperroute-switch@.service')['Service'] == {
        'Type': 'oneshot',
        'ExecStart': '/usr/local/sbin/paperroute-switch-app %i',
    }


def test_polkit_lets_the_service_user_start_only_the_switch_unit() -> None:
    """The rule lets paperroute start a switch unit and nothing else."""
    code = rule_code(path=SWITCH_RULE)
    for part in (
        'subject.user != "paperroute"',
        'org.freedesktop.systemd1.manage-units',
        'action.lookup("verb") == "start"',
        r'/^paperroute-switch@[a-z][a-z0-9]*\.service$/',
    ):
        assert part in code
    assert code.count('polkit.Result') == 1


def test_install_files_installs_the_switch_rule() -> None:
    """install-files installs the switch rule beside the other two."""
    assert '52-paperroute-switch.rules' in script_text(
        path=BIN / 'install-files',
    )
