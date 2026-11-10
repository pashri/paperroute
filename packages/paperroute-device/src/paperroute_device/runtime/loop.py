"""Run an app on the kit: one event queue through the kit's reducer."""

# An app package keeps a twin of this module until it switches over.
# pylint: disable=duplicate-code

import asyncio
import functools
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta, tzinfo
from typing import Final

from paperroute_device.adapters.migrations import KIT_MIGRATIONS, chain
from paperroute_device.core.app import App
from paperroute_device.core.device import kit_saved
from paperroute_device.core.device_state import ClockWait, DeviceState, elapsed
from paperroute_device.core.kit_events import QuitRequested, Tick
from paperroute_device.core.kit_state import KitState
from paperroute_device.core.reducer import step
from paperroute_device.runtime.device_work import (
    key_known,
    leftover_stopped,
    report_app_status,
    report_status,
)
from paperroute_device.runtime.outputs import Outputs, carry_out, draw_pending
from paperroute_device.runtime.ports import KitPorts
from paperroute_device.runtime.saver import StateSaver
from paperroute_device.runtime.timings import ObserverGroup

__all__ = ['handle', 'opened', 'run', 'stop', 'ticker']

logger = logging.getLogger(__name__)

TICK_PAUSE: Final[float] = 0.03  # seconds between window pumps
QUICK_FOR: Final[timedelta] = timedelta(seconds=3)


def quick_now(*, device: DeviceState) -> bool:
    """
    Say whether a knob was touched within the last `QUICK_FOR`.

    Parameters
    ----------
    device : DeviceState
        Device state at the event's time.

    Returns
    -------
    bool
        True when the knobs were touched and `QUICK_FOR` has not passed.
    """
    touched_at = device.touched_at
    return touched_at is not None and not elapsed(
        since=touched_at, now=device.now, span=QUICK_FOR,
    )


def ticker_finished(
    task: asyncio.Task[None], *, queue: asyncio.Queue[object],
) -> None:
    """
    If the ticker crashed, log it and post QuitRequested.

    Parameters
    ----------
    task : asyncio.Task[None]
        The finished ticker task.
    queue : asyncio.Queue[object]
        Event queue.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error('ticker crashed', exc_info=error)
        queue.put_nowait(QuitRequested())


async def ticker[S](
    *,
    app: App[S],
    ports: KitPorts,
    queue: asyncio.Queue[object],
    every: float,
    pump: Callable[[], None] | None,
) -> None:
    """
    Post ticks, each after any event the app posts first, and pump.

    Parameters
    ----------
    app : App[S]
        The app, asked for an event before each tick.
    ports : KitPorts
        Adapters.
    queue : asyncio.Queue[object]
        Event queue.
    every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Called often to process window events.
    """
    elapsed_s = every
    while True:
        if elapsed_s >= every:
            await tick(app=app, ports=ports, queue=queue)
            elapsed_s = 0.0
        if pump:
            pump()
        await asyncio.sleep(TICK_PAUSE)
        elapsed_s += TICK_PAUSE


async def tick[S](
    *, app: App[S], ports: KitPorts, queue: asyncio.Queue[object],
) -> None:
    """
    Post the app's event before the tick, if it has one, then a tick.

    Parameters
    ----------
    app : App[S]
        The app.
    ports : KitPorts
        Adapters.
    queue : asyncio.Queue[object]
        Event queue.
    """
    seen = await app.before_tick()
    if seen is not None:
        await queue.put(seen)
    clock = ports.clock
    synced = clock.synced()
    await queue.put(Tick(now=clock.now(), synced=synced))


def starting[S](*, app: App[S], ports: KitPorts) -> KitState[S]:
    """
    Make the kit state at start-up from the state file.

    What was not read from the file is written on the first tick, and
    until that tick says whether the clock is synchronised the kit waits
    for it.

    Parameters
    ----------
    app : App[S]
        The app, which makes its own state from its section.
    ports : KitPorts
        Adapters.

    Returns
    -------
    KitState[S]
        The kit state, last written now.
    """
    loaded = ports.states.load()
    now = ports.clock.now()
    device = DeviceState(
        now=now, settings=loaded.device, clock=ClockWait(known=False),
    )
    state = app.start(section=loaded.app, stored=loaded.stored, device=device)
    kit = KitState(
        device=device, app=state, stored=loaded.stored, written_at=now,
    )
    if not loaded.stored:
        return kit
    return replace(kit, saved=kit_saved(kit=kit, app=app))


# pylint: disable-next=too-many-arguments
async def handle[S](
    *,
    kit: KitState[S],
    app: App[S],
    zone: tzinfo,
    event: object,
    ports: KitPorts,
    queue: asyncio.Queue[object],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
) -> KitState[S]:
    """
    Advance the kit by one event and carry out its effects.

    The view is drawn only once no other event is waiting, so a burst
    of knob turns draws one frame of the last state, not one per turn.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    zone : tzinfo
        Local time zone.
    event : object
        Event.
    ports : KitPorts
        Adapters.
    queue : asyncio.Queue[object]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.

    Returns
    -------
    KitState[S]
        New kit state.
    """
    if isinstance(event, Tick):
        ports.observer.ticked()
    before = kit
    kit, effects = step(kit=kit, app=app, zone=zone, event=event)
    pended(outputs=outputs, device=kit.device)
    reported(
        ports=ports, app=app, before=before, kit=kit, event=event,
        outputs=outputs,
    )
    await carry_out(
        effects=effects,
        ports=ports,
        app=app,
        queue=queue,
        tasks=tasks,
        outputs=outputs,
    )
    if queue.empty() or not kit.running:
        await draw_pending(ports=ports, app=app, outputs=outputs)
    return kit


def pended(*, outputs: Outputs, device: DeviceState) -> None:
    """
    Set how the waiting view is shown from the device state.

    Parameters
    ----------
    outputs : Outputs
        Holds the waiting view.
    device : DeviceState
        Device state after the event.
    """
    pending = outputs.pending
    pending.turned = device.settings.rotate
    pending.clean_after = device.settings.clean_after
    pending.quick = quick_now(device=device)
    pending.asleep = device.panel.asleep


# pylint: disable-next=too-many-arguments
def reported[S](
    *,
    ports: KitPorts,
    app: App[S],
    before: KitState[S],
    kit: KitState[S],
    event: object,
    outputs: Outputs,
) -> None:
    """
    Tell the observers and the app what one event changed.

    Parameters
    ----------
    ports : KitPorts
        Adapters.
    app : App[S]
        The app, told of its own step.
    before : KitState[S]
        Kit state before the event.
    kit : KitState[S]
        Kit state after it.
    event : object
        The event.
    outputs : Outputs
        Holds the statuses last reported.
    """
    settings = kit.device.settings
    if settings != before.device.settings:
        ports.observer.device_settings_seen(settings=settings)
    app.observe(before=before.app, after=kit.app, event=event)
    report_status(
        observer=ports.observer, kit=kit, reported=outputs.reported,
    )
    report_app_status(
        observer=ports.observer, app=app, kit=kit, reported=outputs.reported,
    )


async def opened[S](
    *,
    app: App[S],
    ports: KitPorts,
    queue: asyncio.Queue[object],
    outputs: Outputs,
) -> KitState[S]:
    """
    Make the starting state, report it and post the keys' state.

    Parameters
    ----------
    app : App[S]
        The app.
    ports : KitPorts
        Adapters.
    queue : asyncio.Queue[object]
        Event queue.
    outputs : Outputs
        Holds the statuses last reported.

    Returns
    -------
    KitState[S]
        The starting kit state.
    """
    kit = starting(app=app, ports=ports)
    await leftover_stopped(ports=ports.device)
    known = key_known(ports=ports.device)
    if known is not None:
        queue.put_nowait(known)
    observer = ports.observer
    observer.device_settings_seen(settings=kit.device.settings)
    report_status(observer=observer, kit=kit, reported=outputs.reported)
    report_app_status(
        observer=observer, app=app, kit=kit, reported=outputs.reported,
    )
    return kit


# pylint: disable-next=too-many-arguments
async def run[S](
    *,
    app: App[S],
    ports: KitPorts,
    zone: tzinfo,
    tick_every: float = 5.0,
    pump: Callable[[], None] | None = None,
    post_hook: Callable[[Callable[[object], None]], None] | None = None,
    max_events: int | None = None,
) -> KitState[S]:
    """
    Run an app until quit, or until max_events events have been handled.

    Background tasks are cancelled and awaited before this returns.
    Observer errors are logged and never stop the loop. Worker threads
    started by to_thread are not cancelled; asyncio.run waits for them
    when it exits.

    Parameters
    ----------
    app : App[S]
        The app.
    ports : KitPorts
        Adapters.
    zone : tzinfo
        Local time zone, for the panel screens' clock and the clock wait.
    tick_every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Window event pump.
    post_hook : Callable[[Callable[[object], None]], None] | None
        Receives a function that posts events (used for keyboard knobs).
    max_events : int | None
        Stop after this many events (tests).

    Returns
    -------
    KitState[S]
        Final kit state.

    Raises
    ------
    ValueError
        At start-up, before anything is read, when the kit and the app
        register the same migration step.
    """
    chain(kit=KIT_MIGRATIONS, app=app.migrations())
    ports = replace(ports, observer=ObserverGroup(observers=[ports.observer]))
    queue: asyncio.Queue[object] = asyncio.Queue()
    if post_hook:
        post_hook(queue.put_nowait)
    outputs = Outputs(saver=StateSaver(states=ports.states))
    kit = await opened(app=app, ports=ports, queue=queue, outputs=outputs)
    tasks: set[asyncio.Task[None]] = set()
    clock = started_ticker(
        app=app, ports=ports, queue=queue, every=tick_every, pump=pump,
    )
    try:
        kit = await handled(
            kit=kit,
            app=app,
            zone=zone,
            ports=ports,
            queue=queue,
            tasks=tasks,
            outputs=outputs,
            max_events=max_events,
        )
    finally:
        await stop(tasks={clock, *tasks})
        await leftover_stopped(ports=ports.device)
    return kit


def started_ticker[S](
    *,
    app: App[S],
    ports: KitPorts,
    queue: asyncio.Queue[object],
    every: float,
    pump: Callable[[], None] | None,
) -> asyncio.Task[None]:
    """
    Start the ticker; if it crashes, the kit is asked to quit.

    Parameters
    ----------
    app : App[S]
        The app.
    ports : KitPorts
        Adapters.
    queue : asyncio.Queue[object]
        Event queue.
    every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Window event pump.

    Returns
    -------
    asyncio.Task[None]
        The ticker's task.
    """
    clock = asyncio.create_task(
        ticker(app=app, ports=ports, queue=queue, every=every, pump=pump),
    )
    clock.add_done_callback(functools.partial(ticker_finished, queue=queue))
    return clock


# pylint: disable-next=too-many-arguments
async def handled[S](
    *,
    kit: KitState[S],
    app: App[S],
    zone: tzinfo,
    ports: KitPorts,
    queue: asyncio.Queue[object],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
    max_events: int | None,
) -> KitState[S]:
    """
    Handle events until quit or `max_events`, then draw what is pending.

    Parameters
    ----------
    kit : KitState[S]
        Starting kit state.
    app : App[S]
        The app.
    zone : tzinfo
        Local time zone.
    ports : KitPorts
        Adapters.
    queue : asyncio.Queue[object]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.
    max_events : int | None
        Stop after this many events (tests).

    Returns
    -------
    KitState[S]
        Final kit state.
    """
    count = 0
    while kit.running and (max_events is None or count < max_events):
        kit = await handle(
            kit=kit,
            app=app,
            zone=zone,
            event=await queue.get(),
            ports=ports,
            queue=queue,
            tasks=tasks,
            outputs=outputs,
        )
        count += 1
    await draw_pending(ports=ports, app=app, outputs=outputs)
    return kit


async def stop(*, tasks: set[asyncio.Task[None]]) -> None:
    """
    Cancel background tasks and wait until they have finished.

    Worker threads started by to_thread are not cancelled; asyncio.run
    joins them when it exits.

    Parameters
    ----------
    tasks : set[asyncio.Task[None]]
        Tasks to cancel.
    """
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
