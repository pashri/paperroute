"""Paths under the timetable data directory."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Layout:
    """Paths under the data directory."""

    data_dir: Path

    @property
    def work(self) -> Path:
        """
        Build scratch directory.

        Returns
        -------
        Path
            The work directory under data_dir.
        """
        return self.data_dir / 'work'

    @property
    def sources(self) -> Path:
        """
        Directory of the last good zips and their states.

        Returns
        -------
        Path
            The sources directory under data_dir.
        """
        return self.data_dir / 'sources'

    @property
    def states(self) -> Path:
        """
        Stored source states.

        Returns
        -------
        Path
            The sources.json file in the sources directory.
        """
        return self.sources / 'sources.json'

    @property
    def timetable(self) -> Path:
        """
        Live timetable.

        Returns
        -------
        Path
            The timetable.db file under data_dir.
        """
        return self.data_dir / 'timetable.db'

    @property
    def lock(self) -> Path:
        """
        Lock file held for the whole refresh.

        Returns
        -------
        Path
            The refresh.lock file under data_dir.
        """
        return self.data_dir / 'refresh.lock'
