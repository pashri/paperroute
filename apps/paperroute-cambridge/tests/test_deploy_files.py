"""Tests for the Cambridge deploy files."""

import re
import subprocess
from pathlib import Path

APP = Path(__file__).parents[1]
DEPLOY = APP / 'deploy'
SYDNEY = APP.parent / 'paperroute-sydney' / 'deploy'


def files_under(directory: Path) -> list[str]:
    """List the files under a deploy directory, relative to its app."""
    return sorted(
        str(path.relative_to(directory.parent))
        for path in directory.rglob('*')
        if path.is_file()
    )


def swapped(name: str) -> str:
    """Name Cambridge where a name has Sydney."""
    return name.replace('sydney', 'cambridge')


def text(name: str) -> str:
    """Read a Cambridge file by its name relative to the app."""
    return (APP / name).read_text(encoding='utf-8')


def sydney_text(name: str) -> str:
    """Read Sydney's file of the same name."""
    swapped_name = name.replace('cambridge', 'sydney')
    return (APP.parent / 'paperroute-sydney' / swapped_name).read_text(
        encoding='utf-8',
    )


def test_deploy_files_are_the_same_set_as_sydneys() -> None:
    """Deploy files are the same set as sydneys."""
    assert files_under(
        DEPLOY,
    ) == [swapped(name) for name in files_under(SYDNEY)]
    assert (APP / 'scripts' / 'before-release').is_file()


def test_timer_runs_at_half_past_six_and_names_no_zone() -> None:
    """Timer runs at half past six and names no zone."""
    timer = text('deploy/systemd/paperroute-timetable.timer')
    assert re.findall(r'^OnCalendar=(.*)$', timer, re.M) == ['*-*-* 06:30:00']


def test_only_the_board_reads_the_bus_data_key() -> None:
    """Only the board reads the bus data key."""
    conf = text('deploy/systemd/paperroute.service.d/app.conf')
    assert (
        'Environment=PAPERROUTE_KEY_FILE_BODS='
        '/var/lib/paperroute/apps/cambridge/secrets\n'
    ) in conf
    assert 'KEY_FILE' not in text(
        'deploy/systemd/paperroute-timetable.service',
    )
    paths = files_under(DEPLOY)
    assert paths
    for path in paths:
        assert 'TFNSW' not in text(path)
        assert 'PAPERROUTE_API_KEY_FILE' not in text(path)


def test_units_name_only_cambridges_directory() -> None:
    """Units name only cambridges directory."""
    paths = files_under(DEPLOY)
    assert paths
    for path in paths:
        for found in re.findall(r'/var/lib/paperroute[^ \n"]*', text(path)):
            assert found == '/var/lib/paperroute' or found.startswith(
                (
                    '/var/lib/paperroute/apps/cambridge',
                    '/var/lib/paperroute/apps/$APP',
                ),
            ), found
        assert 'sydney' not in text(path).lower()


def test_files_differ_from_sydneys_only_as_planned() -> None:
    """Files differ from sydneys only as planned."""
    for name in (
        'deploy/ready',
        'deploy/bin/install-tiles',
        'deploy/sudoers/paperroute-cambridge',
    ):
        assert text(name) == swapped(sydney_text(name))


def test_before_release_checks_and_ships_cambridge_tiles() -> None:
    """Before release checks and ships cambridge tiles."""
    script = text('scripts/before-release')
    assert (
        'TILES=/usr/local/sbin/paperroute-cambridge-install-tiles\n' in script
    )
    assert (
        'uv run python -m paperroute_cambridge.build check-tiles '
        'data/cambridge/tiles.db'
    ) in script
    assert '$STAGE/tiles/cambridge/tiles.db' in script
    assert 'data/tiles.db' not in script
    assert '$STAGE/tiles/tiles.db' not in script


def test_shell_scripts_parse() -> None:
    """Shell scripts parse."""
    for name in ('deploy/bin/install-tiles', 'scripts/before-release'):
        assert subprocess.run(
            ['bash', '-n', str(APP / name)], check=False,
        ).returncode == 0
