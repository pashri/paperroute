"""Tests for Sydney's board on the device kit's command line."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Final, cast

import numpy as np
import pytest
from aiohttp.test_utils import TestClient, TestServer

import paperroute_device.adapters.tk_monitor as tk_monitor
from paperroute_device import cli
from paperroute_device.adapters.clocks import ReplayClock
from paperroute_device.adapters.power import NullPower, SystemctlPower
from paperroute_device.adapters.resets import FileResets, NullResets
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.adapters.web_shell import web_app
from paperroute_device.adapters.wifi import NmcliWifi, NullWifi
from paperroute_device.core.device_model import DeviceKey
from paperroute_device.logs import LastError
from paperroute_device.render.canvas import INK
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.web_observer import web_observer
from paperroute_device_testkit.device_fakes import FakeKeys, FakeWifi
from paperroute_device_testkit.hardware_fakes import (
    FakeKnobLines,
    FakePanelBus,
)
from paperroute_hardware.adapters.knob_reader import KnobReader
from paperroute_hardware.adapters.panel_display import Displays
from paperroute_hardware.core.knob_decoder import KnobDecoder
from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_hardware.panel_open import panel_for
from paperroute_hardware.ports import LineEdge, PanelBus, PanelRefresh
from paperroute_hardware.render.panel_bits import HEIGHT, WIDTH
from paperroute_sydney.__main__ import SYDNEY_PARTS, main, tfnsw_feeds
from paperroute_sydney.adapters.tfnsw import (
    TFNSW_KEY,
    AiohttpFeedsClient,
    tfnsw_limiter,
)
from paperroute_sydney.adapters.web_board import (
    SYDNEY_HINT,
    SYDNEY_NAV,
    SuggestPlug,
    sydney_status_line,
)
from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION, SYDNEY_ZONE
from paperroute_testkit.core.factories import pin_of
from paperroute_testkit.fakes import MemoryStates
from paperroute_testkit.page_markup import parse_page
from paperroute_transit import board
from paperroute_transit.adapters.checked_tiles import CheckedTiles
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.feed_guard import GuardedFeeds
from paperroute_transit.adapters.rebuild import ChildRebuilder, NullBuilder
from paperroute_transit.adapters.tiles_db import (
    NullTiles,
    SqliteTileRepository,
)
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.board import (
    builder_for,
    feeds_client,
    seed_for,
    tiles_for,
)
from paperroute_transit.build.tile_store import TileWriter
from paperroute_transit.city_parts import CityParts
from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.map_view import MAP_HEIGHT, MAP_WIDTH, MapBase
from paperroute_transit.core.tiles import (
    TILE_STYLE,
    ZOOMS,
    TileVariant,
    levels_text,
)
from paperroute_transit.observers import Timings
from paperroute_transit.ports import FeedsClient
from paperroute_transit.render.map_base import draw_map_base
from paperroute_transit.summary import Summary
from paperroute_transit.transit_app import TransitApp
from tests.adapters.transit_web import transit_display

REAL = Path(__file__).parent / 'fixtures' / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=SYDNEY_ZONE)
ORIGIN: Final[str] = '215020'
DESTINATION: Final[str] = '200060'
PIN = pin_of(ORIGIN, DESTINATION)


def sydney_app() -> TransitApp:
    """
    Make Sydney's board as the kit runs it.

    Returns
    -------
    TransitApp
        The app, with Sydney's parts.
    """
    return TransitApp(city=SYDNEY, parts=SYDNEY_PARTS)


def parsed(*, argv: Sequence[str]) -> argparse.Namespace:
    """
    Read a command line as Sydney's board does.

    Parameters
    ----------
    argv : Sequence[str]
        The arguments, without the program name.

    Returns
    -------
    argparse.Namespace
        The parsed arguments.
    """
    return cli.parse_args(app=sydney_app(), argv=argv)


def run_argv(*, tmp_path: Path, feeds: str) -> list[str]:
    """
    Give a web run command over the feeds named.

    Parameters
    ----------
    tmp_path : Path
        Where the timetable and the state file go.
    feeds : str
        'live' or a recordings directory.

    Returns
    -------
    list[str]
        The arguments.
    """
    return [
        'run', '--display', 'web', '--host', '127.0.0.1', '--port', '0',
        '--timetable', str(tmp_path / 'timetable.db'),
        '--state', str(tmp_path / 'state.json'),
        '--feeds', feeds,
    ]


def served(
    *,
    monkeypatch: pytest.MonkeyPatch,
    app: TransitApp,
    argv: Sequence[str],
) -> WebDisplay:
    """
    Run the kit's web path up to serving, and give the display it serves.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Replaces the kit's serve_or_exit, so nothing is served.
    app : TransitApp
        The app run.
    argv : Sequence[str]
        The run command.

    Returns
    -------
    WebDisplay
        The display the kit would have served.
    """
    displays: list[WebDisplay] = []

    def recorded(**kwargs: object) -> None:
        """
        Note the display instead of serving it.

        Parameters
        ----------
        **kwargs : object
            serve_or_exit's arguments.
        """
        displays.append(cast(WebDisplay, kwargs['display']))

    monkeypatch.setattr(cli, 'serve_or_exit', recorded)
    cli.web_main(
        args=cli.parse_args(app=app, argv=argv),
        app=app,
        last_error=LastError(zone=SYDNEY_ZONE),
    )
    assert len(displays) == 1
    return displays[0]


def test_pin_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --from and --to, the environment supplies the pin."""
    monkeypatch.setenv('PAPERROUTE_FROM', '215020')
    monkeypatch.setenv('PAPERROUTE_TO', '200060')
    args = parsed(argv=['run', '--timetable', 'x.db'])
    assert (args.origin, args.destination) == ('215020', '200060')


def test_device_pages_only_for_live_feeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live feeds get the setup and Device pages; replayed feeds none."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(tmp_path / 'key'))
    live = served(
        monkeypatch=monkeypatch,
        app=sydney_app(),
        argv=run_argv(tmp_path=tmp_path, feeds='live'),
    )
    assert live.device is not None
    pages = web_app(live=live.live, device=live.device).router.routes()
    bare = web_app(live=live.live, device=None).router.routes()
    assert sum(route.method != 'HEAD' for route in pages) - sum(
        route.method != 'HEAD' for route in bare
    ) == 13
    replayed = served(
        monkeypatch=monkeypatch,
        app=sydney_app(),
        argv=run_argv(tmp_path=tmp_path, feeds=str(REAL)),
    )
    assert replayed.device is None


async def test_device_pages_link_sydney_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device pages' nav links all four of Sydney's pages."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(tmp_path / 'key'))
    display = served(
        monkeypatch=monkeypatch,
        app=sydney_app(),
        argv=run_argv(tmp_path=tmp_path, feeds='live'),
    )
    assert display.device is not None
    app = web_app(live=display.live, device=display.device)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get('/device/power?action=reboot')
        text = await response.text()
    finally:
        await client.close()
    assert [href for href, _, _ in parse_page(text=text).links] == [
        '/', '/pins', '/settings', '/device',
    ]


async def test_sydney_display_serves_sydney_nav_and_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live page `/` links all four Sydney pages and shows the hint."""
    display = served(
        monkeypatch=monkeypatch,
        app=sydney_app(),
        argv=run_argv(tmp_path=tmp_path, feeds=str(REAL)),
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        text = await (await client.get('/')).text()
    finally:
        await client.close()
    hrefs = [href for href, _, _ in parse_page(text=text).links]
    for href in ('/', '/pins', '/settings', '/device'):
        assert href in hrefs
    assert SYDNEY_HINT in text


def test_panel_for_off_never_opens() -> None:
    """Without --panel the opener is not called."""
    opened: list[str] = []

    def opener() -> PanelBus:
        opened.append('opened')
        raise OSError('unused')

    panel = panel_for(enabled=False, opener=opener, report=lambda _: None)
    assert panel is None
    assert not opened


def test_panel_for_opens_a_panel_display() -> None:
    """A bus opens at the first frame, and the panel stops cleanly."""
    bus = FakePanelBus()
    panel = panel_for(
        enabled=True, opener=lambda: bus, report=lambda _: None,
    )
    assert panel is not None
    assert not bus.log
    panel.show(frame=np.full((HEIGHT, WIDTH), 255, dtype=np.uint8))
    assert panel.settle(timeout=5)
    assert bus.log
    panel.stop()


@pytest.mark.parametrize('error', [OSError('no spi'), ImportError('gpiod')])
def test_panel_open_failure_keeps_web(
    caplog: pytest.LogCaptureFixture, error: Exception,
) -> None:
    """A panel that will not open is one error log and a failed report."""

    def opener() -> PanelBus:
        raise error

    reports: list[PanelRefresh] = []
    panel = panel_for(enabled=True, opener=opener, report=reports.append)
    assert panel is not None
    with caplog.at_level(logging.ERROR):
        panel.show(frame=np.full((HEIGHT, WIDTH), 255, dtype=np.uint8))
        assert panel.settle(timeout=5)
    panel.show(frame=np.full((HEIGHT, WIDTH), 255, dtype=np.uint8))
    panel.stop()
    assert [refresh.ok for refresh in reports] == [False]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ['panel refresh failed']
    display = transit_display(status=Timings().snapshot, city=SYDNEY)
    assert Displays(displays=[display]).settle(timeout=0)


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
    args = parsed(argv=['run', '--timetable', 'x.db', *given])
    board.require_both_or_neither(args=args)
    assert (seed_for(args=args) is not None) == seeded
    assert args.state == Path('/var/lib/paperroute/state.json')


@pytest.mark.parametrize('given', [['--from', 'a'], ['--to', 'b']])
def test_main_rejects_one_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, given: list[str]
) -> None:
    """Only one of --from and --to exits at parsing, before anything opens."""
    monkeypatch.delenv('PAPERROUTE_FROM', raising=False)
    monkeypatch.delenv('PAPERROUTE_TO', raising=False)
    opened: list[str] = []
    for name in ('panel_for', 'knobs_for', 'tk_main', 'web_main'):
        monkeypatch.setattr(
            cli,
            name,
            lambda *_, name=name, **__: opened.append(name),
        )
    timetable = tmp_path / 'x.db'
    app = sydney_app()
    argv = ['run', '--timetable', str(timetable), '--panel', *given]
    with pytest.raises(SystemExit) as raised:
        cli.main(app=app, argv=argv)
    assert raised.value.code == 'give both --from and --to, or neither'
    assert not opened
    assert app.ports is None
    assert not timetable.exists()


def test_web_choices_parse() -> None:
    """The web display, host, port and error log are read."""
    args = parsed(
        argv=[
            'run', '--timetable', 'x.db',
            '--from', 'a', '--to', 'b', '--display', 'web',
            '--host', '127.0.0.1', '--port', '8080',
            '--error-log', 'err.log',
        ],
    )
    assert args.display == 'web'
    assert (args.host, args.port) == ('127.0.0.1', 8080)
    assert args.error_log == Path('err.log')


def test_knobs_flag_parses() -> None:
    """--knobs is off unless given."""
    base = ['run', '--timetable', 'x.db', '--from', 'a', '--to', 'b']
    assert parsed(argv=base).knobs is False
    assert parsed(argv=[*base, '--display', 'web', '--knobs']).knobs is True


def test_tiles_arguments_parse() -> None:
    """--tiles is an optional path and --tiles-variant defaults to grey."""
    base = ['run', '--timetable', 'x.db', '--from', 'a', '--to', 'b']
    args = parsed(argv=base)
    assert (args.tiles, args.tiles_variant) == (None, TileVariant.GREY)
    args = parsed(argv=[*base, '--tiles', 't.db', '--tiles-variant', 'bits'])
    assert (args.tiles, args.tiles_variant) == (
        Path('t.db'),
        TileVariant.BITS,
    )


def test_tiles_for_tolerates_a_missing_file(tmp_path: Path) -> None:
    """A missing tiles file opens as an empty build; no file gives none."""
    with tiles_for(
        args=argparse.Namespace(tiles=tmp_path / 'no.db'), city=SYDNEY,
    ) as tiles:
        assert isinstance(tiles, CheckedTiles)
        assert tiles.version() is None
    with tiles_for(
        args=argparse.Namespace(tiles=None), city=SYDNEY,
    ) as tiles:
        assert isinstance(tiles, NullTiles)


def old_tiles(*, out: Path) -> Path:
    """
    Write a tiles file built before the 25 m/px level and the style.

    Parameters
    ----------
    out : Path
        Destination file.

    Returns
    -------
    Path
        The file.
    """
    with TileWriter(out=out) as writer:
        writer.finish(meta={'zooms': '3,5,8,14,45'})
    return out


def test_mismatched_tiles_open_as_no_tiles(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file for other zooms or style is closed and opens as no tiles."""
    closed: list[bool] = []
    close = SqliteTileRepository.close

    def recorded(self: SqliteTileRepository) -> None:
        """Note the close, then close."""
        closed.append(True)
        close(self)

    monkeypatch.setattr(SqliteTileRepository, 'close', recorded)
    out = old_tiles(out=tmp_path / 'tiles.db')
    with (
        caplog.at_level(logging.WARNING),
        tiles_for(
            args=argparse.Namespace(tiles=out), city=SYDNEY,
        ) as tiles,
    ):
        assert isinstance(tiles, NullTiles)
        assert closed == [True]
    assert (
        f'tiles: {out} was built for zoom levels 3,5,8,14,45, style none '
        'and origin none; this release needs 3,5,8,14,25,45, '
        f'{TILE_STYLE} and 151.2093,-33.8688, so the map is off'
    ) in caplog.text


def test_tiles_installed_while_running_are_checked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A mismatched file that appears after start-up reads as no tiles."""
    out = tmp_path / 'tiles.db'
    with (
        caplog.at_level(logging.WARNING),
        tiles_for(
            args=argparse.Namespace(tiles=out), city=SYDNEY,
        ) as tiles,
    ):
        assert tiles.version() is None
        old_tiles(out=out)
        assert tiles.version() is None
        assert tiles.street_labels(zoom=8, box=(0.0, 0.0, 1.0, 1.0)) == []
        grey = TileVariant.GREY
        assert tiles.tile(zoom=8, col=0, row=0, variant=grey) is None
        assert tiles.version() is None
    assert caplog.text.count('so the map is off') == 1


def test_matching_tiles_installed_while_running_are_read(
    tmp_path: Path,
) -> None:
    """A matching file that appears after start-up is read."""
    out = tmp_path / 'tiles.db'
    with tiles_for(
        args=argparse.Namespace(tiles=out), city=SYDNEY,
    ) as tiles:
        assert tiles.version() is None
        with TileWriter(out=out) as writer:
            writer.finish(meta={
                'zooms': levels_text(zooms=ZOOMS), 'style': TILE_STYLE,
            })
        assert tiles.version() is not None
        assert tiles.street_labels(zoom=8, box=(0.0, 0.0, 1.0, 1.0)) == []
        grey = TileVariant.GREY
        assert tiles.tile(zoom=8, col=0, row=0, variant=grey) is None


def test_mismatched_tiles_draw_map_not_installed(tmp_path: Path) -> None:
    """The map says it isn't installed but keeps the From and route."""
    centre = to_metres(
        lon=151.0036,
        lat=-33.8170,
        projection=SYDNEY_PROJECTION,
    )
    x, y = centre
    base = MapBase(
        centre=centre,
        mpp=8,
        route=(((x - 3000.0, y), (x + 3000.0, y)),),
        projection=SYDNEY_PROJECTION,
    )
    out = old_tiles(out=tmp_path / 'tiles.db')
    with tiles_for(
        args=argparse.Namespace(tiles=out), city=SYDNEY,
    ) as tiles:
        grey = MapRenderer(tiles=tiles).cached(base=base)
    expected = draw_map_base(
        base=base,
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    assert np.array_equal(grey, expected)
    middle = MAP_HEIGHT // 2
    assert np.any(grey[MAP_HEIGHT - 30 :, MAP_WIDTH - 120 :] == INK)
    assert np.any(grey[middle - 3 : middle + 3, MAP_WIDTH // 2] == INK)
    assert np.any(grey[middle - 6 : middle + 6, 20] == INK)


def test_power_for_acts_only_when_asked() -> None:
    """--power runs systemctl; without it the device never powers off."""
    on = cli.power_for(args=argparse.Namespace(power=True))
    off = cli.power_for(args=argparse.Namespace(power=False))
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
    erased = sydney_app().erased(args=args)
    off = cli.resets_for(
        args=args, keys=(FakeKeys(),), wifi=FakeWifi(), erased=erased,
    )
    args.power = True
    on = cli.resets_for(
        args=args, keys=(FakeKeys(),), wifi=FakeWifi(), erased=erased,
    )
    assert isinstance(off, NullResets)
    assert isinstance(on, FileResets)
    assert on.data_dir == Path('/var/lib/paperroute')
    assert on.erased == (args.timetable,)


def test_wifi_for_uses_nmcli_only_when_asked() -> None:
    """--wifi drives NetworkManager; without it Wi-Fi is left alone."""
    on = cli.wifi_for(args=argparse.Namespace(wifi=True))
    off = cli.wifi_for(args=argparse.Namespace(wifi=False))
    assert isinstance(on, NmcliWifi)
    assert isinstance(off, NullWifi)


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


def skipped_tk_main(**kwargs: object) -> None:
    """
    Stand in for the Tk window path, opening nothing.

    Parameters
    ----------
    **kwargs : object
        Ignored.
    """
    del kwargs


@pytest.mark.usefixtures('restored_logging')
def test_main_warns_that_panel_needs_web(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--panel with --display tk is ignored with a warning."""
    monkeypatch.setattr(cli, 'tk_main', skipped_tk_main)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute', 'run', '--timetable', str(tmp_path / 'x.db'),
            '--from', 'a', '--to', 'b', '--feeds', 'files',
            '--display', 'tk', '--panel',
            '--error-log', str(tmp_path / 'errors.jsonl'),
        ],
    )
    main()
    assert '--panel only works with --display web; ignoring it' in (
        capsys.readouterr().err
    )


@pytest.mark.usefixtures('restored_logging')
def test_main_warns_that_knobs_need_web(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--knobs with --display tk is ignored with a warning."""
    monkeypatch.setattr(cli, 'tk_main', skipped_tk_main)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute', 'run', '--timetable', str(tmp_path / 'x.db'),
            '--from', 'a', '--to', 'b', '--feeds', 'files',
            '--display', 'tk', '--knobs',
            '--error-log', str(tmp_path / 'errors.jsonl'),
        ],
    )
    main()
    assert '--knobs only works with --display web; ignoring it' in (
        capsys.readouterr().err
    )


async def test_serve_web_posts_knob_events_then_stops_the_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The knobs' thread reaches the kit's queue; quitting stops it."""
    seen: list[object] = []
    lines = FakeKnobLines(reads=[[LineEdge(line=20, rising=False, at=0.0)]])
    knobs = KnobReader(
        lines=lines, decoder=KnobDecoder(swapped=lambda: False), wait=0.01,
    )

    async def fake_run(**kwargs: object) -> None:
        """
        Bind a queue as the kit's run does, then wait for one event.

        Parameters
        ----------
        **kwargs : object
            run's arguments.
        """
        queue: asyncio.Queue[object] = asyncio.Queue()
        hook = cast(
            Callable[[Callable[[object], None]], None], kwargs['post_hook'],
        )
        hook(queue.put_nowait)
        seen.append(await asyncio.wait_for(queue.get(), timeout=2))

    monkeypatch.setattr(cli, 'run', fake_run)
    display = transit_display(status=Timings().snapshot, city=SYDNEY)
    ports = KitPorts(
        display=display,
        clock=ReplayClock(start=AT, zone=SYDNEY_ZONE),
        states=MemoryStates(),
    )
    await cli.serve_web(
        app=sydney_app(),
        ports=ports,
        display=display,
        host='127.0.0.1',
        port=0,
        zone=SYDNEY_ZONE,
        knobs=knobs,
    )
    assert seen == [KnobTurned(knob=Knob.TOP, steps=1)]
    assert lines.closes == 1


def test_web_main_passes_the_tiles_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--tiles-variant reaches the map the web run draws."""
    app = sydney_app()
    served(
        monkeypatch=monkeypatch,
        app=app,
        argv=[
            *run_argv(tmp_path=tmp_path, feeds=str(REAL)),
            '--tiles-variant', 'bits',
        ],
    )
    assert app.maps.variant == TileVariant.BITS


class FakeMonitor:
    """
    A stand-in for the Tk window that never opens one.

    Attributes
    ----------
    closed : bool
        Whether the window was closed.
    """

    def __init__(self) -> None:
        self.closed = False

    def pump(self) -> None:
        """Process no window events."""

    def bind(self, *, post: Callable[[object], None]) -> None:
        """
        Ignore the event poster.

        Parameters
        ----------
        post : Callable[[object], None]
            Ignored.
        """

    def close(self) -> None:
        """Note that the window was closed."""
        self.closed = True


def test_tk_main_passes_the_tiles_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--tiles-variant reaches the map the Tk run draws."""
    seen: list[TileVariant] = []
    monitor = FakeMonitor()

    async def fake_show(**kwargs: object) -> None:
        """
        Note the variant of the app's map instead of running it.

        Parameters
        ----------
        **kwargs : object
            show's arguments.
        """
        seen.append(cast(TransitApp, kwargs['app']).maps.variant)

    monkeypatch.setattr(tk_monitor, 'TkMonitor', lambda: monitor)
    monkeypatch.setattr(cli, 'show', fake_show)
    timetable = tmp_path / 'timetable.db'
    timetable.touch()
    app = sydney_app()
    args = cli.parse_args(
        app=app,
        argv=[
            'run', '--timetable', str(timetable),
            '--from', ORIGIN, '--to', DESTINATION,
            '--at', '2026-10-16T06:25',
            '--state', str(tmp_path / 'state.json'),
            '--feeds', str(REAL),
            '--tiles-variant', 'bits',
        ],
    )
    cli.tk_main(args=args, app=app, last_error=LastError(zone=SYDNEY_ZONE))
    assert seen == [TileVariant.BITS]
    assert monitor.closed


def test_live_feeds_are_guarded_and_share_the_tfnsw_limiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live feeds back off on 429 and wait on the process's one limiter."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(tmp_path / 'key'))
    client = feeds_client(choice='live', live=tfnsw_feeds)
    assert isinstance(client, GuardedFeeds)
    assert isinstance(client.inner, AiohttpFeedsClient)
    assert client.inner.limiter is tfnsw_limiter()


def test_live_feeds_count_into_the_summary_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live client reports its requests to the run summary."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(tmp_path / 'key'))
    summary = Summary()
    client = feeds_client(
        choice='live', live=tfnsw_feeds, summary=summary,
    )
    assert isinstance(client, GuardedFeeds)
    assert client.summary is summary


def test_web_observer_feeds_frames_and_ticks_to_the_summary(
    tmp_path: Path,
) -> None:
    """The web app's observers include the run summary and the timings."""
    app = sydney_app()
    timings = app.timings()
    observer = web_observer(
        timings=timings, extra=app.observers(), led_root=tmp_path,
    )
    observer.frame_drawn(render_ms=4.0)
    assert app.summary.report()['frames'] == 1
    assert timings.frames == 1


def test_replayed_feeds_are_read_as_they_are(tmp_path: Path) -> None:
    """Replayed feeds need no guard."""
    client = feeds_client(choice=str(tmp_path), live=tfnsw_feeds)
    assert isinstance(client, FileFeedsClient)


def test_parse_args_selftest_needs_nothing() -> None:
    """The selftest command parses with no other arguments."""
    assert parsed(argv=['selftest']).command == 'selftest'


def test_zone_prints_city_zone(capsys: pytest.CaptureFixture[str]) -> None:
    """The zone command prints the city's IANA zone and a newline."""
    board.main(city=SYDNEY, parts=SYDNEY_PARTS, argv=['zone'])
    assert capsys.readouterr() == ('Australia/Sydney\n', '')


def test_feeds_client_calls_live_only_when_live(tmp_path: Path) -> None:
    """Replayed feeds never make the live client; live feeds guard it."""
    made: list[FeedsClient] = []

    def live() -> FeedsClient:
        """
        Make a stand-in live client and note it.

        Returns
        -------
        FeedsClient
            A client reading the real recordings.
        """
        made.append(FileFeedsClient(directory=REAL))
        return made[-1]

    replayed = feeds_client(choice=str(tmp_path), live=live)
    assert isinstance(replayed, FileFeedsClient)
    assert not made
    guarded = feeds_client(choice='live', live=live)
    assert isinstance(guarded, GuardedFeeds)
    assert made == [guarded.inner]


async def test_sydney_parts_wire_sydney(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sydney's parts carry its nav, hint, line, assets, key and plug."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(tmp_path / 'key'))
    assert SYDNEY_PARTS.nav == SYDNEY_NAV
    assert SYDNEY_PARTS.hint == SYDNEY_HINT
    assert SYDNEY_PARTS.status_line is sydney_status_line
    assert SYDNEY_PARTS.assets == APP_ASSETS
    assert SYDNEY_PARTS.sheets == ('palette.css',)
    assert [key.name for key in SYDNEY_PARTS.keys()] == ['tfnsw']
    plug = SYDNEY_PARTS.suggest(live=False)
    assert isinstance(plug, SuggestPlug)
    await plug.close()


def parts_with(*, keys: tuple[DeviceKey, ...]) -> CityParts:
    """
    Give Sydney's parts with other device keys.

    Parameters
    ----------
    keys : tuple[DeviceKey, ...]
        The keys.

    Returns
    -------
    CityParts
        The parts.
    """
    return replace(SYDNEY_PARTS, keys=lambda: keys)


async def test_device_for_probes_and_resets_every_key(
    tmp_path: Path,
) -> None:
    """Live feeds probe and reset each of the city's keys; none is fine."""
    first, second = FakeKeys(key='k' * 32), FakeKeys()
    keys = (
        DeviceKey(
            name='a', credential=TFNSW_KEY, store=first, display_name='A',
        ),
        DeviceKey(
            name='b', credential=TFNSW_KEY, store=second, display_name='B',
        ),
    )
    args = argparse.Namespace(
        feeds='live',
        wifi=False,
        power=True,
        state=tmp_path / 'state.json',
        timetable=tmp_path / 'timetable.db',
        version_file=None,
    )
    last_error = LastError(zone=SYDNEY_ZONE)
    device = cli.device_for(
        args=args,
        app=TransitApp(city=SYDNEY, parts=parts_with(keys=keys)),
        last_error=last_error,
    )
    assert device.keys == keys
    assert device.probe is not None
    assert (await device.probe.probe()).missing_keys == ('B',)
    assert isinstance(device.resets, FileResets)
    assert device.resets.keys == (first, second)
    assert device.resets.erased == (args.timetable,)
    keyless = cli.device_for(
        args=args,
        app=TransitApp(city=SYDNEY, parts=parts_with(keys=())),
        last_error=last_error,
    )
    assert keyless.keys == ()
