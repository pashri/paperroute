"""Shared helpers for the app tests."""

import asyncio
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute_device.core.device_model import DeviceStatus
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.core.kit_state import KitState
from paperroute_device.runtime.loop import handle, run, starting
from paperroute_device.runtime.outputs import Outputs, draw_pending
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.saver import StateSaver
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.core.knob_events import Knob, KnobPressed
from paperroute_hardware.ports import CLEAN_AFTER, PanelRefresh
from paperroute_sydney.city import SYDNEY, SYDNEY_REF
from paperroute_testkit.core.kit_steps import QUIET_PARTS, app_of
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_ports import Ports
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.map_style import LIGHT
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.messages import Event
from paperroute_transit.core.model import Pin
from paperroute_transit.core.saved import Settings
from paperroute_transit.core.state_model import State
from paperroute_transit.core.tiles import (
    TILE,
    LabelKind,
    StreetLabel,
    TileVariant,
)
from paperroute_transit.observers import ObserverGroup
from paperroute_transit.ports import Observer, TimetableRepository
from paperroute_transit.transit_app import TransitApp
from tests.app_effects.helpers import kit_ports, sydney_app
from tests.app_factories import PIN, REAL, ports_of

type PostHook = Callable[[Callable[[object], None]], None]


async def run_shown(
    *,
    ports: Ports,
    display: FrameRecorder,
    observer: Observer | None = None,
) -> KitState[State]:
    """
    Run Sydney's board seeded at the shared pin for sixty events.

    Parameters
    ----------
    ports : Ports
        Transit's adapters.
    display : FrameRecorder
        The display the frames are drawn on.
    observer : Observer | None
        Hears the run; None keeps each part's default.

    Returns
    -------
    KitState[State]
        The final kit state.
    """
    return await run_sydney(
        ports=ports,
        kit=kit_ports(display=display),
        seed=PIN,
        observer=observer,
        max_events=60,
    )


async def run_built(
    *,
    tmp_path: Path,
    display: FrameRecorder,
    observer: Observer | None = None,
) -> KitState[State]:
    """
    Run Sydney's board over a freshly built timetable.

    Parameters
    ----------
    tmp_path : Path
        Folder to build the timetable in.
    display : FrameRecorder
        The display the frames are drawn on.
    observer : Observer | None
        Hears the run; None keeps each part's default.

    Returns
    -------
    KitState[State]
        The final kit state.
    """
    with built_timetable(tmp_path=tmp_path) as timetable:
        return await run_shown(
            ports=ports_of(timetable=timetable),
            display=display,
            observer=observer,
        )


# pylint: disable-next=too-many-arguments
async def run_sydney(
    *,
    ports: Ports,
    kit: KitPorts | None = None,
    seed: Pin | None = None,
    observer: Observer | None = None,
    variant: TileVariant = TileVariant.GREY,
    tick_every: float = 0.01,
    max_events: int | None = None,
    post_hook: PostHook | None = None,
) -> KitState[State]:
    """
    Run Sydney's board on the kit's loop.

    Parameters
    ----------
    ports : Ports
        Transit's adapters.
    kit : KitPorts | None
        The kit's adapters; fakes at the shared test time by default.
    seed : Pin | None
        Pin to start with when nothing is saved.
    observer : Observer | None
        Hears the kit, the feeds, the settings and the pins, through a
        group that swallows its errors; None keeps each part's default.
    variant : TileVariant
        Which tile rendering the Map draws.
    tick_every : float
        Seconds between ticks.
    max_events : int | None
        Stop after this many events; None runs until quit.
    post_hook : PostHook | None
        Receives the function that posts events.

    Returns
    -------
    KitState[State]
        The final kit state.
    """
    kit = kit or kit_ports()
    group = None if observer is None else ObserverGroup(observers=[observer])
    if group is not None:
        ports = replace(ports, observer=group)
        kit = replace(kit, observer=group)
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS, observer=group)
    app.attach(ports=ports, seed=seed, variant=variant)
    return await run(
        app=app,
        ports=kit,
        zone=SYDNEY.zone,
        tick_every=tick_every,
        post_hook=post_hook,
        max_events=max_events,
    )


@dataclass
class Handler:
    """
    Hands events one at a time to the kit with Sydney's board on it.

    Attributes
    ----------
    ports : KitPorts
        The kit's adapters.
    app : TransitApp
        The attached board.
    kit : KitState[State]
        The kit state so far.
    outputs : Outputs
        What the kit's effects draw and write through.
    queue : asyncio.Queue[object]
        Events posted by the work started.
    tasks : set[asyncio.Task[None]]
        The work started.
    """

    ports: KitPorts
    app: TransitApp
    kit: KitState[State]
    outputs: Outputs
    queue: asyncio.Queue[object] = field(default_factory=asyncio.Queue)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def handle(self, *, event: object) -> State:
        """
        Hand one event to the kit and carry out its effects.

        Parameters
        ----------
        event : object
            What happened.

        Returns
        -------
        State
            The board's state after the event.
        """
        self.kit = await handle(
            kit=self.kit,
            app=self.app,
            zone=SYDNEY.zone,
            event=event,
            ports=self.ports,
            queue=self.queue,
            tasks=self.tasks,
            outputs=self.outputs,
        )
        return app_of(kit=self.kit)

    async def draw(self, *, view: object) -> None:
        """
        Draw a view as the waiting one, as the kit last set it up.

        Parameters
        ----------
        view : object
            The view.
        """
        self.outputs.pending.view = view
        await draw_pending(
            ports=self.ports, app=self.app, outputs=self.outputs,
        )


def handler_of(
    *, ports: KitPorts, app: TransitApp | None = None,
) -> Handler:
    """
    Start Sydney's board on the kit as its loop does, one event at a time.

    Parameters
    ----------
    ports : KitPorts
        The kit's adapters.
    app : TransitApp | None
        The attached board; one on fakes seeded with `PIN` by default.

    Returns
    -------
    Handler
        The handler, at the starting kit state.
    """
    app = app or sydney_app(ports=ports_of(), seed=PIN)
    return Handler(
        ports=ports,
        app=app,
        kit=starting(app=app, ports=ports),
        outputs=Outputs(saver=StateSaver(states=ports.states)),
    )


@dataclass
class CrashingFeeds:
    """A feeds client that fails with an unexpected error."""

    async def fetch(self, *, feed: str) -> bytes:
        """
        Fail.

        Parameters
        ----------
        feed : str
            Ignored.

        Returns
        -------
        bytes
            Never returns.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError(f'boom {feed}')

    async def close(self) -> None:
        """Do nothing."""


@dataclass
class CallRecorder:
    """An observer that counts feeds and ticks, keeping settings and status."""

    feeds: int = 0
    ticks: int = 0
    work: list[tuple[str, float]] = field(default_factory=list)
    apps: list[Mapping[str, object]] = field(default_factory=list)
    settings: list[Settings] = field(default_factory=list)
    statuses: list[DeviceStatus] = field(default_factory=list)
    pins: list[tuple[tuple[Pin, ...], int | None]] = field(
        default_factory=list
    )

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Ignore a frame.

        Parameters
        ----------
        render_ms : float
            Ignored.
        """

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Count a parsed feed.

        Parameters
        ----------
        feed : str
            Ignored.
        fetch_ms : float
            Ignored.
        parse_ms : float
            Ignored.
        trips : int
            Ignored.
        """
        del feed, fetch_ms, parse_ms, trips
        self.feeds += 1

    def work_done(self, *, name: str, ms: float) -> None:
        """
        Keep a piece of work.

        Parameters
        ----------
        name : str
            What was done.
        ms : float
            How long it took in milliseconds.
        """
        self.work.append((name, ms))

    def app_seen(self, *, status: Mapping[str, object]) -> None:
        """
        Keep the app status.

        Parameters
        ----------
        status : Mapping[str, object]
            The status.
        """
        self.apps.append(status)

    def ticked(self) -> None:
        """Count a tick."""
        self.ticks += 1

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Keep the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self.settings.append(settings)

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Keep the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self.statuses.append(status)

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Keep the pins and which one is shown.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            Index of the pin shown, or None when the unpinned place is.
        """
        self.pins.append((pins, shown))

    def device_settings_seen(self, *, settings: DeviceSettings) -> None:
        """
        Ignore the device settings.

        Parameters
        ----------
        settings : DeviceSettings
            Ignored.
        """

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Ignore a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            Ignored.
        """


@contextmanager
def built_timetable(*, tmp_path: Path) -> Iterator[SqliteTimetableRepository]:
    """
    Open a timetable repository over a freshly built database.

    Parameters
    ----------
    tmp_path : Path
        Folder to build the database in.

    Yields
    ------
    SqliteTimetableRepository
        The repository; it is closed on leaving.
    """
    timetable = SqliteTimetableRepository(
        path=_build_db(tmp_path),
        city=SYDNEY,
    )
    try:
        yield timetable
    finally:
        timetable.close()


def _build_db(tmp_path: Path) -> Path:
    """
    Build the real-slice timetable database.

    Parameters
    ----------
    tmp_path : Path
        Directory to build in.

    Returns
    -------
    Path
        Database path.
    """
    db = tmp_path / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 16), days=1),
        out=db,
        city_ref=SYDNEY_REF,
    )
    return db


BUS_POSITIONS = 'v1/gtfs/vehiclepos/buses'


@dataclass
class RecordingFeeds:
    """Replayed feeds that remember which feeds were fetched."""

    directory: Path
    fetched: list[str] = field(default_factory=list)

    async def fetch(self, *, feed: str) -> bytes:
        """
        Note the feed and read its recording.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            Recorded body.
        """
        self.fetched.append(feed)
        return await FileFeedsClient(directory=self.directory).fetch(
            feed=feed
        )

    async def close(self) -> None:
        """Release nothing."""


@dataclass
class ActingDisplay(FrameRecorder):
    """A frame recorder that acts before and after it keeps each frame."""

    post: Callable[[Event], None] | None = None

    def before_show(self) -> None:
        """Act before a frame is kept; does nothing."""

    def after_show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Act after a frame is kept; does nothing.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame just kept.
        """

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
    ) -> None:
        """
        Keep a frame, acting before and after.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            Frame.
        full : bool
            Whether a full refresh was asked for.
        turned : bool
            Whether the frame is turned upside down.
        quick : bool
            Whether a knob was turned just now.
        asleep : bool
            Whether the device is in Sleep mode.
        clear : bool
            Whether a clear refresh was asked for.
        clean_after : int
            Quick patches before a cleaning full refresh.
        """
        self.before_show()
        super().show(
            frame=frame,
            full=full,
            turned=turned,
            quick=quick,
            asleep=asleep,
            clear=clear,
            clean_after=clean_after,
        )
        self.after_show(frame=frame)

    def hook(self, post: Callable[[Event], None]) -> None:
        """
        Keep the function that posts events.

        Parameters
        ----------
        post : Callable[[Event], None]
            Posts an event to the app.
        """
        self.post = post


@dataclass
class PressingDisplay(ActingDisplay):
    """A display that presses the bottom knob when it first shows."""

    def before_show(self) -> None:
        """Press the bottom knob before the first frame is kept."""
        if not self.frames and self.post is not None:
            self.post(KnobPressed(knob=Knob.BOTTOM))


@dataclass
class CountingTiles:
    """A tile repository that serves paper tiles and counts reads."""

    built: str = 'v1'
    reads: int = 0
    variants: set[TileVariant] = field(default_factory=set)

    def tile(
        self, *, zoom: int, col: int, row: int, variant: TileVariant
    ) -> NDArray[np.uint8] | None:
        """
        Count a read and give a LIGHT tile.

        Parameters
        ----------
        zoom : int
            Ignored.
        col : int
            Ignored.
        row : int
            Ignored.
        variant : TileVariant
            Recorded.

        Returns
        -------
        NDArray[np.uint8] | None
            A LIGHT tile.
        """
        del zoom, col, row
        self.reads += 1
        self.variants.add(variant)
        return np.full((TILE, TILE), LIGHT, dtype=np.uint8)

    def street_labels(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
        kind: LabelKind = LabelKind.STREET,
    ) -> list[StreetLabel]:
        """
        Know no labels.

        Parameters
        ----------
        zoom : int
            Ignored.
        box : tuple[float, float, float, float]
            Ignored.
        kind : LabelKind
            Ignored.

        Returns
        -------
        list[StreetLabel]
            Nothing.
        """
        del zoom, box, kind
        return []

    def version(self) -> str | None:
        """
        Say which build this is.

        Returns
        -------
        str | None
            The build name.
        """
        return self.built


def started(*, timetable: TimetableRepository, seed: Pin) -> State:
    """
    Start Sydney's board as the kit's loop does, with nothing saved.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable the seed's places are filled from.
    seed : Pin
        Pin to start with.

    Returns
    -------
    State
        The board's starting state.
    """
    app = sydney_app(ports=ports_of(timetable=timetable), seed=seed)
    return app_of(kit=starting(app=app, ports=kit_ports()))


def tableless_tiles(*, out: Path) -> SqliteTileRepository:
    """
    Write a tiles file that has a meta table but no tiles, and open it.

    Parameters
    ----------
    out : Path
        Destination file.

    Returns
    -------
    SqliteTileRepository
        The opened file; it passes the open check.
    """
    with closing(sqlite3.connect(out)) as conn:
        with conn:
            conn.execute('CREATE TABLE meta (key TEXT, value TEXT)')
            conn.execute("INSERT INTO meta VALUES ('built_at', 'broken')")
    return SqliteTileRepository(path=out)


@dataclass
class VersionlessTiles(CountingTiles):
    """Tiles whose build can't be read."""

    def version(self) -> str | None:
        """
        Fail like a damaged database file.

        Returns
        -------
        str | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.Error('file is not a database')
