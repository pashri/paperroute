"""Handlers for the Settings menu and confirming on the panel."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.device_events import ConfirmAsked, PowerRequested
from paperroute_device.core.device_model import (
    MENU_ROWS,
    Confirm,
    ConfirmAction,
    Menu,
    MenuRow,
    Origin,
    PanelScreen,
    PowerAction,
    ResetKind,
)
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_transit.core.device import on_power, on_reset, probe_effects
from paperroute_transit.core.messages import Effect
from paperroute_transit.core.rebuild import forced
from paperroute_transit.core.sleep import slept
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import settled
from paperroute_transit.core.wifi import setup_started

CONFIRM_WAIT: Final[timedelta] = timedelta(seconds=60)
MENU_IDLE: Final[timedelta] = timedelta(minutes=2)
PHONE_ASKS: Final[frozenset[ConfirmAction]] = frozenset(
    {
        ConfirmAction.RESET_SETTINGS,
        ConfirmAction.ERASE,
        ConfirmAction.JOIN_WIFI,
    },
)
CONFIRM_FOR: Final[dict[MenuRow, ConfirmAction]] = {
    MenuRow.RESTART: ConfirmAction.RESTART,
    MenuRow.SHUT_DOWN: ConfirmAction.SHUT_DOWN,
    MenuRow.RESET: ConfirmAction.RESET_SETTINGS,
}
POWER_FOR: Final[dict[ConfirmAction, PowerAction]] = {
    ConfirmAction.RESTART: PowerAction.RESTART,
    ConfirmAction.SHUT_DOWN: PowerAction.OFF,
}
RESET_FOR: Final[dict[ConfirmAction, ResetKind]] = {
    ConfirmAction.RESET_SETTINGS: ResetKind.SETTINGS,
    ConfirmAction.ERASE: ResetKind.ERASE,
}
TOP_PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)
BOTTOM_PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)

type Touch = KnobEvent | KnobsHeld


def with_menu(*, state: State, menu: Menu | None) -> State:
    """
    Set the open menu.

    Parameters
    ----------
    state : State
        State.
    menu : Menu | None
        The menu, or None to close it.

    Returns
    -------
    State
        State with the menu set.
    """
    return replace(state, panel=replace(state.panel, menu=menu))


def with_confirm(*, state: State, confirm: Confirm | None) -> State:
    """
    Set the question shown.

    Parameters
    ----------
    state : State
        State.
    confirm : Confirm | None
        The question, or None to clear it.

    Returns
    -------
    State
        State with the question set.
    """
    return replace(state, panel=replace(state.panel, confirm=confirm))


def opened(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Open the Settings menu on its first row.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the menu open, and its redraw.
    """
    return settled(state=with_menu(state=state, menu=Menu()))


def menu_knob(
    *, state: State, event: Touch
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a knob event to the open menu.

    On the list a bottom turn moves, a bottom press opens the row and a
    top press closes the menu; on the Device and phone screens a top
    press goes back to the list. Everything else does nothing.

    Parameters
    ----------
    state : State
        State, with the menu open.
    event : Touch
        Knob event.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when the event does nothing.
    """
    menu = state.panel.menu or Menu()
    if menu.screen is not PanelScreen.MENU:
        if event != TOP_PRESS:
            return state, ()
        back = replace(menu, screen=PanelScreen.MENU)
        return settled(state=with_menu(state=state, menu=back))
    match event:
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            return moved(state=state, menu=menu, steps=steps)
        case KnobPressed(knob=Knob.BOTTOM):
            return row_chosen(state=state, row=MENU_ROWS[menu.cursor])
        case KnobPressed(knob=Knob.TOP):
            return settled(state=with_menu(state=state, menu=None))
    return state, ()


def moved(
    *, state: State, menu: Menu, steps: int
) -> tuple[State, tuple[Effect, ...]]:
    """
    Move the menu's cursor, keeping it on the rows.

    Parameters
    ----------
    state : State
        State.
    menu : Menu
        The open menu.
    steps : int
        Detents turned.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and its redraw; unchanged at either end.
    """
    cursor = min(max(menu.cursor + steps, 0), len(MENU_ROWS) - 1)
    if cursor == menu.cursor:
        return state, ()
    menu = replace(menu, cursor=cursor)
    return settled(state=with_menu(state=state, menu=menu))


def row_chosen(
    *, state: State, row: MenuRow
) -> tuple[State, tuple[Effect, ...]]:
    """
    Open a menu row.

    Parameters
    ----------
    state : State
        State, with the menu open.
    row : MenuRow
        The row pressed.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The Device screen with a probe when one is due and a forced
        rebuild for its row, the phone screen, a confirm, the sleep
        screen, or Wi-Fi setup scanning.
    """
    menu = state.panel.menu or Menu()
    if row in CONFIRM_FOR:
        confirm = Confirm(
            action=CONFIRM_FOR[row], origin=Origin.PANEL, asked_at=state.now,
        )
        return settled(state=with_confirm(state=state, confirm=confirm))
    if row in (MenuRow.DEVICE, MenuRow.REBUILD):
        return device_shown(state=state, menu=menu, row=row)
    if row is MenuRow.PHONE:
        menu = replace(menu, screen=PanelScreen.PHONE)
        return settled(state=with_menu(state=state, menu=menu))
    if row is MenuRow.SLEEP:
        return settled(state=slept(state=state))
    state, effects = setup_started(state=state)
    return settled(state=state, effects=effects)


def device_shown(
    *, state: State, menu: Menu, row: MenuRow
) -> tuple[State, tuple[Effect, ...]]:
    """
    Show the Device screen, rebuilding the timetable first if asked.

    Parameters
    ----------
    state : State
        State, with the menu open.
    menu : Menu
        The open menu.
    row : MenuRow
        Device, or Rebuild timetable now.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State on the Device screen, a forced rebuild for its row unless
        one runs, a probe when one is due, and the redraw.
    """
    rebuilds: tuple[Effect, ...] = ()
    if row is MenuRow.REBUILD:
        state, rebuilds = forced(state=state)
    menu = replace(menu, screen=PanelScreen.DEVICE)
    state, probes = probe_effects(state=with_menu(state=state, menu=menu))
    return settled(state=state, effects=(*rebuilds, *probes))


def accepted(
    *, state: State, confirm: Confirm
) -> tuple[State, tuple[Effect, ...]]:
    """
    Do what a confirm asked.

    Parameters
    ----------
    state : State
        State, with the confirm shown.
    confirm : Confirm
        The confirm accepted.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the confirm and menu gone, and the restart, shutdown,
        reset or Wi-Fi setup under way.
    """
    action = confirm.action
    state = with_menu(state=with_confirm(state=state, confirm=None), menu=None)
    if action in POWER_FOR:
        request = PowerRequested(action=POWER_FOR[action])
        return on_power(state=state, event=request)
    if action in RESET_FOR:
        return on_reset(state=state, kind=RESET_FOR[action])
    state, effects = setup_started(state=state)
    return settled(state=state, effects=effects)


def confirm_knob(
    *, state: State, event: Touch
) -> tuple[State, tuple[Effect, ...]]:
    """
    Answer the confirm shown.

    Parameters
    ----------
    state : State
        State, with a confirm shown.
    event : Touch
        Knob event.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        A bottom press accepts, a top press cancels back to what was
        under it; anything else does nothing.
    """
    confirm = state.panel.confirm
    if confirm is not None and event == BOTTOM_PRESS:
        return accepted(state=state, confirm=confirm)
    if event == TOP_PRESS:
        return settled(state=with_confirm(state=state, confirm=None))
    return state, ()


def on_asked(
    *, state: State, event: ConfirmAsked
) -> tuple[State, tuple[Effect, ...]]:
    """
    Show a phone's ask on the panel.

    Parameters
    ----------
    state : State
        State.
    event : ConfirmAsked
        What the phone asked for.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the confirm shown, and its redraw; unchanged for an
        action outside `PHONE_ASKS`, or while another confirm shows or
        a power action or reset is under way.
    """
    busy = state.power is not None or state.panel.resetting is not None
    if busy or state.panel.confirm is not None:
        return state, ()
    if event.action not in PHONE_ASKS:
        return state, ()
    confirm = Confirm(
        action=event.action, origin=Origin.PHONE, asked_at=state.now,
    )
    return settled(state=with_confirm(state=state, confirm=confirm))


def confirm_expired(*, state: State) -> State:
    """
    Cancel a confirm left alone for `CONFIRM_WAIT`.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    State
        State with the confirm gone; unchanged otherwise. The caller
        redraws.
    """
    confirm = state.panel.confirm
    if confirm is None or state.now - confirm.asked_at < CONFIRM_WAIT:
        return state
    return with_confirm(state=state, confirm=None)


def touched(*, state: State) -> State:
    """
    Note a knob touch while the menu is open.

    Parameters
    ----------
    state : State
        State after the touch.

    Returns
    -------
    State
        State with the touch time noted; unchanged with the menu closed.
    """
    if state.panel.menu is None:
        return state
    return replace(state, panel=replace(state.panel, touched_at=state.now))


def menu_idle(*, state: State) -> State:
    """
    Close the menu left alone for `MENU_IDLE`.

    A confirm shown keeps the menu open; it has its own wait.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    State
        State with the menu closed; unchanged otherwise. The caller
        redraws.
    """
    panel = state.panel
    if panel.menu is None or panel.confirm is not None:
        return state
    if panel.touched_at and state.now - panel.touched_at < MENU_IDLE:
        return state
    return with_menu(state=state, menu=None)
