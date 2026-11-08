"""Replay recorded feeds from files, for development and tests."""

import asyncio
from pathlib import Path

from paperroute_transit.adapters.feed_errors import FeedError


class FileFeedsClient:
    """
    Feeds read from '<feed path with _ for />.pb' files in a directory.

    Parameters
    ----------
    directory : Path
        Directory of recordings.
    """

    def __init__(self, *, directory: Path) -> None:
        self.directory = directory

    async def fetch(self, *, feed: str) -> bytes:
        """
        Read one recorded feed.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            Recorded body.

        Raises
        ------
        FeedError
            When there's no recording for the feed.
        """
        path = self.directory / (feed.replace('/', '_') + '.pb')
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as error:
            raise FeedError(f'{feed}: no recording') from error

    async def close(self) -> None:
        """Release nothing; replay holds no connections."""
