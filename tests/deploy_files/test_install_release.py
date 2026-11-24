"""Tests for deploy files: install release."""

import os
import re
import subprocess
from pathlib import Path
from typing import Final

from tests.deploy_files.helpers import (
    APP_NAME,
    BIN,
    MV_STUB,
    SHA,
    Sandbox,
    commands,
    run_bash,
    run_functions,
    script_function,
    script_text,
    script_value,
    user_env,
)


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


def test_install_release_records_the_app() -> None:
    """take_over writes the app's name beside VERSION, before chown."""
    body = script_function(path=BIN / 'install-release', name='take_over')
    version = '    printf \'%s\\n\' "$SHA" >"$INCOMING/tree/VERSION"\n'
    app = '    printf \'%s\\n\' "$APP" >"$INCOMING/tree/APP"\n'
    assert body.count(app) == 1
    assert body.index(version) + len(version) == body.index(app)
    assert body.index(app) < body.index('chown -hR')


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
    early = [
        index
        for index, line in enumerate(lines[:last])
        if 'exit 0' in line
    ]
    assert [lines[index] for index in early] == ['    exit 0']
    assert lines.index('if ! is_active; then') < early[0]


def test_install_release_checks_links_after_take_over() -> None:
    """Links are checked once root owns the tree, before it is released."""
    body = script_function(path=BIN / 'install-release', name='take_over')
    check = body.index('check_links "$INCOMING/tree" "$OPT/python"')
    assert body.index('chown -hR') < body.index('chmod -R') < check
    assert check < body.index('mv -T -- "$INCOMING/tree" "$R/$SHA"')
    assert 'the release links outside itself' in body[check:]


# BSD find has no -printf; glob order stands in for age, later is newer.
FIND_STUB: Final[str] = (
    'find() { local i=0 path; for path in "$1"/*; do i=$((i + 1));'
    ' printf \'%s %s\\n\' "$i" "${path##*/}"; done; }'
)


RELEASE_TAIL_FUNCTIONS: Final[tuple[str, ...]] = (
    'fail',
    'has_timers',
    'active_app',
    'is_active',
    'switch_to',
    'prune',
)


def run_release_tail(
    *,
    sandbox: Sandbox,
    active: str,
) -> subprocess.CompletedProcess[str]:
    """
    Run install-release from ``build`` on, with the build, the take-over,
    the health check and the zone tool stubbed.

    Parameters
    ----------
    sandbox : Sandbox
        The directories; ``systemctl.log`` goes in its root.
    active : str
        The app the device's record names as active.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    script = BIN / 'install-release'
    text = script_text(path=script)
    tail = text[text.index('\nbuild\n'):]
    releases = sandbox.opt / 'apps' / APP_NAME / 'releases'
    log = sandbox.root / 'systemctl.log'
    (sandbox.root / 'share' / 'active-app').write_text(
        f'{active}\n',
        encoding='utf-8',
    )
    definitions = '\n'.join(
        script_function(path=script, name=name)
        for name in RELEASE_TAIL_FUNCTIONS
    )
    program = (
        f'set -euo pipefail\n{definitions}\n'
        f'SHA={SHA} APP_NAME={APP_NAME} SHARE={sandbox.root / "share"}\n'
        f'R={releases} CURRENT={releases.parent / "current"} prev= reason=\n'
        f'KEEP=3 SHA_RE={script_value(path=script, name="SHA_RE")}\n'
        'build() { :; }\ntake_over() { mkdir -- "$R/$SHA"; }\n'
        'healthy() { :; }\npaperroute-set-timer-zones() { :; }\n'
        f'systemctl() {{ printf "%s\\n" "$*" >>{log}; }}\n'
        f'{MV_STUB}\n{FIND_STUB}\n{tail}'
    )
    return run_bash(args=['bash', '-c', program])


def test_release_for_another_app_restarts_nothing(sandbox: Sandbox) -> None:
    """A release of an app not on the panel becomes its current, alone."""
    (sandbox.opt / 'apps' / 'first' / 'releases').mkdir(parents=True)
    result = run_release_tail(sandbox=sandbox, active='first')
    releases = sandbox.opt / 'apps' / APP_NAME / 'releases'
    assert (result.returncode, result.stderr) == (0, '')
    assert os.readlink(releases.parent / 'current') == str(releases / SHA)
    assert not (sandbox.root / 'systemctl.log').exists()
    assert result.stdout == (
        f'install-release: {SHA} is installed for sydney; first stays on the'
        ' panel\n'
    )
    assert sorted(path.name for path in sandbox.opt.iterdir()) == ['apps']


def test_release_without_an_active_app_fails(sandbox: Sandbox) -> None:
    """A device with no active app is told to provision; nothing restarts."""
    result = run_release_tail(sandbox=sandbox, active='')
    assert (result.returncode, result.stdout, result.stderr) == (
        2,
        '',
        'install-release: the device has no active app. Run scripts/provision'
        f' HOST {APP_NAME} first.\n',
    )
    assert not (sandbox.root / 'systemctl.log').exists()


def test_release_for_the_active_app_keeps_to_its_directory(
    sandbox: Sandbox,
) -> None:
    """The active app's release goes live under apps/APP and nowhere else."""
    result = run_release_tail(sandbox=sandbox, active=APP_NAME)
    releases = sandbox.opt / 'apps' / APP_NAME / 'releases'
    assert (result.returncode, result.stderr) == (0, '')
    assert result.stdout == f'install-release: {SHA} is live\n'
    assert os.readlink(releases.parent / 'current') == str(releases / SHA)
    assert (sandbox.root / 'systemctl.log').read_text(encoding='utf-8') == (
        'restart paperroute\n'
    )
    assert sorted(path.name for path in sandbox.opt.iterdir()) == ['apps']
    text = script_text(path=BIN / 'install-release')
    for old in ('/opt/paperroute/releases', '$OPT/current', 'installed-app'):
        assert old not in text


def test_releases_are_pruned_per_app(tmp_path: Path) -> None:
    """Pruning one app's releases leaves another app's alone."""
    shas = ('abc1231', 'abc1232', 'abc1233', SHA)
    for app in (APP_NAME, 'first'):
        for sha in shas:
            (tmp_path / 'apps' / app / 'releases' / sha).mkdir(parents=True)
    script = BIN / 'install-release'
    program = (
        f'SHA={SHA} prev= KEEP=3 R={tmp_path}/apps/{APP_NAME}/releases\n'
        f'SHA_RE={script_value(path=script, name="SHA_RE")}\n'
        f'{FIND_STUB}\nprune\n'
    )
    result = run_functions(names=('prune',), program=program, env=user_env())
    assert (result.returncode, result.stderr) == (0, '')
    assert [
        len(list((tmp_path / 'apps' / app / 'releases').iterdir()))
        for app in (APP_NAME, 'first')
    ] == [3, 4]
