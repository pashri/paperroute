"""Tests for the file holding an API key."""

import logging
import stat
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.adapters.secrets_file import SecretsFile

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


@pytest.mark.parametrize('kind', ['undecodable', 'directory'])
def test_unreadable_key_reads_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, kind: str
) -> None:
    """A file that cannot be read or decoded means no key, with a warning."""
    path = tmp_path / 'secrets'
    if kind == 'directory':
        path.mkdir()
    else:
        path.write_bytes(b'fake-key-\xff\xfe0123456789')
    assert SecretsFile(path=path).read() is None
    assert [r.levelname for r in caplog.records] == ['WARNING']
    assert 'secrets' in caplog.records[0].getMessage()
    assert 'fake-key' not in caplog.text
