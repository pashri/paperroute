"""Tests for resetting settings and erasing everything."""

import logging
from pathlib import Path

import pytest

from paperroute_device.adapters.resets import FileResets, NullResets
from paperroute_device.core.device_model import ResetKind
from paperroute_device_testkit.device_fakes import FakeKeys, FakeWifi


def data_files(*, folder: Path) -> dict[str, Path]:
    """
    Write a state file, a moved-aside one, a timetable and tiles.

    Parameters
    ----------
    folder : Path
        The data directory.

    Returns
    -------
    dict[str, Path]
        Each file by its name.
    """
    names = ('state.json', 'state.json.bad-1', 'timetable.db', 'tiles.db')
    files = {name: folder / name for name in names}
    for path in files.values():
        path.write_text('x', encoding='utf-8')
    return files


def resets_in(
    *, folder: Path, keys: FakeKeys, wifi: FakeWifi
) -> FileResets:
    """
    Make resets over the standard files in a data directory.

    Parameters
    ----------
    folder : Path
        The data directory.
    keys : FakeKeys
        The key store.
    wifi : FakeWifi
        The Wi-Fi control.

    Returns
    -------
    FileResets
        The resets.
    """
    return FileResets(
        data_dir=folder,
        state=folder / 'state.json',
        key=keys,
        wifi=wifi,
        timetable=folder / 'timetable.db',
    )


async def test_file_resets_settings_keeps_data(tmp_path: Path) -> None:
    """Resetting settings drops state, key and Wi-Fi; data stays."""
    files = data_files(folder=tmp_path)
    keys, wifi = FakeKeys(key='k' * 32), FakeWifi()
    resets = resets_in(folder=tmp_path, keys=keys, wifi=wifi)
    await resets.reset(kind=ResetKind.SETTINGS)
    assert not files['state.json'].exists()
    assert not files['state.json.bad-1'].exists()
    assert files['timetable.db'].exists()
    assert files['tiles.db'].exists()
    assert keys.key is None
    assert wifi.forgotten == 1


async def test_file_resets_erase_deletes_data(tmp_path: Path) -> None:
    """Erasing everything also deletes the timetable, keeping the tiles."""
    files = data_files(folder=tmp_path)
    keys, wifi = FakeKeys(key='k' * 32), FakeWifi()
    resets = resets_in(folder=tmp_path, keys=keys, wifi=wifi)
    await resets.reset(kind=ResetKind.ERASE)
    assert not files['state.json'].exists()
    assert not files['timetable.db'].exists()
    assert files['tiles.db'].exists()
    assert keys.key is None
    assert wifi.forgotten == 1


async def test_resets_survive_missing_files(tmp_path: Path) -> None:
    """With none of the files there, the key and Wi-Fi are still reset."""
    keys, wifi = FakeKeys(key='k' * 32), FakeWifi()
    resets = resets_in(folder=tmp_path, keys=keys, wifi=wifi)
    await resets.reset(kind=ResetKind.ERASE)
    assert keys.key is None
    assert wifi.forgotten == 1


async def test_file_resets_refuse_paths_outside_data_dir(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Paths outside the data directory, even through links, are kept."""
    data, elsewhere = tmp_path / 'data', tmp_path / 'elsewhere'
    data.mkdir()
    elsewhere.mkdir()
    outside = elsewhere / 'timetable.db'
    outside.write_text('x', encoding='utf-8')
    target = elsewhere / 'state.json'
    target.write_text('x', encoding='utf-8')
    (data / 'state.json').symlink_to(target)
    resets = FileResets(
        data_dir=data,
        state=data / 'state.json',
        key=FakeKeys(),
        wifi=FakeWifi(),
        timetable=outside,
    )
    with caplog.at_level(logging.ERROR):
        await resets.reset(kind=ResetKind.ERASE)
    assert outside.exists()
    assert target.exists()
    refused = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(refused) == 2


async def test_null_resets_log_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NullResets touches nothing and logs the first time only."""
    resets = NullResets()
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            await resets.reset(kind=ResetKind.ERASE)
    assert [r.getMessage() for r in caplog.records] == [
        'reset not enabled',
    ]


async def test_file_resets_refuse_a_linked_directory(
    tmp_path: Path,
) -> None:
    """A file under a linked directory that leads outside is kept."""
    data, elsewhere = tmp_path / 'data', tmp_path / 'elsewhere'
    data.mkdir()
    elsewhere.mkdir()
    (data / 'sub').symlink_to(elsewhere, target_is_directory=True)
    outside = elsewhere / 'timetable.db'
    outside.write_text('x', encoding='utf-8')
    resets = FileResets(
        data_dir=data,
        state=data / 'state.json',
        key=FakeKeys(),
        wifi=FakeWifi(),
        timetable=data / 'sub' / 'timetable.db',
    )
    await resets.reset(kind=ResetKind.ERASE)
    assert outside.exists()
