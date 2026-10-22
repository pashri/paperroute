"""Tests for the file holding the TfNSW API key."""

import logging
import stat
from pathlib import Path
from typing import Final

import pytest
from aiohttp.test_utils import TestServer

from paperroute.adapters.secrets_file import SecretsFile, key_path_from_env
from paperroute.adapters.tfnsw import AiohttpFeedsClient, FeedError
from tests.adapters.test_tfnsw import recording_server

FAKE_KEY: Final[str] = 'fake-key-0123456789abcdef'
OTHER_KEY: Final[str] = 'fake-key-fedcba9876543210'


def test_secrets_round_trip(tmp_path: Path) -> None:
    """A written key reads back, from a file only its owner can read."""
    path = tmp_path / 'secrets'
    secrets = SecretsFile(path=path)
    secrets.write(key=f'  {FAKE_KEY}\n')
    assert secrets.read() == FAKE_KEY
    assert secrets.present()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob('.secrets.*'))


def test_missing_and_empty_read_none(tmp_path: Path) -> None:
    """No file, or a blank one, means no key."""
    path = tmp_path / 'secrets'
    secrets = SecretsFile(path=path)
    assert secrets.read() is None
    assert not secrets.present()
    path.write_text('\n', encoding='utf-8')
    assert secrets.read() is None


@pytest.mark.parametrize(
    'key',
    ['short', 'fake key with a space', 'k' * 5_000],
)
def test_write_rejects_bad_key(tmp_path: Path, key: str) -> None:
    """A key of the wrong length or with spaces is refused."""
    path = tmp_path / 'secrets'
    with pytest.raises(ValueError) as caught:
        SecretsFile(path=path).write(key=key)
    assert key not in str(caught.value)
    assert not path.exists()


def test_read_sees_a_new_key(tmp_path: Path) -> None:
    """A key written later is read without making a new reader."""
    path = tmp_path / 'secrets'
    reader = SecretsFile(path=path)
    SecretsFile(path=path).write(key=FAKE_KEY)
    assert reader.read() == FAKE_KEY
    SecretsFile(path=path).write(key=OTHER_KEY)
    assert reader.read() == OTHER_KEY


def test_clear_empties_the_file(tmp_path: Path) -> None:
    """Clearing leaves an empty, private file and no key."""
    path = tmp_path / 'secrets'
    secrets = SecretsFile(path=path)
    secrets.write(key=FAKE_KEY)
    secrets.clear()
    assert path.read_text(encoding='utf-8') == ''
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert secrets.read() is None


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
