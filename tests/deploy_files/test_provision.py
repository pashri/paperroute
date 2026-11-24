"""Tests for deploy files: provision."""

import getpass
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from paperroute_device.boot_splash import SPLASH_IMAGE
from tests.deploy_files.helpers import (
    BIN,
    DEPLOY,
    MV_STUB,
    OFF_HOOK,
    OWNERLESS_INSTALL,
    ROOT,
    SERVICE_USER_STUBS,
    SUDOERS_RULE,
    SYDNEY_APP,
    TILES_RULE,
    WITH_UNITS,
    WITHOUT_UNITS,
    commands,
    copy_deploy,
    script_function,
    script_text,
    script_value,
    secrets_line,
    snapshot,
    sudoers_rules,
    user_env,
)
from tests.deploy_files.layout_helpers import (
    REFUSED_OTHER_APP,
    layout_snapshot,
    new_layout,
    old_layout,
    run_adopt,
)
from tests.deploy_files.script_helpers import (
    COPY_FUNCTIONS,
    PROVISION_STEPS,
    SUDOERS_FUNCTIONS,
    first_app,
    install_sudoers,
    make_device,
    provision_apps,
    provision_functions,
    stub_commands,
    sudoers_files,
    tree_files,
)


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
    gate = lines.index('check_pin_saved')
    assert gate < lines.index('copy_deploy')
    assert gate < lines.index('install_tools')


def run_provision(
    *,
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


def test_provision_installs_only_the_kit_tools() -> None:
    """provision installs the kit's four tools; the tiles are Sydney's."""
    script = BIN / 'provision-device'
    assert script_value(path=script, name='KIT_TOOLS') == (
        "'install-release install-files set-timer-zones switch-app'"
    )
    needed = ' '.join(
        line
        for line in commands(path=script, word='need')
        if line.startswith('for need in')
    ).split()
    assert {'bin/install-release', 'bin/switch-app'} <= set(needed)
    assert 'tiles' not in script_text(path=script)
    assert commands(path=script, word='install -m 0755') == [
        'install -m 0755 -o root -g root "$path"'
        ' "$SBIN/paperroute-$app-$name" || exit 1',
        'install -m 0755 -o root -g root "$SHARE/deploy/bin/$name"'
        ' "$SBIN/paperroute-$name"',
    ]


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
            ' polkit/51-paperroute-network.rules'
            ' polkit/52-paperroute-switch.rules; do',
        )
        for line in needed
    )


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


@pytest.mark.parametrize(
    ('tool', 'status', 'stderr'),
    (
        (
            'Bad Name',
            1,
            'provision-device: app tool Bad Name is not allowed\n',
        ),
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
    assert commands(
        path=BIN / 'provision-device',
        word='paperroute-stage',
    ) == [
        'make_dir "$DEPLOY_USER" 0700 /var/cache/paperroute-stage',
    ]
    assert 'DEPLOY_USER=$SUDO_USER' in text.splitlines()


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
    assert SPLASH_IMAGE == Path(screen)
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
            'paperroute-led-boot.service',
            'the app may not replace systemd/paperroute-led-boot.service',
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
