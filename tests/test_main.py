"""Tests for the command line and the web serving loop."""

import argparse
import asyncio
import json
import logging
import signal
import socket
import sys
from collections.abc import Iterator
from datetime import date, datetime
from http import HTTPStatus
from pathlib import Path

import aiohttp
import pytest

from paperroute.__main__ import main, parse_args, serve_web, web_main
from paperroute.adapters.clocks import ReplayClock
from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.adapters.web_display import WebDisplay
from paperroute.app import Ports
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.model import SYDNEY, Pin
from paperroute.observers import Timings

REAL = Path(__file__).parent / 'fixtures' / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=SYDNEY)
PIN = Pin(origin='215020', destination='200060')


def test_pin_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --from and --to, the environment supplies the pin."""
    monkeypatch.setenv('PAPERROUTE_FROM', '215020')
    monkeypatch.setenv('PAPERROUTE_TO', '200060')
    monkeypatch.setattr(
        sys, 'argv', ['paperroute', 'run', '--timetable', 'x.db']
    )
    args = parse_args()
    assert (args.origin, args.destination) == ('215020', '200060')


def test_missing_pin_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no pin anywhere, the command exits with a message."""
    monkeypatch.delenv('PAPERROUTE_FROM', raising=False)
    monkeypatch.delenv('PAPERROUTE_TO', raising=False)
    monkeypatch.setattr(
        sys, 'argv', ['paperroute', 'run', '--timetable', 'x.db']
    )
    with pytest.raises(SystemExit, match='PAPERROUTE_FROM'):
        parse_args()


def test_web_choices_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web display, host, port and error log are read."""
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute', 'run', '--timetable', 'x.db',
            '--from', 'a', '--to', 'b', '--display', 'web',
            '--host', '127.0.0.1', '--port', '8080',
            '--error-log', 'err.log',
        ],
    )
    args = parse_args()
    assert args.display == 'web'
    assert (args.host, args.port) == ('127.0.0.1', 8080)
    assert args.error_log == Path('err.log')


def test_defaults_are_tk_on_port_80(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defaults are the Tk window, all interfaces and port 80."""
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute', 'run', '--timetable', 'x.db',
            '--from', 'a', '--to', 'b',
        ],
    )
    args = parse_args()
    assert (args.display, args.host, args.port) == ('tk', '0.0.0.0', 80)
    assert args.error_log is None


def free_port() -> int:
    """
    Find a port nothing is listening on.

    Returns
    -------
    int
        Port number.
    """
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


async def wait_for_frame(*, port: int, server: asyncio.Task[None]) -> None:
    """
    Poll /frame.png until it answers 200, or raise if the server ended.

    Parameters
    ----------
    port : int
        Port to poll on 127.0.0.1.
    server : asyncio.Task[None]
        The serving task; its error is raised if it finishes early.
    """
    async with aiohttp.ClientSession() as session:
        while True:
            if server.done():
                server.result()
                raise AssertionError('server stopped before serving')
            try:
                async with session.get(
                    f'http://127.0.0.1:{port}/frame.png'
                ) as response:
                    if response.status == HTTPStatus.OK:
                        return
            except aiohttp.ClientConnectionError:
                pass
            await asyncio.sleep(0.05)


def build_db(*, path: Path) -> None:
    """
    Build the real-slice timetable database.

    Parameters
    ----------
    path : Path
        Database file to create.
    """
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=1),
        out=path,
    )


async def test_serve_web_runs_and_stops(tmp_path: Path) -> None:
    """The app serves a frame over HTTP and returns cleanly."""
    db = tmp_path / 'timetable.db'
    build_db(path=db)
    timings = Timings()
    display = WebDisplay(status=timings.snapshot)
    timetable = SqliteTimetableRepository(path=db)
    ports = Ports(
        timetable=timetable,
        feeds=FileFeedsClient(directory=REAL),
        display=display,
        clock=ReplayClock(start=AT),
        observer=timings,
    )
    port = free_port()
    server = asyncio.create_task(
        serve_web(
            ports=ports,
            display=display,
            pin=PIN,
            host='127.0.0.1',
            port=port,
            tick_every=0.01,
        )
    )
    try:
        async with asyncio.timeout(30):
            await wait_for_frame(port=port, server=server)
            signal.raise_signal(signal.SIGTERM)
            await server
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        server.cancel()
        await asyncio.gather(server, return_exceptions=True)
        timetable.close()


def test_web_main_logs_a_bind_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A port that is taken is logged with its address and exits 1."""
    args = argparse.Namespace(
        timetable=tmp_path / 'timetable.db',
        origin=PIN.origin,
        destination=PIN.destination,
        at=AT,
        host='127.0.0.1',
        port=0,
    )
    build_db(path=args.timetable)
    with socket.socket() as held:
        held.bind(('127.0.0.1', 0))
        held.listen()
        args.port = held.getsockname()[1]
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as info:
            web_main(args=args, feeds=FileFeedsClient(directory=REAL))
    assert info.value.code == 1
    assert f'cannot serve on 127.0.0.1:{args.port}' in caplog.text


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """Put the root logger back after a test that configures it."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.usefixtures('restored_logging')
def test_main_logs_a_startup_crash_to_the_error_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing key file reaches errors.jsonl, then still propagates."""
    key_file = tmp_path / 'no-such-key'
    error_log = tmp_path / 'log' / 'errors.jsonl'
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(key_file))
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute', 'run', '--timetable', str(tmp_path / 'x.db'),
            '--from', 'a', '--to', 'b', '--feeds', 'live',
            '--error-log', str(error_log),
        ],
    )
    with pytest.raises(FileNotFoundError):
        main()
    lines = error_log.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['level'] == 'ERROR'
    assert 'FileNotFoundError' in record['exc_info']
