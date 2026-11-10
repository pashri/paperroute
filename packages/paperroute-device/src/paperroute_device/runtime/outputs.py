"""Where the kit's effects draw and write, and carrying them out."""

import asyncio
import functools
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from paperroute_device.core.app import App
from paperroute_device.core.device_model import PowerAction
from paperroute_device.core.kit_effects import (
    ControlPower,
    JoinWifi,
    ProbeDevice,
    Render,
    ResetDevice,
    SaveState,
    StartWifiSetup,
    Stop,
    StopWifiSetup,
)
from paperroute_device.core.kit_events import PowerFailed
from paperroute_device.core.panel_views import (
    NoticeView,
    PanelView,
    PlainSleepView,
)
from paperroute_device.ports import Frame
from paperroute_device.render.notice import draw_notice_view
from paperroute_device.render.panel import draw_panel_view
from paperroute_device.render.sleep import draw_plain_sleep
from paperroute_device.runtime.device_work import Reported, device_work
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.saver import StateSaver
from paperroute_hardware.ports import CLEAN_AFTER

logger = logging.getLogger(__name__)

type Job = tuple[Coroutine[Any, Any, None], object | None]

PANEL_SETTLE: Final[float] = 15  # seconds
MS_PER_SECOND: Final[float] = 1000


@dataclass
# pylint: disable-next=too-many-instance-attributes
class PendingView:
    """
    The last view asked for and not drawn yet.

    Attributes
    ----------
    view : object | None
        The view, or None when the screen is up to date.
    full : bool
        Whether any render since the last frame asked for a full refresh.
    turned : bool
        Whether frames are shown upside down.
    quick : bool
        Whether a knob was turned just now.
    asleep : bool
        Whether the device is in Sleep mode.
    clear : bool
        Whether any render since the last frame asked for a clear
        refresh.
    clean_after : int
        Quick patches before the panel cleans the screen.
    """

    view: object | None = None
    full: bool = False
    turned: bool = False
    quick: bool = False
    asleep: bool = False
    clear: bool = False
    clean_after: int = CLEAN_AFTER


@dataclass(frozen=True)
class Outputs:
    """
    What the kit's effects draw and write through.

    Attributes
    ----------
    saver : StateSaver
        Writes the state file.
    pending : PendingView
        The view waiting to be drawn.
    reported : Reported
        The statuses reported last.
    """

    saver: StateSaver
    pending: PendingView = field(default_factory=PendingView)
    reported: Reported = field(default_factory=Reported)


def drawing[S](*, app: App[S], view: object) -> Callable[[], Frame]:
    """
    Choose how a view is drawn.

    Parameters
    ----------
    app : App[S]
        The app, which draws every view that is not the kit's.
    view : object
        The view.

    Returns
    -------
    Callable[[], Frame]
        Draws the frame: the kit's own drawing for its panel screens,
        notices and plain sleep screen, else the app's.
    """
    if isinstance(view, PanelView):
        return functools.partial(draw_panel_view, view=view)
    if isinstance(view, NoticeView):
        return functools.partial(draw_notice_view, view=view)
    if isinstance(view, PlainSleepView):
        return functools.partial(draw_plain_sleep, view=view)
    return functools.partial(app.draw, view=view)


async def draw_pending[S](
    *, ports: KitPorts, app: App[S], outputs: Outputs,
) -> None:
    """
    Draw the waiting view off the event loop, if there is one.

    Parameters
    ----------
    ports : KitPorts
        Adapters.
    app : App[S]
        The app.
    outputs : Outputs
        What effects draw and write through.
    """
    pending = outputs.pending
    view, full, clear = pending.view, pending.full, pending.clear
    if view is None:
        return
    pending.view, pending.full, pending.clear = None, False, False
    started = time.perf_counter()
    frame = await asyncio.to_thread(drawing(app=app, view=view))
    render_ms = (time.perf_counter() - started) * MS_PER_SECOND
    ports.display.show(
        frame=frame,
        full=full,
        clear=clear,
        turned=pending.turned,
        quick=pending.quick,
        asleep=pending.asleep,
        clean_after=pending.clean_after,
    )
    logger.debug('frame drawn', extra={'render_ms': render_ms, 'full': full})
    ports.observer.frame_drawn(render_ms=render_ms)


async def settle_panel(*, ports: KitPorts) -> None:
    """
    Wait for the panel to finish drawing, warning if it does not.

    Parameters
    ----------
    ports : KitPorts
        Adapters.
    """
    settled = await asyncio.to_thread(
        ports.display.settle, timeout=PANEL_SETTLE,
    )
    if not settled:
        logger.warning('panel still busy, carrying on')


def pend(*, pending: PendingView, effect: Render) -> None:
    """
    Keep a render's view to draw, keeping any full or clear asked for.

    Parameters
    ----------
    pending : PendingView
        The view waiting to be drawn.
    effect : Render
        The render.
    """
    pending.view = effect.view
    pending.full = pending.full or effect.full
    pending.clear = pending.clear or effect.clear


# pylint: disable-next=too-many-arguments
async def carry_out[S](
    *,
    effects: Sequence[object],
    ports: KitPorts,
    app: App[S],
    queue: asyncio.Queue[object],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
) -> None:
    """
    Perform an event's effects in order, keeping its view to draw later.

    A full or clear Render stays so when a later one in the burst
    replaces its view. A ControlPower or ResetDevice draws the waiting
    view first, so the notice is on screen before the device goes down
    or is reset. Saving finishes before this returns, so a quit's save
    is written before the loop ends; other work runs in the background.

    Parameters
    ----------
    effects : Sequence[object]
        The effects: the kit's own, and the app's for `App.perform`.
    ports : KitPorts
        Adapters.
    app : App[S]
        The app.
    queue : asyncio.Queue[object]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.
    """
    for effect in effects:
        match effect:
            case Render():
                pend(pending=outputs.pending, effect=effect)
            case SaveState():
                await outputs.saver.save(effect=effect)
            case Stop():
                pass
            case _:
                if isinstance(effect, ControlPower | ResetDevice):
                    await draw_pending(ports=ports, app=app, outputs=outputs)
                    await settle_panel(ports=ports)
                launched(
                    work=background(
                        ports=ports, app=app, effect=effect, queue=queue,
                    ),
                    tasks=tasks,
                    queue=queue,
                )


def background[S](
    *,
    ports: KitPorts,
    app: App[S],
    effect: object,
    queue: asyncio.Queue[object],
) -> Job:
    """
    Make the work for an effect done in the background.

    Parameters
    ----------
    ports : KitPorts
        Adapters.
    app : App[S]
        The app, which performs every effect that is not the kit's.
    effect : object
        Effect.
    queue : asyncio.Queue[object]
        Event queue.

    Returns
    -------
    Job
        The work, and the event that lets the kit recover if it crashes;
        None for the app's work, whose crash is only logged.
    """
    if isinstance(effect, ControlPower):
        work = power_control(ports=ports, action=effect.action, queue=queue)
        return work, PowerFailed(action=effect.action)
    device = (
        ProbeDevice | ResetDevice | StartWifiSetup | JoinWifi | StopWifiSetup
    )
    if isinstance(effect, device):
        return device_work(
            ports=ports.device, app=app, effect=effect, queue=queue,
        )
    return app.perform(effect=effect, post=queue.put_nowait), None


async def power_control(
    *, ports: KitPorts, action: PowerAction, queue: asyncio.Queue[object],
) -> None:
    """
    Shut down or restart, posting PowerFailed when it is refused.

    Parameters
    ----------
    ports : KitPorts
        Adapters.
    action : PowerAction
        Shut down or restart.
    queue : asyncio.Queue[object]
        Event queue.
    """
    if not await ports.power.control(action=action):
        await queue.put(PowerFailed(action=action))


def launched(
    *,
    work: Job,
    tasks: set[asyncio.Task[None]],
    queue: asyncio.Queue[object],
) -> None:
    """
    Start background work, kept alive until it is done.

    Parameters
    ----------
    work : Job
        The coroutine, and the event posted if it crashes, or None.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    queue : asyncio.Queue[object]
        Event queue.
    """
    coroutine, failed = work
    task = asyncio.create_task(coroutine)
    tasks.add(task)
    task.add_done_callback(
        functools.partial(finished, tasks=tasks, queue=queue, failed=failed),
    )


def finished(
    task: asyncio.Task[None],
    *,
    tasks: set[asyncio.Task[None]],
    queue: asyncio.Queue[object],
    failed: object | None,
) -> None:
    """
    Forget a finished task; if it crashed, log it and post its failure.

    Parameters
    ----------
    task : asyncio.Task[None]
        The finished task.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    queue : asyncio.Queue[object]
        Event queue.
    failed : object | None
        Event that lets the kit recover from this task crashing; None
        when there is none to post.
    """
    tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is None:
        return
    logger.error('background task crashed', exc_info=error)
    if failed is not None:
        queue.put_nowait(failed)
