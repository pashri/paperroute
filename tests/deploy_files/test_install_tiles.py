"""Tests for deploy files: install tiles."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from tests.deploy_files.helpers import (
    BEFORE_RELEASE,
    BIN,
    SHA,
    SYDNEY_BIN,
    SYDNEY_SUDOERS,
    commands,
    run_script,
    script_text,
    script_value,
    snapshot,
    user_env,
)
from tests.deploy_files.script_helpers import (
    DEPLOY_SCRIPT,
    TilesSandbox,
    stub_commands,
)

TILES_SCRIPT: Final[Path] = SYDNEY_BIN / 'install-tiles'


TILES_ROOT_MESSAGE: Final[str] = (
    'install-tiles: checks passed; installing needs root\n'
)


TILES_NOT_A_FILE: Final[str] = (
    'install-tiles: the staged tiles are not a regular file\n'
)


TILES_DIR_NOT_REAL: Final[str] = (
    'install-tiles: the stage tiles directory is not a real directory\n'
)


DATA_PATH: Final[re.Pattern[str]] = re.compile(
    pattern=r'\$\{?(?:DATA|DEST|STAMP|tmp)\b'
)


TMP_PATH: Final[re.Pattern[str]] = re.compile(pattern=r'\$\{?tmp\b')


STAGED_PATH: Final[re.Pattern[str]] = re.compile(pattern=r'\$\{?STAGED\b')


LEADING_WORDS: Final[re.Pattern[str]] = re.compile(
    pattern=r'^(?:(?:if|then|while|!)\s+)+'
)


SERVICE_HEADS: Final[tuple[str, ...]] = (
    'as_service ',
    '[[ ',
    'DATA=${PAPERROUTE_DATA:-$DATA}',
    'DEST=$DATA/tiles.db',
    'STAMP=$DATA/tiles.stamp',
    'suffix=${tmp##*.}',
)


OWNER_HEADS: Final[tuple[str, ...]] = (
    'as_owner ',
    '[[ ',
    'find "$STAGED" -maxdepth 0 -user ',
    'STAGED=${PAPERROUTE_TILES_STAGE:-',
    'local uid found dir=${STAGED%/*}',
)


def stamp_of(path: Path) -> str:
    """
    Describe a file by its size and whole-second mtime.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    str
        ``'<size> <mtime>'``, as install-tiles writes its stamp.
    """
    status = path.stat()
    return f'{status.st_size} {int(status.st_mtime)}'


def command_heads(line: str, pattern: re.Pattern[str]) -> list[str]:
    """
    Find the simple command that uses each match of a pattern in a line.

    Parameters
    ----------
    line : str
        One command line, with continuations joined.
    pattern : re.Pattern[str]
        What to look for, such as a variable reference.

    Returns
    -------
    list[str]
        For each match inside a ``[[ ... ]]`` test that runs no ``$(...)``,
        ``'[[ '``. For any other, the line from the start of its simple
        command (after any pipe, list operator, ``;`` or ``$(``, and
        without a leading ``if``, ``then``, ``while`` or ``!``) to the end.
    """
    tests = [
        found.span()
        for found in re.finditer(pattern=r'\[\[ .*? \]\]', string=line)
        if '$(' not in found.group(0)
    ]
    return [
        '[[ '
        if any(start < found.start() < end for start, end in tests)
        else LEADING_WORDS.sub(
            repl='',
            string=re.split(
                pattern=r'\|\||&&|[|;(]',
                string=line[:found.start()],
            )[-1].lstrip(),
        ) + line[found.start():]
        for found in pattern.finditer(line)
    ]


def run_tiles(
    tiles: TilesSandbox,
    args: tuple[str, ...] = (),
    sudo_user: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run install-tiles in the sandbox as the current user.

    Parameters
    ----------
    tiles : TilesSandbox
        The sandbox.
    args : tuple[str, ...]
        The arguments.
    sudo_user : str | None
        The ``SUDO_USER`` to claim, or None to leave it unset.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The exit status and output.
    """
    extra = {} if sudo_user is None else {'SUDO_USER': sudo_user}
    return run_script(
        name='install-tiles',
        args=args,
        env=tiles.env | extra,
        folder=SYDNEY_BIN,
    )


@pytest.mark.parametrize('value', ('', 'x', '--help', SHA))
def test_install_tiles_rejects_any_argument(
    tiles: TilesSandbox,
    value: str,
) -> None:
    """install-tiles takes no arguments at all."""
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles, args=(value,))
    assert (result.returncode, result.stderr) == (
        2,
        'install-tiles: takes no arguments\n',
    )
    assert snapshot(root=tiles.root) == before


def test_install_tiles_rejects_a_symlinked_stage(tiles: TilesSandbox) -> None:
    """A staged file that is a symlink exits 2 and writes nothing."""
    elsewhere = tiles.root / 'elsewhere.db'
    tiles.staged.rename(elsewhere)
    tiles.staged.symlink_to(elsewhere)
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stderr) == (2, TILES_NOT_A_FILE)
    assert snapshot(root=tiles.root) == before


def test_install_tiles_rejects_a_symlinked_stage_directory(
    tiles: TilesSandbox,
) -> None:
    """A stage tiles directory that is a symlink exits 2 before it is read."""
    linked = tiles.root / 'linked'
    linked.symlink_to(tiles.staged.parent)
    env = tiles.env | {'PAPERROUTE_TILES_STAGE': str(linked / 'tiles.db')}
    before = snapshot(root=tiles.root)
    result = run_script(
        name='install-tiles',
        args=(),
        env=env,
        folder=SYDNEY_BIN,
    )
    assert (result.returncode, result.stderr) == (2, TILES_DIR_NOT_REAL)
    assert snapshot(root=tiles.root) == before


def test_install_tiles_rejects_a_missing_stage(tiles: TilesSandbox) -> None:
    """With nothing staged the script exits 2 and writes nothing."""
    tiles.staged.unlink()
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stderr) == (2, TILES_NOT_A_FILE)
    assert snapshot(root=tiles.root) == before


@pytest.mark.parametrize(
    ('sudo_user', 'reason'),
    (
        ('nobody', 'the staged tiles are not owned by nobody'),
        ('no-such-user-paperroute', 'unknown user'),
    ),
)
def test_install_tiles_checks_the_stage_owner(
    tiles: TilesSandbox,
    sudo_user: str,
    reason: str,
) -> None:
    """The staged file must belong to the sudo caller."""
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles, sudo_user=sudo_user)
    assert (result.returncode, result.stderr) == (
        2,
        f'install-tiles: {reason}\n',
    )
    assert snapshot(root=tiles.root) == before


def test_install_tiles_skips_when_stamp_matches(tiles: TilesSandbox) -> None:
    """Tiles already installed from this staged file are left alone."""
    (tiles.data / 'tiles.db').write_text('old tiles\n', encoding='utf-8')
    (tiles.data / 'tiles.stamp').write_text(
        f'{stamp_of(path=tiles.staged)}\n',
        encoding='utf-8',
    )
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        'tiles unchanged\n',
        '',
    )
    assert snapshot(root=tiles.root) == before


@pytest.mark.parametrize(
    ('dest', 'stamp'),
    ((True, '0 0'), (False, 'staged'), (True, None), (False, None)),
)
def test_install_tiles_needs_root_when_changed(
    tiles: TilesSandbox,
    dest: bool,
    stamp: str | None,
) -> None:
    """A changed or never-installed file passes the checks, then needs root."""
    if dest:
        (tiles.data / 'tiles.db').write_text('old tiles\n', encoding='utf-8')
    if stamp is not None:
        (tiles.data / 'tiles.stamp').write_text(
            stamp.replace('staged', stamp_of(path=tiles.staged)),
            encoding='utf-8',
        )
    before = snapshot(root=tiles.root)
    result = run_tiles(tiles=tiles)
    assert (result.returncode, result.stderr) == (2, TILES_ROOT_MESSAGE)
    assert snapshot(root=tiles.root) == before


def test_install_tiles_checks_free_disk_before_copying(
    tiles: TilesSandbox,
) -> None:
    """Too little free disk exits 1 before anything is written."""
    (tiles.data / 'tiles.db').write_text('old tiles\n', encoding='utf-8')
    (tiles.data / 'tiles.stamp').write_text('0 0\n', encoding='utf-8')
    before = snapshot(root=tiles.root)
    result = run_tiles(
        tiles=TilesSandbox(
            root=tiles.root,
            staged=tiles.staged,
            data=tiles.data,
            env=tiles.env | {'PAPERROUTE_FREE_BYTES': '1'},
        ),
    )
    assert result.returncode == 1
    assert 'not enough free disk' in result.stderr
    assert not list(tiles.data.glob('tiles.db.*'))
    assert snapshot(root=tiles.root) == before


def test_install_tiles_free_check_runs_df_as_service() -> None:
    """df runs only as the service user, before root's first write."""
    text = script_text(path=TILES_SCRIPT)
    found = commands(path=TILES_SCRIPT, word='df')
    assert len(found) == 1
    assert 'as_service df ' in found[0]
    assert text.index('as_service df ') < text.index('installing needs root')
    assert text.index('as_service df ') < text.index('mktemp')
    assert text.index('installing needs root') < text.index('mktemp')


def test_install_tiles_ignores_free_override_as_root() -> None:
    """The free-space override applies only when the caller isn't root."""
    assert (
        "if [[ $EUID -ne 0 && -n ${PAPERROUTE_FREE_BYTES:-} ]]; then\n"
        '    free=$PAPERROUTE_FREE_BYTES\n'
    ) in script_text(path=TILES_SCRIPT)
    assert len(commands(path=TILES_SCRIPT, word='PAPERROUTE_FREE_BYTES')) == 2


def test_install_tiles_rejects_a_non_numeric_free_override(
    tiles: TilesSandbox,
) -> None:
    """A free-space override that is not a plain number is refused."""
    result = run_tiles(
        tiles=TilesSandbox(
            root=tiles.root,
            staged=tiles.staged,
            data=tiles.data,
            env=tiles.env | {'PAPERROUTE_FREE_BYTES': '1+1'},
        ),
    )
    assert result.returncode == 1
    assert 'cannot read the free disk space' in result.stderr


def test_before_release_checks_free_disk_before_tile_upload() -> None:
    """Sydney's hook reads the Pi's free space before the tile rsync."""
    text = BEFORE_RELEASE.read_text(encoding='utf-8')
    rsync = text.index('rsync -a --partial data/tiles.db')
    assert 0 < text.index('df -Pk') < rsync
    assert text.count('^(0|[1-9][0-9]*)$') >= 1
    assert (
        'for value in "$free" "$staged_size" "$staged_mtime" "$size" '
        '"$mtime"; do\n    [[ $value =~ $NUMBER ]]'
    ) in text
    assert text.index('=~ $NUMBER') < rsync
    assert 'not enough free disk on the Pi for the tiles' in text


def test_before_release_only_warns_on_tiles() -> None:
    """Sydney's hook checks the tiles before any ssh and never stops."""
    lines = BEFORE_RELEASE.read_text(encoding='utf-8').splitlines()
    check = next(
        index
        for index, line in enumerate(lines)
        if 'check-tiles' in line
    )
    first_ssh = next(
        index
        for index, line in enumerate(lines)
        if re.search(r'\bssh\b', line) and not line.lstrip().startswith('#')
    )
    assert check < first_ssh
    assert lines[check] == (
        'uv run python -m paperroute_sydney.build check-tiles data/tiles.db '
        "|| printf 'deploy: warning: tiles do not match this release; "
        "deploying anyway\\n' >&2"
    )
    assert 'exit' not in lines[check]
    assert lines[check - 1] == '[[ -f data/tiles.db ]] || exit 0'


def test_before_release_reports_a_failed_tile_size_read() -> None:
    """An ssh failure reading sizes reaches the number check's message."""
    text = BEFORE_RELEASE.read_text(encoding='utf-8')
    assert "{ print $4 }') \\\n    || free=\n" in text
    assert "|| echo '0 0'\") \\\n    || remote=\n" in text
    assert text.index('|| remote=') < text.index('=~ $NUMBER')


def test_install_tiles_ignores_overrides_as_root() -> None:
    """The test overrides apply only when the caller isn't root."""
    assert (
        'if [[ $EUID -ne 0 ]]; then\n'
        '    STAGED=${PAPERROUTE_TILES_STAGE:-$STAGED}\n'
        '    DATA=${PAPERROUTE_DATA:-$DATA}\n'
        'fi\n'
        'DEST=$DATA/tiles.db\n'
        'STAMP=$DATA/tiles.stamp\n'
    ) in script_text(path=TILES_SCRIPT)


def test_install_tiles_constants() -> None:
    """The real paths, and the lock install-release takes."""
    values = {
        name: script_value(path=TILES_SCRIPT, name=name)
        for name in ('APP', 'STAGED', 'DATA', 'LOCK')
    }
    assert values == {
        'APP': 'sydney',
        'STAGED': '/var/cache/paperroute-stage/tiles/$APP/tiles.db',
        'DATA': '/var/lib/paperroute/apps/$APP',
        'LOCK': script_value(path=BIN / 'install-release', name='LOCK'),
    }


def test_install_tiles_touches_data_only_as_the_service_user() -> None:
    """Root never opens a data path, and reads the stage only as its owner."""
    data_heads = [
        head
        for line in commands(
            path=TILES_SCRIPT,
            word='(?:DATA|DEST|STAMP|tmp)',
        )
        for head in command_heads(line=line, pattern=DATA_PATH)
    ]
    owner_heads = [
        head
        for line in commands(path=TILES_SCRIPT, word='STAGED')
        for head in command_heads(line=line, pattern=STAGED_PATH)
    ]
    assert len(data_heads) >= 8
    assert all(head.startswith(SERVICE_HEADS) for head in data_heads)
    assert any(head.startswith('as_owner cat ') for head in owner_heads)
    assert all(head.startswith(OWNER_HEADS) for head in owner_heads)
    assert not any(
        re.search(pattern=r'(?:^|[;&|(]) *install ', string=line)
        for line in commands(path=TILES_SCRIPT, word='install')
    )
    assert [
        'as_service sqlite3 ' in line
        for line in commands(path=TILES_SCRIPT, word='sqlite3')
    ] == [True]


def test_install_tiles_runs_users_detached_without_the_lock() -> None:
    """Every runuser has no terminal, no stdin and no lock fd."""
    assert commands(path=TILES_SCRIPT, word='runuser') == [
        'setsid -w runuser -u "$OWNER" -- "$@" 2>/dev/null 9>&-',
        'setsid -w runuser -u paperroute -- "$@" 2>/dev/null 9>&-',
    ]
    assert '\nexec </dev/null\n' in script_text(path=TILES_SCRIPT)


def test_install_tiles_keeps_user_output_off_the_terminal() -> None:
    """Every owner or service command's stdout is captured or discarded."""
    found = [
        line
        for line in commands(path=TILES_SCRIPT, word='as_(?:owner|service)')
        if not re.fullmatch(pattern=r'as_\w+\(\) \{', string=line)
    ]
    assert len(found) >= 10
    assert all('$(' in line or '>/dev/null' in line for line in found)


def test_install_tiles_ends_options_before_the_temp_file() -> None:
    """
    The service-chosen temp path follows ``--`` or ``sh -c``.

    sqlite3 takes the path without ``--``; the script has already checked
    that it is an absolute path under the data directory.
    """
    prefixes = [
        head.split('$tmp')[0]
        for line in commands(path=TILES_SCRIPT, word='tmp')
        for head in command_heads(line=line, pattern=TMP_PATH)
        if head.startswith('as_service ')
    ]
    assert len(prefixes) >= 5
    assert 'as_service sqlite3 -readonly "' in prefixes
    assert all(
        ' -- ' in prefix
        or prefix.startswith('as_service sh -c ')
        or prefix == 'as_service sqlite3 -readonly "'
        for prefix in prefixes
    )


def test_install_tiles_locks_cleans_up_and_renames() -> None:
    """The install holds the lock, removes its temp file, and renames."""
    text = script_text(path=TILES_SCRIPT)
    assert 'exec 9>"$LOCK"\nif ! flock -n 9; then\n' in text
    assert 'trap cleanup EXIT\n' in text
    assert '        if ! as_service rm -f -- "$tmp" >/dev/null; then\n' in text


def test_install_tiles_fails_loudly_on_a_directory_at_dest() -> None:
    """mv -T refuses to move the tiles into a directory, and that aborts."""
    text = script_text(path=TILES_SCRIPT)
    rename = (
        'as_service mv -T -f -- "$tmp" "$DEST" >/dev/null \\\n'
        "    || abort 'could not replace the tiles; is tiles.db a directory?'"
    )
    abort = (
        "abort() {\n    printf 'install-tiles: %s\\n' \"$1\" >&2\n"
        '    exit 1\n}\n'
    )
    assert text.startswith('#!/bin/bash\n')
    assert '\nset -euo pipefail\n' in text
    assert abort in text
    assert rename in text
    assert text.index(rename) < text.index("printf 'tiles installed")


def test_install_tiles_stamps_portably() -> None:
    """The stamp is size and mtime, from GNU stat or the BSD fallback."""
    found = commands(path=TILES_SCRIPT, word='stat')
    assert found == [
        "stamp=$(as_owner stat -c '%s %Y' -- \"$STAGED\" 2>/dev/null"
        " || as_owner stat -f '%z %m' -- \"$STAGED\")"
        " || fail 'cannot read the staged tiles'",
    ]


def test_sydney_ships_install_tiles_and_its_rule() -> None:
    """Sydney's deploy/ holds the tiles tool and the one rule for it."""
    assert sorted(path.name for path in SYDNEY_BIN.iterdir()) == [
        'install-tiles',
    ]
    assert sorted(path.name for path in SYDNEY_SUDOERS.iterdir()) == [
        'paperroute-sydney',
    ]


def test_before_release_installs_tiles_after_staging_them() -> None:
    """A failed tiles install fails the hook; it stages before installing."""
    text = script_text(path=BEFORE_RELEASE)
    assert commands(path=BEFORE_RELEASE, word='TILES') == [
        'TILES=/usr/local/sbin/paperroute-sydney-install-tiles',
        'if ! ssh -- "$host" sudo -n -l "$TILES" >/dev/null 2>&1; then',
        'ssh -- "$host" sudo -n "$TILES" || exit',
    ]
    order = [
        text.index(part)
        for part in (
            "check-tiles data/tiles.db",
            'if ! ssh -- "$host" sudo -n -l "$TILES"',
            'tiles not installed: run scripts/provision HOST sydney',
            'ssh -- "$host" mkdir -p "$STAGE/tiles/sydney"',
            'rsync -a --partial data/tiles.db '
            '"$host:$STAGE/tiles/sydney/tiles.db"',
            'sudo -n "$TILES" || exit',
        )
    ]
    assert order == sorted(order)
    assert script_value(path=BEFORE_RELEASE, name='STAGE') == (
        script_value(path=DEPLOY_SCRIPT, name='STAGE')
    )


def test_before_release_moves_the_old_staged_tiles_first() -> None:
    """The tiles staged before the per-app layout move before the upload."""
    text = script_text(path=BEFORE_RELEASE)
    move = 'mv $STAGE/tiles/tiles.db $STAGE/tiles/sydney/tiles.db'
    assert 0 < text.index(move) < text.index('rsync')


def test_before_release_carries_on_without_the_tile_tool(
    tmp_path: Path,
) -> None:
    """
    With no tile tool on the Pi the hook says so, uploads nothing, exits 0.
    """
    script = (
        tmp_path / 'apps' / 'paperroute-sydney' / 'scripts' / 'before-release'
    )
    script.parent.mkdir(parents=True)
    shutil.copy(BEFORE_RELEASE, script)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'tiles.db').write_bytes(b'')
    log = tmp_path / 'log'
    stubs = stub_commands(
        root=tmp_path,
        scripts={
            'uv': '#!/bin/sh\nexit 0\n',
            'ssh': f'#!/bin/sh\necho "$*" >> {log}\nexit 1\n',
        },
    )
    result = subprocess.run(
        [str(script), 'pi'],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, 'PATH': f'{stubs}:{os.environ["PATH"]}'},
    )
    assert result.returncode == 0
    assert result.stderr.endswith(
        'deploy: tiles not installed: run scripts/provision HOST sydney, '
        'then deploy again\n'
    )
    assert 'rsync' not in log.read_text(encoding='utf-8')


@dataclass(frozen=True)
class DeployRepo:
    """A committed tree for scripts/deploy, with ssh and rsync stubbed."""

    root: Path
    hook: Path
    log: Path
    env: dict[str, str]


@pytest.fixture(name='deploy_repo')
def fixture_deploy_repo(tmp_path: Path) -> DeployRepo:
    """
    Make a tree holding scripts/deploy, a passing check and one app.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Returns
    -------
    DeployRepo
        The tree, where the app's hook goes, the stub log and the
        environment that puts the stubs first.
    """
    repo = tmp_path / 'repo'
    scripts = repo / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy2(src=DEPLOY_SCRIPT, dst=scripts / 'deploy')
    (scripts / 'check').write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    (scripts / 'check').chmod(0o755)
    app = repo / 'apps' / 'paperroute-demo'
    app.mkdir(parents=True)
    (app / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
    (repo / '.gitignore').write_text('data/\n', encoding='utf-8')
    (repo / 'data').mkdir()
    (repo / 'data' / 'tiles.db').write_text('tiles\n', encoding='utf-8')
    log = tmp_path / 'calls.log'
    stubs = stub_commands(
        root=tmp_path,
        scripts={
            'ssh': f'#!/bin/sh\nprintf "ssh %s\\n" "$*" >>{log}\n',
            'rsync': (
                f'#!/bin/sh\ncat >/dev/null\nprintf "rsync %s\\n" "$*"'
                f' >>{log}\n'
            ),
        },
    )
    return DeployRepo(
        root=repo,
        hook=app / 'scripts' / 'before-release',
        log=log,
        env=user_env(PATH=f'{stubs}:{os.environ["PATH"]}'),
    )


def write_hook(*, repo: DeployRepo, status: int, mode: int) -> None:
    """
    Give the app a before-release hook that logs its arguments.

    Parameters
    ----------
    repo : DeployRepo
        The tree.
    status : int
        The hook's exit status.
    mode : int
        The hook's permission bits.
    """
    repo.hook.parent.mkdir()
    repo.hook.write_text(
        f'printf "hook %s\\n" "$*" >>{repo.log}\nexit {status}\n',
        encoding='utf-8',
    )
    repo.hook.chmod(mode)


def run_deploy(*, repo: DeployRepo) -> tuple[int, list[str], str]:
    """
    Commit the tree and run scripts/deploy for its app.

    Parameters
    ----------
    repo : DeployRepo
        The tree.

    Returns
    -------
    tuple[int, list[str], str]
        Deploy's exit status, the stub calls in order, and the short sha.
    """
    git = [
        'git',
        '-c', 'user.email=test@example.com',
        '-c', 'user.name=Test',
        '-c', 'commit.gpgsign=false',
        '-c', 'core.hooksPath=/dev/null',
    ]
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'x']):
        subprocess.run(
            args=[*git, *args],
            cwd=repo.root,
            check=True,
            timeout=30,
        )
    sha = subprocess.run(
        args=['git', 'rev-parse', '--short', 'HEAD'],
        cwd=repo.root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    result = subprocess.run(
        args=['bash', str(repo.root / 'scripts' / 'deploy'), 'host', 'demo'],
        env=repo.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    calls = (
        repo.log.read_text(encoding='utf-8').splitlines()
        if repo.log.exists()
        else []
    )
    return result.returncode, calls, sha


def test_deploy_runs_the_apps_before_release(
    deploy_repo: DeployRepo,
) -> None:
    """The hook runs with the host after staging, before install-release."""
    write_hook(repo=deploy_repo, status=0, mode=0o755)
    status, calls, sha = run_deploy(repo=deploy_repo)
    assert status == 0
    assert calls[-3].startswith('rsync -a --from0 --files-from=- ./ host:')
    assert calls[-2:] == [
        'hook host',
        f'ssh -- host sudo -n /usr/local/sbin/paperroute-install-release'
        f' {sha} demo',
    ]


def test_deploy_runs_before_release_without_its_executable_bit(
    deploy_repo: DeployRepo,
) -> None:
    """A hook that lost its executable bit still runs, through bash."""
    write_hook(repo=deploy_repo, status=0, mode=0o644)
    _, calls, _ = run_deploy(repo=deploy_repo)
    assert 'hook host' in calls
    assert commands(path=DEPLOY_SCRIPT, word='HOOK') == [
        'HOOK=scripts/before-release',
        'if [[ -f apps/paperroute-$app/$HOOK ]]; then',
        'bash "apps/paperroute-$app/$HOOK" "$host" || exit',
    ]


def test_deploy_stops_when_before_release_fails(
    deploy_repo: DeployRepo,
) -> None:
    """A failing hook ends the deploy with its status, before the install."""
    write_hook(repo=deploy_repo, status=3, mode=0o755)
    status, calls, _ = run_deploy(repo=deploy_repo)
    assert status == 3
    assert calls[-1] == 'hook host'
    assert not any('install-release' in call for call in calls[1:])


def test_deploy_without_before_release_skips_tiles(
    deploy_repo: DeployRepo,
) -> None:
    """An app with no hook deploys without touching data/tiles.db."""
    status, calls, sha = run_deploy(repo=deploy_repo)
    assert status == 0
    assert not any('tiles' in call for call in calls)
    assert calls[-1] == (
        f'ssh -- host sudo -n /usr/local/sbin/paperroute-install-release'
        f' {sha} demo'
    )
