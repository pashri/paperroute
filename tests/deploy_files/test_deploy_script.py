"""Tests for deploy files: deploy script."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

import numpy as np
import pytest

from paperroute_hardware.screen import load_frame
from tests.deploy_files.helpers import (
    APP_NAME,
    BEFORE_RELEASE,
    BIN,
    CONFIG_TXT,
    DEPLOY,
    FONTS_CONF,
    OFF_HOOK,
    ROOT,
    SYDNEY_BIN,
    WITHOUT_UNITS,
    UnitParser,
    commands,
    file_content,
    kms_off,
    read_unit,
    run_bash,
    run_functions,
    run_script,
    script_text,
    script_value,
    secrets_line,
    user_env,
)
from tests.deploy_files.layout_helpers import SwitchBox, run_switch
from tests.deploy_files.script_helpers import (
    DEPLOY_SCRIPT,
    KIT_INSTALLED,
    make_device,
    provision_functions,
    provision_tools,
    rule,
    tree_files,
)

BIN_SCRIPTS: Final[tuple[str, ...]] = (
    'install-files',
    'install-release',
    'paperroute-led',
    'provision-device',
    'set-timer-zones',
    'switch-app',
)


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


CITY_NAMES: Final[re.Pattern[str]] = re.compile(
    pattern=r'(?i)sydney|tfnsw|\bnsw',
)


KMS_OFF: Final[str] = 's/^dtoverlay=vc4-kms-v3d/#&/'


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


JOURNALD: Final[dict[str, dict[str, str]]] = {
    'Journal': {'Storage': 'volatile', 'RuntimeMaxUse': '24M'},
}


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
    result = run_bash(args=['bash', str(deploy), *args])
    assert (result.returncode, result.stderr) == (2, message)
    assert not (tmp_path / 'checked').exists()


def test_deploy_checks_a_known_app(tmp_path: Path) -> None:
    """An app that exists gets as far as scripts/check."""
    deploy = copy_deploy_script(root=tmp_path)
    app = tmp_path / 'apps' / f'paperroute-{APP_NAME}'
    app.mkdir(parents=True)
    (app / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
    result = run_bash(args=['bash', str(deploy), 'host', APP_NAME])
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


def test_bin_holds_the_planned_scripts() -> None:
    """deploy/bin holds the six scripts and nothing else."""
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
    if path == BIN / 'provision-device':
        text = text.replace(secrets_line(), '')
    assert 'tfnsw_api_key' not in text
    assert 'secrets' not in text


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


def test_shipped_screens_are_panel_frames() -> None:
    """Every picture loads and holds only the panel's four greys."""
    for name in (
        'off.raw',
        'on.raw',
        'loading-cambridge.raw',
        'loading-sydney.raw',
    ):
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
            '-k',
            '2',
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


def test_off_hook_runs_as_the_app_user_with_its_groups() -> None:
    """The shutdown hook drops root as the app does."""
    app = read_unit(name='paperroute.service')['Service']
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


def test_simulate_pi_build_checks_links() -> None:
    """The simulated build passes the same link check the Pi runs."""
    text = script_text(path=ROOT / 'scripts' / 'simulate-pi-build')
    assert text.count('check_links "$moved" "$python_dir"') == 1


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
    *,
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


def test_go_back_starts_the_board_even_when_it_cannot_reinstall(
    switch_box: SwitchBox,
) -> None:
    """The board is started again, and the owner is told to provision."""
    (switch_box.root / 'install-fails').write_text('always', encoding='utf-8')
    result, log = run_switch(box=switch_box, call='apply_locked')
    assert result.returncode == 1
    assert log[-1] == 'systemctl start paperroute'
    assert 'could not go back to first cleanly; run scripts/provision' in (
        result.stderr
    )
