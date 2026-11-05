"""Where effects draw and write, and carrying them out."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

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

logger = logging.getLogger(__name__)

PANEL_SETTLE: Final[float] = 15  # seconds


@dataclass
class PendingView:
    """
    The last view asked for and not drawn yet.

    Attributes
    ----------
    view : View | None
        The view, or None when the screen is up to date.
    full : bool
        Whether any render since the last frame asked for a full refresh.
    turned : bool
        Whether frames are shown upside down.
    quick : bool
        Whether a knob was turned just now.
    """

    view: View | None = None
    full: bool = False
    turned: bool = False
    quick: bool = False


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
    pending = outputs.pending
    view, full = pending.view, pending.full
    if view is None:
        return
    pending.view, pending.full = None, False
    await render(
        ports=ports,
        view=view,
        maps=outputs.maps,
        full=full,
        turned=pending.turned,
        quick=pending.quick,
    )


async def settle_panel(*, ports: Ports) -> None:
    """
    Wait for the panel to finish drawing, warning if it does not.

    Parameters
    ----------
    ports : Ports
        Adapters.
    """
    settled = await asyncio.to_thread(
        ports.display.settle, timeout=PANEL_SETTLE,
    )
    if not settled:
        logger.warning('panel still busy, carrying on')


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

    A full Render stays full when a later one in the burst replaces its
    view. A ControlPower or ResetDevice draws the waiting view first, so the
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
            outputs.pending.full = outputs.pending.full or effect.full
            continue
        if isinstance(effect, ControlPower | ResetDevice):
            await draw_pending(ports=ports, outputs=outputs)
            await settle_panel(ports=ports)
        await perform(
            ports=ports,
            effect=effect,
            queue=queue,
            tasks=tasks,
            maps=outputs.maps,
            saver=outputs.saver,
        )
