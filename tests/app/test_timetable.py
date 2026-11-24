# pylint: disable=unused-argument
"""Tests for app: timetable."""

import asyncio
import logging
import shutil
import sqlite3
from collections.abc import Collection
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.adapters.resets import FileResets
from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.device import NOTICE, on_reset
from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import PowerAction, ResetKind
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import Render, SaveState
from paperroute_device.core.kit_events import PowerFailed, ResetDone, Tick
from paperroute_device.core.kit_state import SAVE_WAIT
from paperroute_device.core.panel_screens import menu_view
from paperroute_device.core.panel_views import NoticeView
from paperroute_device.render.notice import draw_notice_view
from paperroute_device.render.panel import draw_panel_view
from paperroute_device.runtime.device_work import DevicePorts
from paperroute_device.runtime.loop import stop
from paperroute_device.runtime.outputs import Outputs, carry_out
from paperroute_device.runtime.saver import StateSaver
from paperroute_device_testkit.device_fakes import (
    FakeKeys,
    FakePower,
    FakeWifi,
)
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.kit_steps import (
    QUIET_PARTS,
    app_of,
    settings_posted,
)
from paperroute_testkit.fakes import (
    BrokenTimetable,
    BuiltTimetable,
    FakeBuilder,
)
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_work import fetch
from paperroute_transit.core.alert_model import AlertsFetched
from paperroute_transit.core.alerts_view import AlertRow, AlertsView, AlertText
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.messages import (
    Estimating,
    Event,
    FeedFetched,
    FetchFeed,
    PositionsFetched,
    RebuildTimetable,
    TimetableRebuilt,
)
from paperroute_transit.core.rebuild import TRANSIT_ROWS
from paperroute_transit.core.stop_view import NOT_PINNED
from paperroute_transit.render.alerts_view import draw_alerts_view
from paperroute_transit.transit_app import TransitApp
from tests.app.helpers import (
    ActingDisplay,
    RecordingFeeds,
    _build_db,
    handler_of,
    run_shown,
    run_sydney,
)
from tests.app_effects.helpers import kit_ports, rendered, sydney_app
from tests.app_factories import AT, PIN, REAL, ports_of, posted_by
from tests.golden.scenes import GOLDEN


async def test_app_performs_rebuild() -> None:
    """A RebuildTimetable posts how it went; stopping the app stops it."""
    builder = FakeBuilder(result=True)
    ports = replace(
        ports_of(timetable=BuiltTimetable()), builder=builder,
    )
    posted = await posted_by(ports=ports, effects=[RebuildTimetable()])
    assert posted[0] == TimetableRebuilt(ok=True)
    await run_sydney(ports=ports, seed=PIN, max_events=1)
    assert (builder.rebuilds, builder.stops) == (1, 1)


async def test_app_draws_notice_before_power() -> None:
    """The notice is on screen before the power action; a failure redraws."""
    display = FrameRecorder()
    power = FakePower(result=False, display=display)
    ports = replace(kit_ports(display=display), power=power)
    handler = handler_of(ports=ports)
    for event in (Tick(now=AT), PowerRequested(action=PowerAction.OFF)):
        await handler.handle(event=event)
    await asyncio.gather(*handler.tasks)
    title = NOTICE[PowerAction.OFF]
    notice = draw_notice_view(view=NoticeView(title=title))
    [(action, shown_at)] = power.calls
    assert action is PowerAction.OFF
    assert np.array_equal(display.frames[shown_at - 1], notice)
    failed = next_of(queue=handler.queue, kind=PowerFailed)
    state = await handler.handle(event=failed)
    assert state.power is None
    assert not np.array_equal(display.frames[-1], notice)
    await stop(tasks=handler.tasks)


async def test_app_reset_leaves_no_state_file(tmp_path: Path) -> None:
    """A reset deletes state.json, nothing writes it again, and it stops."""
    path = tmp_path / 'state.json'
    resets = FileResets(
        data_dir=tmp_path,
        state=path,
        keys=(FakeKeys(key='k' * 32),),
        wifi=FakeWifi(),
        erased=(tmp_path / 'timetable.db',),
    )
    app = sydney_app(ports=ports_of(), seed=PIN)
    ports = replace(
        kit_ports(),
        states=StateFile(path=path, app=app),
        device=DevicePorts(resets=resets),
    )
    handler = handler_of(ports=ports, app=app)
    for event in (Tick(now=AT), settings_posted(auto_rebuild=False)):
        await handler.handle(event=event)
    assert path.exists()
    device, effects = on_reset(
        device=handler.kit.device, kind=ResetKind.SETTINGS,
    )
    handler.kit = replace(handler.kit, device=device)
    await carry_out(
        effects=effects,
        ports=ports,
        app=app,
        queue=handler.queue,
        tasks=handler.tasks,
        outputs=handler.outputs,
    )
    await asyncio.gather(*handler.tasks)
    after: tuple[object, ...] = (
        Tick(now=AT + SAVE_WAIT * 2),
        next_of(queue=handler.queue, kind=ResetDone),
    )
    for later in after:
        state = await handler.handle(event=later)
    assert not state.running
    assert not path.exists()
    await stop(tasks=handler.tasks)


def next_of[T](*, queue: asyncio.Queue[object], kind: type[T]) -> T:
    """
    Take events off a queue until one of a kind turns up.

    Parameters
    ----------
    queue : asyncio.Queue[object]
        Event queue.
    kind : type[T]
        The kind wanted.

    Returns
    -------
    T
        The first event of that kind.
    """
    while True:
        event = queue.get_nowait()
        if isinstance(event, kind):
            return event


async def test_app_starts_without_a_timetable(tmp_path: Path) -> None:
    """With no timetable file the app runs and ticks without crashing."""
    missing = tmp_path / 'timetable.db'
    timetable = SqliteTimetableRepository(
        path=missing,
        city=SYDNEY,
    )
    network = SqliteNetworkRepository(
        path=missing,
        city=SYDNEY,
    )
    ports = replace(ports_of(), timetable=timetable, network=network)
    try:
        kit = await run_sydney(ports=ports, seed=PIN, max_events=5)
    finally:
        timetable.close()
        network.close()
    assert kit.running
    assert not missing.exists()


@dataclass
class ScriptedDisplay(ActingDisplay):
    """
    A display that posts a script of events once a frame is shown.

    Attributes
    ----------
    trigger : NDArray[np.uint8] | None
        The frame that starts the script.
    script : tuple[Event, ...]
        The events posted.
    """

    trigger: NDArray[np.uint8] | None = None
    script: tuple[Event, ...] = ()

    def after_show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        When the frame is the trigger, post the script once.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame just kept.
        """
        trigger = self.trigger
        if trigger is None or self.post is None:
            return
        if (frame == trigger).all():
            for event in self.script:
                self.post(event)
            self.trigger = None


def frame_index(
    *, frames: list[NDArray[np.uint8]], frame: NDArray[np.uint8]
) -> int:
    """
    Find the first frame equal to one given.

    Parameters
    ----------
    frames : list[NDArray[np.uint8]]
        Frames shown.
    frame : NDArray[np.uint8]
        The frame looked for.

    Returns
    -------
    int
        Its index, or -1 when it was never shown.
    """
    return next(
        (
            index
            for index, shown_frame in enumerate(frames)
            if (shown_frame == frame).all()
        ),
        -1,
    )


async def test_app_runs_with_no_timetable(tmp_path: Path) -> None:
    """With no timetable the Building screen shows, then the menu opens."""
    missing = tmp_path / 'timetable.db'
    timetable = SqliteTimetableRepository(
        path=missing,
        city=SYDNEY,
    )
    network = SqliteNetworkRepository(
        path=missing,
        city=SYDNEY,
    )
    building = draw_notice_view(
        view=NoticeView(
            title='Building the timetable',
            detail='Both knobs: hold to open Settings',
        ),
    )
    display = ScriptedDisplay(
        trigger=building,
        script=(
            Tick(now=AT),
            KnobTurned(knob=Knob.BOTTOM, steps=2),
            KnobTurned(knob=Knob.TOP, steps=-1),
            KnobPressed(knob=Knob.TOP),
            Tick(now=AT),
            KnobsHeld(),
            Tick(now=AT),
        ),
    )
    ports = replace(
        ports_of(),
        timetable=timetable,
        network=network,
        builder=FakeBuilder(result=True),
    )
    try:
        kit = await run_sydney(
            ports=ports,
            kit=kit_ports(display=display),
            seed=PIN,
            post_hook=display.hook,
            max_events=20,
        )
    finally:
        timetable.close()
        network.close()
    rows = TRANSIT_ROWS
    view = menu_view(device=kit.device, rows=rows, zone=SYDNEY_ZONE)
    menu = draw_panel_view(view=view)
    first = frame_index(frames=display.frames, frame=building)
    assert 0 <= first < frame_index(frames=display.frames, frame=menu)
    assert app_of(kit=kit).rebuild.missing


@dataclass
class ArrivingDisplay(ActingDisplay):
    """
    A display that puts a timetable in place once a frame is shown.

    Attributes
    ----------
    source : Path | None
        The built timetable copied in, once.
    target : Path | None
        Where it goes.
    """

    source: Path | None = None
    target: Path | None = None

    def after_show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Copy the timetable in once.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame just kept.
        """
        if self.source is not None and self.target is not None:
            shutil.copyfile(self.source, self.target)
            self.source = None


async def test_app_goes_live_when_the_timetable_arrives(
    tmp_path: Path,
) -> None:
    """A timetable built while running ends the Building screen."""
    (tmp_path / 'built').mkdir()
    built = _build_db(tmp_path / 'built')
    target = tmp_path / 'timetable.db'
    timetable = SqliteTimetableRepository(
        path=target,
        city=SYDNEY,
    )
    network = SqliteNetworkRepository(
        path=target,
        city=SYDNEY,
    )
    display = ArrivingDisplay(source=built, target=target)
    feeds = RecordingFeeds(directory=REAL)
    ports = replace(
        ports_of(),
        timetable=timetable,
        network=network,
        feeds=feeds,
        builder=FakeBuilder(result=True),
    )
    with closing(timetable), closing(network):
        kit = await run_shown(ports=ports, display=display)
    state = app_of(kit=kit)
    assert not state.rebuild.missing
    assert state.board.departures
    assert feeds.fetched
    assert not isinstance(state.last_view, NoticeView)


async def test_alert_fetch_uses_feeds_port() -> None:
    """An alerts fetch goes through the feeds port once and posts alerts."""
    feeds = RecordingFeeds(directory=REAL)
    posted = await posted_by(
        ports=replace(ports_of(), feeds=feeds),
        effects=[
            FetchFeed(
                feed='v2/gtfs/alerts/sydneytrains', kind=FeedKind.ALERTS,
            ),
        ],
    )
    assert feeds.fetched == ['v2/gtfs/alerts/sydneytrains']
    first = posted[0]
    assert isinstance(first, AlertsFetched)
    assert len(first.snapshot.alerts) == 5


async def test_app_draws_alerts_view() -> None:
    """A Render of an AlertsView reaches the display as a full frame."""
    display = FrameRecorder()
    ports = kit_ports(display=display)
    app = sydney_app(ports=ports_of(timetable=BuiltTimetable()))
    view = AlertsView(
        origin='Strathfield',
        destination=None,
        clock='09:39',
        tag=NOT_PINNED,
        rows=(AlertRow(headline='One', when='Ongoing', active=True),),
        selected=0,
        text=AlertText(title='One', when='Ongoing', body='Text', also=()),
        page=0,
    )
    await rendered(
        app=app,
        ports=ports,
        outputs=Outputs(saver=StateSaver(states=ports.states)),
        render=Render(view=view, full=True),
    )
    [frame] = display.frames
    assert frame.shape == (480, 800)
    assert np.array_equal(frame, draw_alerts_view(view=view))


async def test_saver_writes_the_board_section(tmp_path: Path) -> None:
    """The kit's saver writes Sydney's state file as it was read."""
    path = tmp_path / 'state.json'
    golden = (GOLDEN / 'state.json').read_bytes()
    path.write_bytes(golden)
    states = StateFile(
        path=path, app=TransitApp(city=SYDNEY, parts=QUIET_PARTS),
    )
    loaded = states.load()
    path.unlink()
    saver = StateSaver(states=states)
    kept = KitSaved(device=loaded.device, app=loaded.app)
    await saver.save(effect=SaveState(saved=kept))
    assert path.read_bytes() == golden
    turned = replace(kept.device, rotate=not kept.device.rotate)
    await saver.save(effect=SaveState(saved=replace(kept, device=turned)))
    assert states.load().device == turned


ESTIMATING: Final[Estimating] = Estimating(
    feed='estimates/bus',
    trip_ids=frozenset({'none-of-these'}),
    stale_after=timedelta(minutes=3),
)


POSITIONS_FETCH: Final[FetchFeed] = FetchFeed(
    feed='v1/gtfs/vehiclepos/buses',
    kind=FeedKind.POSITIONS,
    estimating=ESTIMATING,
)


async def test_fetch_posts_estimates_before_positions() -> None:
    """The estimates land before the positions that release the frame."""
    ports = replace(
        ports_of(timetable=BrokenTimetable()),
        feeds=FileFeedsClient(directory=REAL),
    )
    posted: list[Event] = []
    await fetch(ports=ports, effect=POSITIONS_FETCH, post=posted.append)
    assert [type(event) for event in posted] == [FeedFetched, PositionsFetched]
    first = posted[0]
    assert isinstance(first, FeedFetched)
    assert first.feed == 'estimates/bus' and not first.snapshot.trips


class JoinFailingTimetable(BrokenTimetable):
    """A timetable whose joins read fails."""

    def joined_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, str]
            Never returns.

        Raises
        ------
        sqlite3.OperationalError
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_fetch_posts_positions_when_estimating_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed estimate is logged; the positions still arrive."""
    ports = replace(
        ports_of(timetable=JoinFailingTimetable()),
        feeds=FileFeedsClient(directory=REAL),
    )
    posted: list[Event] = []
    with caplog.at_level(logging.ERROR):
        await fetch(ports=ports, effect=POSITIONS_FETCH, post=posted.append)
    assert [type(event) for event in posted] == [PositionsFetched]
    assert 'estimating trip times failed' in caplog.text
