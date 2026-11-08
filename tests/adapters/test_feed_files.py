"""Tests for the replay feed client."""

from pathlib import Path

import pytest

from paperroute.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.feed_errors import FeedError


async def test_file_client_reads_named_file(tmp_path: Path) -> None:
    """Feed paths map to underscore file names."""
    (tmp_path / 'v1_gtfs_realtime_buses.pb').write_bytes(b'abc')
    client = FileFeedsClient(directory=tmp_path)
    assert await client.fetch(feed='v1/gtfs/realtime/buses') == b'abc'
    await client.close()


async def test_file_client_missing_file_is_feed_error(tmp_path: Path) -> None:
    """A missing recording behaves like a failed download."""
    client = FileFeedsClient(directory=tmp_path)
    with pytest.raises(FeedError):
        await client.fetch(feed='v1/gtfs/realtime/buses')
