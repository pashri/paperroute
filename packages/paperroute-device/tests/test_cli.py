"""Tests for the kit's `paperroute-board` command line."""

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, tzinfo
from http import HTTPStatus
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import aiohttp
import pytest

import paperroute_device.adapters.tk_monitor as tk_monitor
from paperroute_device import cli
from paperroute_device.adapters.resets import FileResets, NullResets
from paperroute_device.adapters.state_file import DEFAULT_PATH, StateFile
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.core.device_model import DeviceKey
from paperroute_device.core.kit_events import QuitRequested
from paperroute_device.logs import LastError
from paperroute_device.runtime.ports import KitPorts
from paperroute_device_testkit.device_fakes import FakeKeys, FixedClock
from paperroute_device_testkit.device_pages import TEST_KEY
from tests.core.fake_app import START, FakeApp

HOBART: Final[ZoneInfo] = ZoneInfo('Australia/Hobart')


class ZonedApp(FakeApp):
    """
    An app with a zone and a flag of its own, whose keys must not be read.

    Attributes
    ----------
    keys_read : int
        Times `keys` was called.
    """

    def __init__(self) -> None:
        super().__init__()
        self.keys_read = 0

    def zone(self) -> tzinfo | None:
        """
        Give Hobart's zone.

        Returns
        -------
        tzinfo | None
            Hobart.
        """
        return HOBART

    def add_arguments(self, *, parser: argparse.ArgumentParser) -> None:
        """
        Add `--colour`.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The run command's parser.
        """
        parser.add_argument('--colour', default='red')

    def keys(self) -> tuple[DeviceKey, ...]:
        """
        Count a read of the keys.

        Returns
        -------
        tuple[DeviceKey, ...]
            None.
        """
        self.keys_read += 1
        return ()


class KeyedApp(FakeApp):
    """
    An app with two keys and a file of its own to erase.

    Parameters
    ----------
    keys : tuple[DeviceKey, ...]
        The keys.
    data : Path
        The app's file.
    """

    def __init__(self, *, keys: tuple[DeviceKey, ...], data: Path) -> None:
        super().__init__()
        self._keys = keys
        self._data = data

    def keys(self) -> tuple[DeviceKey, ...]:
        """
        Give the keys.

        Returns
        -------
        tuple[DeviceKey, ...]
            The keys.
        """
        return self._keys

    def erased(self, *, args: argparse.Namespace) -> tuple[Path, ...]:
        """
        Name the app's file.

        Parameters
        ----------
        args : argparse.Namespace
            Ignored.

        Returns
        -------
        tuple[Path, ...]
            The file.
        """
        del args
        return (self._data,)


class CrashApp(FakeApp):
    """An app whose adapters fail to open."""

    @contextmanager
    def opened(
        self, *, args: argparse.Namespace, last_error: LastError,
    ) -> Iterator[None]:
        """
        Fail to open.

        Parameters
        ----------
        args : argparse.Namespace
            Ignored.
        last_error : LastError
            Ignored.

        Yields
        ------
        None
            Never.

        Raises
        ------
        OSError
            Always.
        """
        del args, last_error
        raise OSError('unable to open the app data')
        yield  # pylint: disable=unreachable


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


def test_zone_prints_the_apps_zone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The zone command prints the app's IANA zone and a newline."""
    cli.main(app=ZonedApp(), argv=['zone'])
    assert capsys.readouterr() == ('Australia/Hobart\n', '')


def test_zone_without_a_zone_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An app with no zone exits 1 and says so."""
    with pytest.raises(SystemExit) as info:
        cli.main(app=FakeApp(), argv=['zone'])
    assert info.value.code == cli.NO_ZONE
    assert cli.NO_ZONE == 'paperroute-board: this app has no time zone'
    assert capsys.readouterr().out == ''


def test_zone_needs_no_key_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The zone command reads no key and needs no PAPERROUTE_ variable."""
    for name in list(os.environ):
        if name.startswith('PAPERROUTE_'):
            monkeypatch.delenv(name)
    app = ZonedApp()
    cli.main(app=app, argv=['zone'])
    assert capsys.readouterr().out == 'Australia/Hobart\n'
    assert app.keys_read == 0


def test_app_arguments_parse() -> None:
    """The app's flags sit beside the kit's on the run command."""
    args = cli.parse_args(
        app=ZonedApp(),
        argv=['run', '--colour', 'blue', '--power', '--state', 'x.json'],
    )
    assert (args.command, args.colour, args.power) == ('run', 'blue', True)
    assert args.state == Path('x.json')
    with pytest.raises(SystemExit):
        cli.parse_args(app=FakeApp(), argv=['run', '--colour', 'blue'])


def test_defaults_are_tk_on_port_80() -> None:
    """The defaults are the Tk window, all interfaces and port 80."""
    args = cli.parse_args(app=FakeApp(), argv=['run'])
    assert (args.display, args.host, args.port) == ('tk', '0.0.0.0', 80)
    assert args.error_log is None
    assert args.state == DEFAULT_PATH
    assert not (args.panel or args.knobs or args.power or args.wifi)
    assert args.version_file is None


async def test_device_for_probes_and_resets_every_key(
    tmp_path: Path,
) -> None:
    """Every key is probed and reset, with the app's files erased."""
    first, second = FakeKeys(key='k' * 32), FakeKeys()
    keys = (
        DeviceKey(
            name='a', credential=TEST_KEY, store=first, display_name='A',
        ),
        DeviceKey(
            name='b', credential=TEST_KEY, store=second, display_name='B',
        ),
    )
    app = KeyedApp(keys=keys, data=tmp_path / 'app.db')
    args = argparse.Namespace(
        wifi=False,
        power=True,
        state=tmp_path / 'state.json',
        version_file=None,
    )
    last_error = LastError(zone=HOBART)
    device = cli.device_for(args=args, app=app, last_error=last_error)
    assert device.keys == keys
    assert device.probe is not None
    assert (await device.probe.probe()).missing_keys == ('B',)
    assert isinstance(device.resets, FileResets)
    assert device.resets.keys == (first, second)
    assert device.resets.erased == (tmp_path / 'app.db',)
    keyless = cli.device_for(
        args=argparse.Namespace(**{**vars(args), 'power': False}),
        app=FakeApp(),
        last_error=last_error,
    )
    assert keyless.keys == ()
    assert isinstance(keyless.resets, NullResets)


@pytest.mark.usefixtures('restored_logging')
def test_main_logs_a_startup_crash_to_the_error_log(tmp_path: Path) -> None:
    """A crash opening the app reaches errors.jsonl, then propagates."""
    error_log = tmp_path / 'log' / 'errors.jsonl'
    with pytest.raises(OSError, match='unable to open'):
        cli.main(
            app=CrashApp(),
            argv=[
                'run', '--display', 'web', '--host', '127.0.0.1',
                '--port', '0', '--state', str(tmp_path / 'state.json'),
                '--error-log', str(error_log),
            ],
        )
    lines = error_log.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['level'] == 'ERROR'
    assert 'OSError' in record['exc_info']


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

    Raises
    ------
    AssertionError
        When the server stops before serving a frame.
    """
    async with aiohttp.ClientSession() as session:
        while True:
            if server.done():
                server.result()
                raise AssertionError('server stopped before serving')
            try:
                async with session.get(
                    f'http://127.0.0.1:{port}/frame.png',
                ) as response:
                    if response.status == HTTPStatus.OK:
                        return
            except aiohttp.ClientConnectionError:
                pass
            await asyncio.sleep(0.05)


async def test_serve_web_runs_and_stops(tmp_path: Path) -> None:
    """The kit serves the app's frame over HTTP and stops on SIGTERM."""
    app = FakeApp()
    display = WebDisplay()
    ports = KitPorts(
        display=display,
        clock=FixedClock(at=START),
        states=StateFile(path=tmp_path / 'state.json', app=app),
    )
    port = free_port()
    server = asyncio.create_task(
        cli.serve_web(
            app=app,
            ports=ports,
            display=display,
            host='127.0.0.1',
            port=port,
            zone=UTC,
            tick_every=0.01,
        ),
    )
    try:
        async with asyncio.timeout(30):
            await wait_for_frame(port=port, server=server)
            signal.raise_signal(signal.SIGTERM)
            await server
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        assert (tmp_path / 'state.json').is_file()
    finally:
        server.cancel()
        await asyncio.gather(server, return_exceptions=True)


def test_web_main_logs_a_bind_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A port that is taken is logged with its address and exits 1."""
    with socket.socket() as held:
        held.bind(('127.0.0.1', 0))
        held.listen()
        port = held.getsockname()[1]
        app = ZonedApp()
        args = cli.parse_args(
            app=app,
            argv=[
                'run', '--display', 'web', '--host', '127.0.0.1',
                '--port', str(port), '--state', str(tmp_path / 'state.json'),
            ],
        )
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as info:
            cli.web_main(args=args, app=app, last_error=LastError(zone=UTC))
    assert info.value.code == 1
    assert f'cannot serve on 127.0.0.1:{port}' in caplog.text


class FakeMonitor:
    """
    A stand-in for the Tk window that asks to quit on its first pump.

    Attributes
    ----------
    closed : bool
        Whether the window was closed.
    """

    def __init__(self) -> None:
        self.closed = False
        self._post: Callable[[object], None] | None = None

    def show(self, **kwargs: object) -> None:
        """
        Show nothing.

        Parameters
        ----------
        **kwargs : object
            Ignored.
        """
        del kwargs

    def settle(self, *, timeout: float) -> bool:
        """
        Report every frame shown.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            Always True.
        """
        del timeout
        return True

    def pump(self) -> None:
        """Post a quit once."""
        if self._post is not None:
            self._post(QuitRequested())
            self._post = None

    def bind(self, *, post: Callable[[object], None]) -> None:
        """
        Keep the event poster.

        Parameters
        ----------
        post : Callable[[object], None]
            Posts an event to the kit.
        """
        self._post = post

    def close(self) -> None:
        """Note that the window was closed."""
        self.closed = True


def test_tk_main_runs_the_app_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window path runs the app until it quits, saves, then closes."""
    monitor = FakeMonitor()
    monkeypatch.setattr(tk_monitor, 'TkMonitor', lambda: monitor)
    args = cli.parse_args(
        app=FakeApp(),
        argv=['run', '--state', str(tmp_path / 'state.json')],
    )
    cli.tk_main(args=args, app=FakeApp(), last_error=LastError(zone=UTC))
    assert monitor.closed
    assert (tmp_path / 'state.json').is_file()
