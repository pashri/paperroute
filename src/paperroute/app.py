"""Wire the ports to the reducer through one event queue."""

import asyncio
import functools
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Final

from paperroute.app_device import (
    key_known,
    leftover_stopped,
    pins_shown,
    report_pins,
    report_status,
)
from paperroute.app_effects import StateSaver, VersionWatch, filled
from paperroute.app_outputs import Outputs, carry_out, draw_pending
from paperroute.app_ports import Ports
from paperroute.app_render import MapRenderer
from paperroute.app_work import perform
from paperroute.core.messages import Event, QuitRequested, Tick
from paperroute.core.model import Pin
from paperroute.core.refresh import quick_now
from paperroute.core.saved import seeded
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.tiles import TileVariant
from paperroute.observers import ObserverGroup

__all__ = [
    'MapRenderer',
    'Outputs',
    'Ports',
    'handle',
    'perform',
    'run',
    'starting_state',
    'stop',
    'ticker',
]

logger = logging.getLogger(__name__)

TICK_PAUSE: Final[float] = 0.03  # seconds between window pumps


def ticker_finished(
    task: asyncio.Task[None], *, queue: asyncio.Queue[Event]
) -> None:
    """
    If the ticker crashed, log it and post QuitRequested.

    Parameters
    ----------
    task : asyncio.Task[None]
        The finished ticker task.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error('ticker crashed', exc_info=error)
        queue.put_nowait(QuitRequested())


async def ticker(
    *,
    ports: Ports,
    queue: asyncio.Queue[Event],
    every: float,
    pump: Callable[[], None] | None,
) -> None:
    """
    Post ticks, note timetable changes and keep the window responsive.

    The timetable version is posted at start and before any tick it
    changed by; a version read error posts nothing.

    Parameters
    ----------
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Called often to process window events.
    """
    watch = VersionWatch(timetable=ports.timetable)
    elapsed = every
    while True:
        if elapsed >= every:
            await tick(watch=watch, ports=ports, queue=queue)
            elapsed = 0.0
        if pump:
            pump()
        await asyncio.sleep(TICK_PAUSE)
        elapsed += TICK_PAUSE


async def tick(
    *, watch: VersionWatch, ports: Ports, queue: asyncio.Queue[Event],
) -> None:
    """
    Post any timetable change, then a tick.

    Parameters
    ----------
    watch : VersionWatch
        Notes the timetable version.
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    """
    seen = await watch.check()
    if seen is not None:
        await queue.put(seen)
    clock = ports.clock
    synced = clock.synced()
    await queue.put(Tick(now=clock.now(), synced=synced))


def starting_state(*, ports: Ports, seed: Pin | None = None) -> State:
    """
    Make the state at start-up from the saved state, or else the seed.

    Without saved state the seed becomes the one pin, its places filled
    in from the timetable; without either, the defaults show. State not
    from the store is written on the first tick, and until that tick
    says whether the clock is synchronised the app waits for it.

    Parameters
    ----------
    ports : Ports
        Adapters.
    seed : Pin | None
        Pin from the command line or environment.

    Returns
    -------
    State
        State.
    """
    loaded = ports.states.load()
    saved = loaded.saved
    if seed is not None and loaded.stored:
        logger.info('seed ignored: the saved state is used')
    elif seed is not None:
        saved = seeded(pin=filled(timetable=ports.timetable, pin=seed))
        logger.info('state seeded from the command line or environment')
    state = initial_state(
        saved=saved, now=ports.clock.now(), stored=loaded.stored
    )
    return replace(state, clock_known=False)


async def handle(
    *,
    state: State,
    event: Event,
    ports: Ports,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
) -> State:
    """
    Advance the state by one event and carry out its effects.

    The view is drawn only once no other event is waiting, so a burst
    of knob turns draws one frame of the last state, not one per turn.

    Parameters
    ----------
    state : State
        State.
    event : Event
        Event.
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.

    Returns
    -------
    State
        New state.
    """
    if isinstance(event, Tick):
        ports.observer.ticked()
    before = state
    state, effects = step(state, event)
    outputs.pending.turned = state.settings.device.rotate
    outputs.pending.clean_after = state.settings.device.clean_after
    outputs.pending.quick = quick_now(state=state)
    outputs.pending.asleep = state.panel.asleep
    report_step(
        ports=ports, before=before, state=state, event=event, outputs=outputs,
    )
    await carry_out(
        effects=effects,
        ports=ports,
        queue=queue,
        tasks=tasks,
        outputs=outputs,
    )
    if queue.empty() or not state.running:
        await draw_pending(ports=ports, outputs=outputs)
    return state


def report_step(
    *, ports: Ports, before: State, state: State, event: Event,
    outputs: Outputs,
) -> None:
    """
    Tell the observers what one event changed.

    Parameters
    ----------
    ports : Ports
        Adapters.
    before : State
        State before the event.
    state : State
        State after it.
    event : Event
        The event.
    outputs : Outputs
        Holds the status last reported.
    """
    if state.settings != before.settings:
        ports.observer.settings_seen(settings=state.settings)
    report_pins(
        observer=ports.observer,
        before=before.places,
        state=state,
        event=event,
    )
    report_status(
        observer=ports.observer, state=state, reported=outputs.reported,
    )


async def opened(
    *,
    ports: Ports,
    seed: Pin | None,
    queue: asyncio.Queue[Event],
    outputs: Outputs,
) -> State:
    """
    Make the starting state, report it and post the key's state.

    Parameters
    ----------
    ports : Ports
        Adapters.
    seed : Pin | None
        Pin to start with when there is no saved state.
    queue : asyncio.Queue[Event]
        Event queue.
    outputs : Outputs
        Holds the status last reported.

    Returns
    -------
    State
        The starting state.
    """
    state = starting_state(ports=ports, seed=seed)
    ports.observer.settings_seen(settings=state.settings)
    pins_shown(observer=ports.observer, places=state.places)
    report_status(
        observer=ports.observer, state=state, reported=outputs.reported,
    )
    await leftover_stopped(ports=ports.device)
    known = key_known(ports=ports.device)
    if known is not None:
        queue.put_nowait(known)
    return state


async def run(  # pylint: disable=too-many-statements
    *,
    ports: Ports,
    seed: Pin | None = None,
    tick_every: float = 5.0,
    pump: Callable[[], None] | None = None,
    post_hook: Callable[[Callable[[Event], None]], None] | None = None,
    max_events: int | None = None,
    tiles_variant: TileVariant = TileVariant.GREY,
) -> State:
    """
    Run the app until quit, or until max_events events have been handled.

    Background tasks are cancelled and awaited before this returns.
    Observer errors are logged and never stop the loop.
    Worker threads started by to_thread are not cancelled; asyncio.run
    waits for them when it exits.

    Parameters
    ----------
    ports : Ports
        Adapters.
    seed : Pin | None
        Pin to start with when there is no saved state.
    tick_every : float
        Seconds between ticks.
    pump : Callable[[], None] | None
        Window event pump.
    post_hook : Callable[[Callable[[Event], None]], None] | None
        Receives a function that posts events (used for keyboard knobs).
    max_events : int | None
        Stop after this many events (tests).
    tiles_variant : TileVariant
        Which tile rendering the Map draws.

    Returns
    -------
    State
        Final state.
    """
    ports = replace(
        ports, observer=ObserverGroup(observers=[ports.observer])
    )
    queue: asyncio.Queue[Event] = asyncio.Queue()
    if post_hook:
        post_hook(queue.put_nowait)
    outputs = Outputs(
        maps=MapRenderer(tiles=ports.tiles, variant=tiles_variant),
        saver=StateSaver(states=ports.states),
    )
    state = await opened(
        ports=ports, seed=seed, queue=queue, outputs=outputs,
    )
    tasks: set[asyncio.Task[None]] = set()
    clock = started_ticker(
        ports=ports, queue=queue, every=tick_every, pump=pump,
    )
    try:
        state = await handled(
            state=state,
            ports=ports,
            queue=queue,
            tasks=tasks,
            outputs=outputs,
            max_events=max_events,
        )
    finally:
        await stop(tasks={clock, *tasks})
        await ports.builder.stop()
        await leftover_stopped(ports=ports.device)
    return state


def started_ticker(
    *,
    ports: Ports,
    queue: asyncio.Queue[Event],
    every: float,
    pump: Callable[[], None] | None,
) -> asyncio.Task[None]:
    """
    Start the ticker; if it crashes, the app is asked to quit.

    Parameters
    ----------
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
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
        ticker(ports=ports, queue=queue, every=every, pump=pump)
    )
    clock.add_done_callback(
        functools.partial(ticker_finished, queue=queue)
    )
    return clock


async def handled(
    *,
    state: State,
    ports: Ports,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
    max_events: int | None,
) -> State:
    """
    Handle events until quit or `max_events`, then draw what is pending.

    Parameters
    ----------
    state : State
        Starting state.
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.
    max_events : int | None
        Stop after this many events (tests).

    Returns
    -------
    State
        Final state.
    """
    count = 0
    while state.running and (max_events is None or count < max_events):
        state = await handle(
            state=state,
            event=await queue.get(),
            ports=ports,
            queue=queue,
            tasks=tasks,
            outputs=outputs,
        )
        count += 1
    await draw_pending(ports=ports, outputs=outputs)
    return state


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
