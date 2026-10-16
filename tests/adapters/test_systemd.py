"""Tests for the systemd notifier."""

import logging
import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from paperroute.adapters.systemd import SystemdNotifier, notify_address


@pytest.fixture(name='sock_dir')
def _sock_dir() -> Iterator[Path]:
    """A short directory, as macOS caps AF_UNIX paths at 104 bytes."""
    path = Path(tempfile.mkdtemp(dir='/tmp'))
    yield path
    shutil.rmtree(path)


def test_notifier_sends_ready_once_and_watchdog(sock_dir: Path) -> None:
    """READY=1 goes out on the first frame only, then WATCHDOG=1."""
    path = sock_dir / 'n.sock'
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(path))
        server.settimeout(1)
        notifier = SystemdNotifier(socket_path=str(path))
        notifier.frame_drawn(render_ms=1.0)
        notifier.frame_drawn(render_ms=1.0)
        notifier.feed_parsed(
            feed='f', fetch_ms=1.0, parse_ms=1.0, trips=1
        )
        notifier.ticked()
        got = [server.recv(64), server.recv(64)]
        server.settimeout(0.1)
        with pytest.raises(TimeoutError):
            server.recv(64)
    assert got == [b'READY=1', b'WATCHDOG=1']


def test_notifier_without_socket_is_silent() -> None:
    """With no socket path every call does nothing."""
    notifier = SystemdNotifier(socket_path=None)
    notifier.frame_drawn(render_ms=1.0)
    notifier.ticked()


def test_notifier_from_environment_reads_notify_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The socket path comes from NOTIFY_SOCKET."""
    monkeypatch.setenv('NOTIFY_SOCKET', '/run/x')
    assert SystemdNotifier.from_environment().socket_path == '/run/x'
    monkeypatch.delenv('NOTIFY_SOCKET')
    assert SystemdNotifier.from_environment().socket_path is None


def test_notifier_logs_and_continues_when_socket_gone(
    sock_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing listener is logged as a warning, not raised."""
    notifier = SystemdNotifier(socket_path=str(sock_dir / 'none.sock'))
    with caplog.at_level(logging.WARNING):
        notifier.frame_drawn(render_ms=1.0)
        notifier.ticked()
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_notify_address_maps_abstract_namespace() -> None:
    """A leading '@' becomes a NUL; a filesystem path is unchanged."""
    assert notify_address(path='@/org/x') == '\x00/org/x'
    assert notify_address(path='/run/x') == '/run/x'


def test_notifier_retries_ready_after_failed_send(sock_dir: Path) -> None:
    """READY=1 is resent on the next frame if the first send failed."""
    path = sock_dir / 'n.sock'
    notifier = SystemdNotifier(socket_path=str(path))
    notifier.frame_drawn(render_ms=1.0)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(path))
        server.settimeout(1)
        notifier.frame_drawn(render_ms=1.0)
        notifier.frame_drawn(render_ms=1.0)
        assert server.recv(64) == b'READY=1'
        server.settimeout(0.1)
        with pytest.raises(TimeoutError):
            server.recv(64)


def test_notifier_warns_once_per_outage(
    sock_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Repeated failures log once; a success re-arms the warning."""
    path = sock_dir / 'n.sock'
    notifier = SystemdNotifier(socket_path=str(path))
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            notifier.ticked()
    assert len(caplog.records) == 1
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(path))
        notifier.ticked()
    path.unlink()
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        notifier.ticked()
        notifier.ticked()
    assert len(caplog.records) == 1


def test_notifier_does_not_block_when_the_queue_is_full(
    sock_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A full notify queue is one quiet warning, not a stalled send."""
    path = sock_dir / 'n.sock'
    notifier = SystemdNotifier(socket_path=str(path))
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(path))
        server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        with caplog.at_level(logging.WARNING):
            for _ in range(100_000):
                notifier.ticked()
                if caplog.records:
                    break
    assert len(caplog.records) == 1
    assert 'systemd notify failed' in caplog.text
