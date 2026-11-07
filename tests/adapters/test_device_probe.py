"""Tests for reading the device's facts from the system."""

from pathlib import Path

from paperroute.adapters.device_probe import SystemProbe, mem_available
from paperroute.adapters.tiles_db import NullTiles
from paperroute.core.device_model import WifiStatus
from paperroute.logs import LastError
from paperroute_testkit.fakes import FakeKeys, FakeWifi

LINK: WifiStatus = WifiStatus(managed=True, ssid='Home', dbm=-60)


def probe_of(*, root: Path, version_file: Path | None) -> SystemProbe:
    """
    Make a probe on fakes over a fake system root.

    Parameters
    ----------
    root : Path
        The fake system root.
    version_file : Path | None
        The version file.

    Returns
    -------
    SystemProbe
        The probe.
    """
    return SystemProbe(
        wifi=FakeWifi(link=LINK),
        key=FakeKeys(key='secret'),
        tiles=NullTiles(),
        last_error=LastError(),
        data_dir=root,
        version_file=version_file,
        root=root,
    )


def write(*, path: Path, text: str) -> None:
    """
    Write a file, making its directories.

    Parameters
    ----------
    path : Path
        The file.
    text : str
        Its text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


async def test_probe_reads_system_files(tmp_path: Path) -> None:
    """Uptime, temperature, memory, clock and version come from files."""
    write(path=tmp_path / 'proc/uptime', text='7440.12 1000.0\n')
    write(
        path=tmp_path / 'sys/class/thermal/thermal_zone0/temp',
        text='48123\n',
    )
    write(
        path=tmp_path / 'proc/meminfo',
        text='MemTotal: 999 kB\nMemAvailable: 137216 kB\n',
    )
    write(path=tmp_path / 'run/systemd/timesync/synchronized', text='')
    write(path=tmp_path / 'VERSION', text='a0e68ae\n')
    facts = await probe_of(
        root=tmp_path, version_file=tmp_path / 'VERSION',
    ).probe()
    assert facts.uptime_s == 7440.12
    assert facts.cpu_c == 48.1
    assert facts.mem_free == 140509184
    assert facts.clock_synced is True
    assert facts.version == 'a0e68ae'
    assert facts.disk_free is not None
    assert (facts.wifi, facts.key_set, facts.tiles_built) == (
        LINK,
        True,
        None,
    )
    assert facts.last_error is None


async def test_probe_without_files_gives_none(tmp_path: Path) -> None:
    """Missing files give None, and no version file gives 'dev'."""
    facts = await probe_of(root=tmp_path, version_file=None).probe()
    assert facts.uptime_s is None
    assert facts.cpu_c is None
    assert facts.mem_free is None
    assert facts.clock_synced is None
    assert facts.version == 'dev'


async def test_probe_clock_not_synced(tmp_path: Path) -> None:
    """A timesync directory without the flag file is not synced."""
    (tmp_path / 'run/systemd/timesync').mkdir(parents=True)
    facts = await probe_of(
        root=tmp_path, version_file=tmp_path / 'missing',
    ).probe()
    assert facts.clock_synced is False
    assert facts.version == 'dev'


async def test_probe_without_data_dir(tmp_path: Path) -> None:
    """A data directory that is gone gives no disk space."""
    probe = SystemProbe(
        wifi=FakeWifi(),
        key=FakeKeys(),
        tiles=NullTiles(),
        last_error=LastError(),
        data_dir=tmp_path / 'gone',
        version_file=None,
        root=tmp_path,
    )
    facts = await probe.probe()
    assert facts.disk_free is None
    assert facts.key_set is False


def test_mem_available_without_the_line() -> None:
    """Meminfo with no MemAvailable line gives None."""
    assert mem_available(meminfo='MemTotal: 9 kB\n') is None
    assert mem_available(meminfo='MemAvailable: x kB\n') is None
