"""Reset the settings or erase everything the device keeps."""

import logging
from pathlib import Path

from paperroute.core.device_model import ResetKind
from paperroute.ports import KeyStore, WifiControl

logger = logging.getLogger(__name__)


class NullResets:
    """Resets that never act, for development machines."""

    def __init__(self) -> None:
        self.logged = False

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Do nothing, saying so the first time.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.
        """
        if not self.logged:
            logger.warning('reset not enabled', extra={'kind': kind.value})
            self.logged = True


def inside(*, path: Path, folder: Path) -> bool:
    """
    Say whether a path, with every link followed, is inside a folder.

    Parameters
    ----------
    path : Path
        The path.
    folder : Path
        The folder, already resolved.

    Returns
    -------
    bool
        True when the resolved path is within the folder.
    """
    return path.resolve().is_relative_to(folder)


class FileResets:
    """
    Delete the device's files, clear its key and forget its Wi-Fi.

    Resetting settings deletes the state file and the ones moved aside
    beside it; erasing everything also deletes the timetable. The map
    tiles are always kept. A path that resolves outside the data
    directory is never deleted.

    Parameters
    ----------
    data_dir : Path
        The only directory files are deleted from.
    state : Path
        The state file.
    key : KeyStore
        Holds the API key.
    wifi : WifiControl
        Forgets the saved Wi-Fi networks.
    timetable : Path
        The timetable file.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        state: Path,
        key: KeyStore,
        wifi: WifiControl,
        timetable: Path,
    ) -> None:
        self.data_dir = data_dir
        self.state = state
        self.key = key
        self.wifi = wifi
        self.timetable = timetable

    def doomed(self, *, kind: ResetKind) -> list[Path]:
        """
        List the files a reset deletes.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.

        Returns
        -------
        list[Path]
            The state file, the ones moved aside, and for ERASE the
            timetable. The map tiles are never listed.
        """
        aside = sorted(self.state.parent.glob(f'{self.state.name}.bad-*'))
        paths = [self.state, *aside]
        if kind is ResetKind.ERASE:
            paths.append(self.timetable)
        return paths

    def delete(self, *, path: Path) -> None:
        """
        Delete a file inside the data directory; failures are logged.

        Parameters
        ----------
        path : Path
            The file.
        """
        if not inside(path=path, folder=self.data_dir.resolve()):
            logger.error(
                'reset refused a path outside the data directory',
                extra={'path': str(path)},
            )
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception(
                'reset could not delete a file', extra={'path': str(path)},
            )

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Delete the files, clear the key and forget the Wi-Fi networks.

        Each part that fails is logged and the rest still run.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.
        """
        logger.warning('resetting', extra={'kind': kind.value})
        for path in self.doomed(kind=kind):
            self.delete(path=path)
        try:
            self.key.clear()
        except OSError:
            logger.exception('reset could not clear the key')
        await self.forget_networks()

    async def forget_networks(self) -> None:
        """Forget every saved Wi-Fi network, logging a failure."""
        try:
            await self.wifi.forget_all()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception('reset could not forget the Wi-Fi networks')
