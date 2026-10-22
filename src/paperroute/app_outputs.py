"""Where effects draw and write, and carrying them out."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from paperroute.app_device import Reported
from paperroute.app_effects import StateSaver
from paperroute.app_ports import Ports
from paperroute.app_render import MapRenderer, View, render
from paperroute.app_work import perform
from paperroute.core.messages import (
    ControlPower,
    Effect,
    Event,
    Render,
    ResetDevice,
)


@dataclass
class PendingView:
    """
    The last view asked for and not drawn yet.

    Attributes
    ----------
    view : View | None
        The view, or None when the screen is up to date.
    """

    view: View | None = None


@dataclass(frozen=True)
class Outputs:
    """
    What effects draw and write through.

    Attributes
    ----------
    maps : MapRenderer
        Draws the views with a map.
    saver : StateSaver
        Writes the saved state.
    pending : PendingView
        The view waiting to be drawn.
    reported : Reported
        The device status reported last.
    """

    maps: MapRenderer
    saver: StateSaver
    pending: PendingView = field(default_factory=PendingView)
    reported: Reported = field(default_factory=Reported)


async def draw_pending(*, ports: Ports, outputs: Outputs) -> None:
    """
    Draw the waiting view, if there is one.

    Parameters
    ----------
    ports : Ports
        Adapters.
    outputs : Outputs
        What effects draw and write through.
    """
    view = outputs.pending.view
    if view is None:
        return
    outputs.pending.view = None
    await render(ports=ports, view=view, maps=outputs.maps)


async def carry_out(
    *,
    effects: Sequence[Effect],
    ports: Ports,
    queue: asyncio.Queue[Event],
    tasks: set[asyncio.Task[None]],
    outputs: Outputs,
) -> None:
    """
    Perform an event's effects in order, keeping its view to draw later.

    A ControlPower or ResetDevice draws the waiting view first, so the
    notice is on screen before the device goes down or is reset.

    Parameters
    ----------
    effects : Sequence[Effect]
        The effects.
    ports : Ports
        Adapters.
    queue : asyncio.Queue[Event]
        Event queue.
    tasks : set[asyncio.Task[None]]
        Background tasks kept alive until done.
    outputs : Outputs
        What effects draw and write through.
    """
    for effect in effects:
        if isinstance(effect, Render):
            outputs.pending.view = effect.view
            continue
        if isinstance(effect, ControlPower | ResetDevice):
            await draw_pending(ports=ports, outputs=outputs)
        await perform(
            ports=ports,
            effect=effect,
            queue=queue,
            tasks=tasks,
            maps=outputs.maps,
            saver=outputs.saver,
        )
