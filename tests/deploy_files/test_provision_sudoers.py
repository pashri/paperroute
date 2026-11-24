"""Tests for deploy files: provision sudoers."""

import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from tests.deploy_files.helpers import (
    APP_CONF,
    BIN,
    DEPLOY,
    ROOT,
    SUDOERS_RULE,
    SYDNEY_BIN,
    SYDNEY_SUDOERS,
    TILES_RULE,
    UNITS,
    commands,
    copy_deploy,
    drop_in_environment,
    read_unit,
    script_function,
    script_text,
    sudoers_rules,
)
from tests.deploy_files.script_helpers import (
    KIT_INSTALLED,
    app_copy,
    assert_line_refused,
    first_app,
    install_files_stub,
    install_sudoers,
    provision_apps,
    provision_tools,
    rule,
    rule_code,
    run_app_tools,
    sudoers_files,
    tree_files,
)

CAMBRIDGE_SUDOERS: Final[Path] = (
    ROOT / 'apps' / 'paperroute-cambridge' / 'deploy' / 'sudoers'
)


RELEASES: Final[re.Pattern[str]] = re.compile(
    pattern=r'/opt/paperroute/apps/[^/\s]+/releases',
)


APP_RULE: Final[re.Pattern[str]] = re.compile(
    pattern=(
        r'^\S+ ALL=\(root\) NOPASSWD: '
        r'/usr/local/sbin/paperroute-[a-z][a-z0-9]*-[a-z][a-z0-9-]* ""$'
    ),
)


ROOT_SAFE_ENVIRONMENT: Final[frozenset[str]] = frozenset(
    {
        'PAPERROUTE_APP_ARGS',
        'PAPERROUTE_KEY_FILE_TFNSW',
        'PAPERROUTE_API_KEY_FILE',
        'FONTCONFIG_FILE',
    }
)


ROOT_EXEC_KEYS: Final[tuple[str, ...]] = (
    'ExecStartPre',
    'ExecStartPost',
    'ExecStopPost',
)


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


POLKIT_RULE: Final[Path] = DEPLOY / 'polkit' / '50-paperroute-power.rules'


POWER_ACTIONS: Final[frozenset[str]] = frozenset({
    'org.freedesktop.login1.power-off',
    'org.freedesktop.login1.power-off-multiple-sessions',
    'org.freedesktop.login1.reboot',
    'org.freedesktop.login1.reboot-multiple-sessions',
})


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
    """The three rule files are installed root-owned, power first."""
    assert commands(path=BIN / 'install-files', word='polkit') == [
        'install -D -m 0644 -o root -g root'
        ' "$SRC/polkit/50-paperroute-power.rules"'
        ' /etc/polkit-1/rules.d/50-paperroute-power.rules',
        'install -D -m 0644 -o root -g root'
        ' "$SRC/polkit/51-paperroute-network.rules"'
        ' /etc/polkit-1/rules.d/51-paperroute-network.rules',
        'install -D -m 0644 -o root -g root'
        ' "$SRC/polkit/52-paperroute-switch.rules"'
        ' /etc/polkit-1/rules.d/52-paperroute-switch.rules',
    ]


def test_provision_installs_polkitd() -> None:
    """provision installs polkitd and needs the rule files."""
    installs = commands(path=BIN / 'provision-device', word='apt-get')
    assert 'polkitd' in installs[-1].split()
    needed = commands(path=BIN / 'provision-device', word='polkit')
    assert any(
        line.startswith('for need in') and line.endswith(
            ' polkit/51-paperroute-network.rules'
            ' polkit/52-paperroute-switch.rules; do',
        )
        for line in needed
    )


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
    assert not any('/releases' in line for line in rules)
    shipped = [
        *sudoers_rules(path=DEPLOY / 'sudoers' / 'paperroute-deploy'),
        *sudoers_rules(path=SYDNEY_SUDOERS / 'paperroute-sydney'),
        *sudoers_rules(path=CAMBRIDGE_SUDOERS / 'paperroute-cambridge'),
    ]
    assert RELEASES.search('/opt/paperroute/apps/demo/releases/x/bin/y')
    assert not any(RELEASES.search(line) for line in shipped)


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
        'pashri ALL=(root) NOPASSWD:'
        ' /usr/local/sbin/paperroute-install-release ""',
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
        'paperroute-set-timer-zones': 'new zones\n',
        'paperroute-switch-app': 'new switch\n',
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
