"""Tests for the TfNSW API key file as the app uses it."""

import logging
from pathlib import Path
from typing import Final

import pytest
from aiohttp.test_utils import TestServer

from paperroute.adapters.tfnsw import (
    AiohttpFeedsClient,
    FeedError,
    key_path_from_env,
)
from paperroute_device.adapters.secrets_file import SecretsFile
from tests.adapters.test_tfnsw import recording_server

FAKE_KEY: Final[str] = 'fake-key-0123456789abcdef'


async def test_key_never_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No log record holds the key, whatever is done with it."""
    caplog.set_level(logging.DEBUG)
    secrets = SecretsFile(path=tmp_path / 'secrets')
    secrets.write(key=FAKE_KEY)
    secrets.read()
    with pytest.raises(ValueError):
        secrets.write(key=f'{FAKE_KEY} x')
    server: TestServer = await recording_server(seen=[])
    client = AiohttpFeedsClient(
        key=secrets.read,
        base_url=str(server.make_url('/missing/')),
    )
    try:
        with pytest.raises(FeedError):
            await client.fetch(feed='v1/gtfs/realtime/buses')
    finally:
        await client.close()
        await server.close()
    secrets.clear()
    assert all(FAKE_KEY not in r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in caplog.text


def test_key_path_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path comes from PAPERROUTE_API_KEY_FILE; unset, the app exits."""
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(tmp_path / 'secrets'))
    assert key_path_from_env() == tmp_path / 'secrets'
    monkeypatch.delenv('PAPERROUTE_API_KEY_FILE')
    with pytest.raises(SystemExit, match='PAPERROUTE_API_KEY_FILE'):
        key_path_from_env()
