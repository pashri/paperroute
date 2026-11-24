"""Tests for deploy files: install files."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from tests.deploy_files.helpers import (
    APP_CONF,
    BAD_SHAS,
    BIN,
    CONFIG_TXT,
    DEPLOY,
    FONTS_CONF,
    KIT_UNITS,
    OFF_HOOK,
    ROOT,
    SECRETS,
    SHA,
    SYDNEY_APP,
    SYDNEY_DATA,
    SYDNEY_DEPLOY,
    SYDNEY_SYSTEMD,
    SYDNEY_UNITS,
    SYSTEMD,
    UNITS,
    WITH_UNITS,
    WITHOUT_UNITS,
    Sandbox,
    commands,
    drop_in_environment,
    file_content,
    kms_off,
    read_unit,
    run_script,
    script_text,
    snapshot,
)
from tests.deploy_files.script_helpers import (
    ACTIVE_LINES,
    DEPLOY_SCRIPT,
    INSTALL_FILES_FUNCTIONS,
    KIT_INSTALLED,
    Device,
    assert_line_refused,
    install_units,
    make_device,
    rule,
    run_app_tools,
    stub_commands,
    tree_files,
)

APP_WORDS: Final[re.Pattern[str]] = re.compile(
    pattern=r'(?i)\b(timetable|tfnsw|sydney)\b',
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
        *(
            BIN / name
            for name in (
                'install-files',
                'install-release',
                'set-timer-zones',
                'switch-app',
            )
        ),
        BIN / 'paperroute-led',
        ROOT / 'scripts' / 'provision',
    ]
    named = [
        str(path.relative_to(ROOT))
        for path in paths
        if APP_WORDS.search(script_text(path=path))
    ]
    assert len(paths) == 9
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


def test_service_units_block_debug_syscalls() -> None:
    """Both paperroute services refuse ptrace and the other debug calls."""
    for name in ('paperroute.service', 'paperroute-timetable.service'):
        service = read_unit(name=name)['Service']
        assert service['SystemCallFilter'] == '~@debug'


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


@pytest.mark.parametrize(
    'name', ['paperroute.service', 'paperroute-timetable.service'],
)
def test_units_point_fontconfig_at_the_shipped_file(name: str) -> None:
    """Both Python services read the fonts.conf the release ships."""
    environment = read_unit(name=name)['Service']['Environment'].split()
    assert f'FONTCONFIG_FILE=/opt/paperroute/current/{FONTS_CONF}' in (
        environment
    )


def test_service_keeps_no_new_privileges() -> None:
    """Power goes through polkit, so the unit still forbids new privileges."""
    service = read_unit(name='paperroute.service')['Service']
    assert service['NoNewPrivileges'] == 'yes'


def test_install_files_installs_hook_and_enables_the_kit() -> None:
    """The hook is installed 0755 and the kit's units are enabled."""
    path = BIN / 'install-files'
    assert commands(path=path, word='system-shutdown') == [
        'install -D -m 0755 -o root -g root'
        ' "$SRC/system-shutdown/paperroute-off-screen"'
        ' /usr/lib/systemd/system-shutdown/paperroute-off-screen',
    ]
    assert commands(path=path, word='enable') == [
        'systemctl enable paperroute-led-boot paperroute $timers',
    ]


def test_no_on_screen_unit_ships() -> None:
    """The board draws the ON picture; no unit of its own ships or is named."""
    assert not (SYSTEMD / 'paperroute-on-screen.service').exists()
    for path in (SYSTEMD / 'paperroute.service', BIN / 'install-files'):
        assert 'paperroute-on-screen' not in script_text(path=path)


def test_app_tool_needs_no_args_rule(tmp_path: Path) -> None:
    """A rule without the empty-argument marker lets any argument through."""
    assert_line_refused(
        root=tmp_path,
        line=rule('demo').removesuffix(' ""'),
        tools='install-tiles\n',
    )


@pytest.mark.parametrize('kit', ('install-release', 'install-files'))
def test_app_tool_cannot_replace_a_kit_tool(tmp_path: Path, kit: str) -> None:
    """
    An app tool named like a kit tool stops provision, installing nothing.
    """
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


KIT_ENABLED: Final[str] = 'enable paperroute-led-boot paperroute'


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


def test_install_files_retires_the_on_screen_unit(tmp_path: Path) -> None:
    """The Pi's copy is disabled while its file is there, then removed."""
    device = make_device(root=tmp_path, app=WITHOUT_UNITS)
    (device.etc / 'paperroute-on-screen.service').write_text(
        '[Service]\nType=oneshot\n\n[Install]\nWantedBy=multi-user.target\n',
        encoding='utf-8',
    )
    stub_commands(
        root=tmp_path,
        scripts={
            'systemctl': (
                '#!/bin/sh\n'
                f'printf "%s\\n" "$*" >>{device.log}\n'
                'if [ "$1" = disable ] && [ -f "' + str(device.etc)
                + '/$3" ]; then\n'
                f'  printf "%s\\n" "$3 still there" >>{device.log}\n'
                'fi\n'
            ),
        },
    )
    result = install_units(device=device)
    assert (result.returncode, result.stderr) == (0, '')
    lines = logged(device=device).splitlines()
    start = lines.index('disable --now paperroute-on-screen.service')
    assert lines[start + 1] == 'paperroute-on-screen.service still there'
    assert not (device.etc / 'paperroute-on-screen.service').exists()
    assert 'enable paperroute-led-boot paperroute' in lines


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
