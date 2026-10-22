"""Tests for the command line and the web serving loop."""

import argparse
import asyncio
import json
import logging
import signal
import socket
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import aiohttp
import pytest

import paperroute.__main__ as cli
import paperroute.adapters.tk_monitor as tk_monitor
from paperroute.__main__ import (
    builder_for,
    device_pages_for,
    main,
    parse_args,
    power_for,
    resets_for,
    seed_for,
    serve_or_exit,
    serve_web,
    tiles_for,
    web_main,
    wifi_for,
)
from paperroute.adapters.clocks import ReplayClock
from paperroute.adapters.feed_files import FileFeedsClient
from paperroute.adapters.power import NullPower, SystemctlPower
from paperroute.adapters.rebuild import ChildRebuilder, NullBuilder
from paperroute.adapters.resets import FileResets, NullResets
from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.adapters.web_display import WebDisplay
from paperroute.adapters.wifi import NmcliWifi, NullWifi
from paperroute.app import Ports
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.messages import Event
from paperroute.core.model import SYDNEY
from paperroute.core.tiles import TileVariant
from paperroute.observers import Timings
from tests.core.factories import pin_of
from tests.fakes import FakeKeys, FakeNetwork, FakeWifi, MemoryStates

if TYPE_CHECKING:
    from paperroute.adapters.tk_monitor import TkMonitor

REAL = Path(__file__).parent / 'fixtures' / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=SYDNEY)
ORIGIN: Final[str] = '215020'
DESTINATION: Final[str] = '200060'
PIN = pin_of(ORIGIN, DESTINATION)


def test_pin_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --from and --to, the environment supplies the pin."""
    monkeypatch.setenv('PAPERROUTE_FROM', '215020')
    monkeypatch.setenv('PAPERROUTE_TO', '200060')
    monkeypatch.setattr(
        sys, 'argv', ['paperroute', 'run', '--timetable', 'x.db']
    )
    args = parse_args()
    assert (args.origin, args.destination) == ('215020', '200060')


def test_device_pages_only_for_live_feeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live feeds get the setup and Device pages; replayed feeds none."""
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(tmp_path / 'key'))
    timings = Timings()
    display = WebDisplay(status=timings.snapshot)
    args = argparse.Namespace(
        feeds='live', error_log=None, state=tmp_path / 'state.json',
    )
    pages = device_pages_for(args=args, timings=timings, display=display)
    assert pages is not None
    assert len(pages.routes()) == 10
    args.feeds = str(tmp_path)
    assert device_pages_for(
        args=args, timings=timings, display=display,
    ) is None


def test_seed_for_builds_bare_places() -> None:
    """--from and --to become places with no names or points yet."""
    args = argparse.Namespace(origin=ORIGIN, destination=DESTINATION)
    assert seed_for(args=args) == PIN


def test_seed_for_is_none_without_a_pin() -> None:
    """With neither --from nor --to there is no seed."""
    args = argparse.Namespace(origin=None, destination=None)
    assert seed_for(args=args) is None


@pytest.mark.parametrize(
    ('given', 'seeded'),
    [
        ([], False),
        (['--from', 'a', '--to', 'b'], True),
    ],
)
def test_main_accepts_neither_or_both_seeds(
    monkeypatch: pytest.MonkeyPatch, given: list[str], seeded: bool
) -> None:
    """Neither or both of --from and --to parse; the state path defaults."""
    monkeypatch.delenv('PAPERROUTE_FROM', raising=False)
    monkeypatch.delenv('PAPERROUTE_TO', raising=False)
    monkeypatch.setattr(
        sys, 'argv', ['paperroute', 'run', '--timetable', 'x.db', *given]
    )
    args = parse_args()
    assert (seed_for(args=args) is not None) == seeded
    assert args.state == Path('/var/lib/paperroute/state.json')


@pytest.mark.parametrize('given', [['--from', 'a'], ['--to', 'b']])
def test_main_rejects_one_seed(
    monkeypatch: pytest.MonkeyPatch, given: list[str]
) -> None:
    """Only one of --from and --to exits with a message."""
    monkeypatch.delenv('PAPERROUTE_FROM', raising=False)
    monkeypatch.delenv('PAPERROUTE_TO', raising=False)
    monkeypatch.setattr(
        sys, 'argv', ['paperroute', 'run', '--timetable', 'x.db', *given]
    )
    with pytest.raises(
        SystemExit, match='give both --from and --to, or neither'
    ):
        parse_args()


def test_state_path_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """--state sets where the saved state is kept."""
    monkeypatch.setattr(
        sys,
        'argv',
        ['paperroute', 'run', '--timetable', 'x.db', '--state', 's.json'],
    )
    assert parse_args().state == Path('s.json')


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


def test_tiles_arguments_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """--tiles is an optional path and --tiles-variant defaults to grey."""
    base = [
        'paperroute', 'run', '--timetable', 'x.db', '--from', 'a', '--to', 'b',
    ]
    monkeypatch.setattr(sys, 'argv', base)
    args = parse_args()
    assert (args.tiles, args.tiles_variant) == (None, TileVariant.GREY)
    monkeypatch.setattr(
        sys,
        'argv',
        [*base, '--tiles', 't.db', '--tiles-variant', 'bits'],
    )
    args = parse_args()
    assert (args.tiles, args.tiles_variant) == (
        Path('t.db'),
        TileVariant.BITS,
    )


def test_tiles_for_tolerates_a_missing_file(tmp_path: Path) -> None:
    """A missing tiles file opens as an empty build; no file gives none."""
    with tiles_for(args=argparse.Namespace(tiles=tmp_path / 'no.db')) as tiles:
        assert isinstance(tiles, SqliteTileRepository)
        assert tiles.version() is None
    with tiles_for(args=argparse.Namespace(tiles=None)) as tiles:
        assert isinstance(tiles, NullTiles)


def test_power_for_acts_only_when_asked() -> None:
    """--power runs systemctl; without it the device never powers off."""
    on = power_for(args=argparse.Namespace(power=True))
    off = power_for(args=argparse.Namespace(power=False))
    assert isinstance(on, SystemctlPower)
    assert on.systemctl == '/usr/bin/systemctl'
    assert isinstance(off, NullPower)


def test_resets_for_without_power_is_null(tmp_path: Path) -> None:
    """Only --power resets for real, so a Mac run never deletes data."""
    args = argparse.Namespace(
        power=False,
        state=tmp_path / 'state.json',
        timetable=tmp_path / 'timetable.db',
        tiles=tmp_path / 'tiles.db',
    )
    off = resets_for(args=args, key=FakeKeys(), wifi=FakeWifi())
    args.power = True
    on = resets_for(args=args, key=FakeKeys(), wifi=FakeWifi())
    assert isinstance(off, NullResets)
    assert isinstance(on, FileResets)
    assert on.data_dir == Path('/var/lib/paperroute')


def test_wifi_for_uses_nmcli_only_when_asked() -> None:
    """--wifi drives NetworkManager; without it Wi-Fi is left alone."""
    assert isinstance(wifi_for(args=argparse.Namespace(wifi=True)), NmcliWifi)
    assert isinstance(wifi_for(args=argparse.Namespace(wifi=False)), NullWifi)


def test_builder_for_rebuilds_only_when_asked(tmp_path: Path) -> None:
    """--rebuild runs the refresh on the timetable's directory."""
    timetable = tmp_path / 'timetable.db'
    off = argparse.Namespace(rebuild=False)
    on = argparse.Namespace(
        rebuild=True, timetable=timetable, error_log=None,
    )
    builder = builder_for(args=on)
    assert isinstance(builder_for(args=off), NullBuilder)
    assert isinstance(builder, ChildRebuilder)
    assert builder.command[-1] == str(tmp_path)
    assert builder.env['TMPDIR'] == str(tmp_path / 'work')


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
        network=FakeNetwork(),
        states=MemoryStates(),
        observer=timings,
    )
    port = free_port()
    server = asyncio.create_task(
        serve_web(
            ports=ports,
            display=display,
            seed=PIN,
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
        origin=ORIGIN,
        destination=DESTINATION,
        at=AT,
        host='127.0.0.1',
        port=0,
        tiles=None,
        tiles_variant=TileVariant.GREY,
        state=tmp_path / 'state.json',
        rebuild=False,
        power=False,
        wifi=False,
        version_file=None,
        feeds=str(REAL),
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


def unopenable(*, path: Path) -> SqliteTimetableRepository:
    """
    Fail to open a timetable.

    Parameters
    ----------
    path : Path
        Ignored.

    Raises
    ------
    sqlite3.OperationalError
        Always.
    """
    raise sqlite3.OperationalError(f'unable to open {path.name}')


@pytest.mark.usefixtures('restored_logging')
def test_main_logs_a_startup_crash_to_the_error_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash opening the timetable reaches errors.jsonl, then propagates."""
    key_file = tmp_path / 'no-such-key'
    error_log = tmp_path / 'log' / 'errors.jsonl'
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(key_file))
    monkeypatch.setattr(cli, 'SqliteTimetableRepository', unopenable)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute', 'run', '--timetable', str(tmp_path / 'x.db'),
            '--from', 'a', '--to', 'b', '--feeds', 'live',
            '--error-log', str(error_log),
        ],
    )
    with pytest.raises(sqlite3.OperationalError):
        main()
    lines = error_log.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['level'] == 'ERROR'
    assert 'OperationalError' in record['exc_info']


async def test_serve_web_passes_the_tiles_variant_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve_web runs the app with the tiles variant it was given."""
    seen: list[TileVariant] = []

    async def fake_run(**kwargs: object) -> None:
        seen.append(kwargs['tiles_variant'])  # type: ignore[arg-type]

    monkeypatch.setattr(cli, 'run', fake_run)
    display = WebDisplay(status=Timings().snapshot)
    memory = SqliteTimetableRepository(connection=sqlite3.connect(':memory:'))
    with closing(memory) as timetable:
        ports = Ports(
            timetable=timetable,
            feeds=FileFeedsClient(directory=REAL),
            display=display,
            clock=ReplayClock(start=AT),
            network=FakeNetwork(),
            states=MemoryStates(),
        )
        await serve_web(
            ports=ports,
            display=display,
            seed=PIN,
            host='127.0.0.1',
            port=free_port(),
            tiles_variant=TileVariant.BITS,
        )
    assert seen == [TileVariant.BITS]


def test_serve_or_exit_passes_the_tiles_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--tiles-variant reaches serve_web."""
    seen: list[TileVariant] = []

    async def fake_serve_web(**kwargs: object) -> None:
        seen.append(kwargs['tiles_variant'])  # type: ignore[arg-type]

    monkeypatch.setattr(cli, 'serve_web', fake_serve_web)
    display = WebDisplay(status=Timings().snapshot)
    args = argparse.Namespace(
        origin=ORIGIN,
        destination=DESTINATION,
        host='127.0.0.1',
        port=0,
        tiles_variant=TileVariant.BITS,
    )
    memory = SqliteTimetableRepository(connection=sqlite3.connect(':memory:'))
    with closing(memory) as timetable:
        ports = Ports(
            timetable=timetable,
            feeds=FileFeedsClient(directory=REAL),
            display=display,
            clock=ReplayClock(start=AT),
            network=FakeNetwork(),
            states=MemoryStates(),
        )
        serve_or_exit(args=args, ports=ports, display=display)
    assert seen == [TileVariant.BITS]


@dataclass
class FakeMonitor:
    """A stand-in for the Tk window that never opens one."""

    closed: bool = False

    def pump(self) -> None:
        """Process no window events."""

    def bind(self, *, post: Callable[[Event], None]) -> None:
        """
        Ignore the event poster.

        Parameters
        ----------
        post : Callable[[Event], None]
            Ignored.
        """

    def close(self) -> None:
        """Note that the window was closed."""
        self.closed = True


def test_tk_main_passes_the_tiles_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--tiles-variant reaches show on the Tk path."""
    seen: list[TileVariant] = []
    monitor = FakeMonitor()

    async def fake_show(**kwargs: object) -> None:
        seen.append(kwargs['tiles_variant'])  # type: ignore[arg-type]

    monkeypatch.setattr(tk_monitor, 'TkMonitor', lambda: monitor)
    monkeypatch.setattr(cli, 'show', fake_show)
    timetable = tmp_path / 'timetable.db'
    timetable.touch()
    args = argparse.Namespace(
        timetable=timetable,
        origin=ORIGIN,
        destination=DESTINATION,
        at=AT,
        tiles=None,
        tiles_variant=TileVariant.BITS,
        state=tmp_path / 'state.json',
        rebuild=False,
        power=False,
        wifi=False,
        version_file=None,
        feeds=str(REAL),
    )
    cli.tk_main(args=args, feeds=FileFeedsClient(directory=REAL))
    assert seen == [TileVariant.BITS]
    assert monitor.closed


async def test_show_passes_the_tiles_variant_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """show runs the app with the tiles variant it was given."""
    seen: list[TileVariant] = []

    async def fake_run(**kwargs: object) -> None:
        seen.append(kwargs['tiles_variant'])  # type: ignore[arg-type]

    monkeypatch.setattr(cli, 'run', fake_run)
    memory = SqliteTimetableRepository(connection=sqlite3.connect(':memory:'))
    with closing(memory) as timetable:
        ports = Ports(
            timetable=timetable,
            feeds=FileFeedsClient(directory=REAL),
            display=WebDisplay(status=Timings().snapshot),
            clock=ReplayClock(start=AT),
            network=FakeNetwork(),
            states=MemoryStates(),
        )
        await cli.show(
            ports=ports,
            seed=PIN,
            monitor=cast('TkMonitor', FakeMonitor()),
            tiles_variant=TileVariant.BITS,
        )
    assert seen == [TileVariant.BITS]
