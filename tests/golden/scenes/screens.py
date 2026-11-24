"""Golden chooser, notice and panel scenes."""

from dataclasses import replace

from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import (
    Confirm,
    Menu,
    PowerAction,
    WifiSetup,
)
from paperroute_device_testkit.device_pages import FACTS
from paperroute_testkit.core.kit_steps import step
from paperroute_transit.core.chooser_lists import turned
from paperroute_transit.core.panel_screens import TilesFacts
from paperroute_transit.core.state_model import State
from tests.core.chooser_walks import at_routes, at_towards
from tests.core.state.helpers import fresh
from tests.golden.scenes.maps import map_scene
from tests.golden.scenes.stops import stop


def chooser() -> State:
    """
    Show the chooser at 606's Towards column.

    Returns
    -------
    State
        State at `START`.
    """
    return replace(stop(), chooser=at_towards())


def chooser_routes() -> State:
    """
    Show the chooser on 609 in a ROUTE column, its preview overflowing.

    Returns
    -------
    State
        State at `START`.
    """
    return replace(
        stop(),
        chooser=turned(chooser=at_routes(), steps=1, reach=None)[0],
    )


def building() -> State:
    """
    Show the Building screen, with no timetable.

    Returns
    -------
    State
        State at `START`.
    """
    state = fresh()
    return replace(state, rebuild=replace(state.rebuild, missing=True))


def notice() -> State:
    """
    Show the notice while shutting down.

    Returns
    -------
    State
        State at `START`, shutting down.
    """
    state, _ = step(stop(), PowerRequested(action=PowerAction.OFF))
    return state


def panel(
    *,
    menu: Menu | None = None,
    wifi: WifiSetup | None = None,
    confirm: Confirm | None = None,
) -> State:
    """
    Show a panel screen over the Stop view.

    Parameters
    ----------
    menu : Menu | None
        The open menu.
    wifi : WifiSetup | None
        The open Wi-Fi setup.
    confirm : Confirm | None
        The question asked.

    Returns
    -------
    State
        State at `START`, with the probe's facts.
    """
    state = stop()
    facts = replace(FACTS, app=TilesFacts(built='2025-10-19T08:00:00+11:00'))
    shown = replace(
        state.panel, facts=facts, menu=menu, wifi=wifi, confirm=confirm,
    )
    return replace(state, panel=shown)


def asleep() -> State:
    """
    Show the sleep screen over the Map scene's pin.

    Returns
    -------
    State
        State at `START`, asleep.
    """
    state = map_scene()
    return replace(state, panel=replace(state.panel, asleep=True))
