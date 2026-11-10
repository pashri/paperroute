"""Reading the device's facts from the system."""

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from paperroute_device.core.device_model import (
    DeviceFacts,
    DeviceKey,
    WifiStatus,
    missing_keys,
)
from paperroute_device.ports import WifiControl
from paperroute_transit.logs import LastError
from paperroute_transit.ports import TileRepository

UPTIME: Final[str] = 'proc/uptime'
TEMPERATURE: Final[str] = 'sys/class/thermal/thermal_zone0/temp'
MEMINFO: Final[str] = 'proc/meminfo'
TIMESYNC: Final[str] = 'run/systemd/timesync'
DEV_VERSION: Final[str] = 'dev'
KIB: Final[int] = 1024


def read_text(*, path: Path) -> str | None:
    """
    Read a small text file.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    str | None
        Its text, or None when it can't be read.
    """
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def mem_available(*, meminfo: str) -> int | None:
    """
    Find the memory available in /proc/meminfo's text.

    Parameters
    ----------
    meminfo : str
        The file's text.

    Returns
    -------
    int | None
        Bytes available, or None when the line is missing or unreadable.
    """
    for line in meminfo.splitlines():
        name, _, value = line.partition(':')
        if name != 'MemAvailable':
            continue
        words = value.split()
        if not words or not words[0].isdigit():
            return None
        return int(words[0]) * KIB
    return None


def uptime(*, text: str | None) -> float | None:
    """
    Read the seconds since boot from /proc/uptime's text.

    Parameters
    ----------
    text : str | None
        The file's text.

    Returns
    -------
    float | None
        Seconds, or None when unreadable.
    """
    try:
        return float((text or '').split()[0])
    except (IndexError, ValueError):
        return None


def temperature(*, text: str | None) -> float | None:
    """
    Read the CPU temperature from a thermal zone's text.

    Parameters
    ----------
    text : str | None
        The file's text, in milli-degrees Celsius.

    Returns
    -------
    float | None
        Degrees Celsius to 0.1, or None when unreadable.
    """
    try:
        return round(int((text or '').strip()) / 1000, 1)
    except ValueError:
        return None


def disk_free(*, path: Path) -> int | None:
    """
    Measure the free space on a directory's disk.

    Parameters
    ----------
    path : Path
        The directory.

    Returns
    -------
    int | None
        Bytes free, or None when it can't be measured.
    """
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


class SystemProbe:
    """
    Gathers the device's facts from /proc, /sys and the adapters.

    Parameters
    ----------
    wifi : WifiControl
        Reports the Wi-Fi link.
    keys : Sequence[DeviceKey]
        Each says whether it is set.
    tiles : TileRepository
        Says when the map tiles were built.
    last_error : LastError
        Holds the latest error logged.
    data_dir : Path
        The directory whose disk space is reported.
    version_file : Path | None
        Holds the release installed; without one it is 'dev'.
    root : Path
        Where /proc, /sys and /run are found.
    """

    def __init__(
        self,
        *,
        wifi: WifiControl,
        keys: Sequence[DeviceKey],
        tiles: TileRepository,
        last_error: LastError,
        data_dir: Path,
        version_file: Path | None,
        root: Path = Path('/'),
    ) -> None:
        self._wifi = wifi
        self._keys = tuple(keys)
        self._tiles = tiles
        self._last_error = last_error
        self._data_dir = data_dir
        self._version_file = version_file
        self._root = root

    def version(self) -> str:
        """
        Read the release installed.

        Returns
        -------
        str
            The version file's stripped text, or 'dev' when there is
            none or it is empty.
        """
        if self._version_file is None:
            return DEV_VERSION
        text = read_text(path=self._version_file)
        return (text or '').strip() or DEV_VERSION

    def clock_synced(self) -> bool | None:
        """
        Say whether systemd-timesyncd has set the clock.

        Returns
        -------
        bool | None
            Whether its flag file exists; None without timesyncd.
        """
        directory = self._root / TIMESYNC
        if not directory.is_dir():
            return None
        return (directory / 'synchronized').exists()

    def read(self, *, wifi: WifiStatus) -> DeviceFacts:
        """
        Read the device's facts from files, blocking.

        Parameters
        ----------
        wifi : WifiStatus
            The Wi-Fi link, found already.

        Returns
        -------
        DeviceFacts
            What was found; a file that can't be read gives None.
        """
        meminfo = read_text(path=self._root / MEMINFO) or ''
        return DeviceFacts(
            wifi=wifi,
            clock_synced=self.clock_synced(),
            uptime_s=uptime(text=read_text(path=self._root / UPTIME)),
            cpu_c=temperature(text=read_text(path=self._root / TEMPERATURE)),
            disk_free=disk_free(path=self._data_dir),
            mem_free=mem_available(meminfo=meminfo),
            version=self.version(),
            last_error=self._last_error.text(),
            tiles_built=self._tiles.version(),
            missing_keys=missing_keys(keys=self._keys),
        )

    async def probe(self) -> DeviceFacts:
        """
        Gather the device's facts, reading files off the event loop.

        Returns
        -------
        DeviceFacts
            What was found; a file that can't be read gives None.
        """
        wifi = await self._wifi.status()
        return await asyncio.to_thread(self.read, wifi=wifi)
