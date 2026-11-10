"""Handlers for the Settings menu and confirming on the panel."""

# An app package keeps a twin of this module until it switches over.
# pylint: disable=duplicate-code

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.app import App
from paperroute_device.core.device import on_power, on_reset, probe_effects
from paperroute_device.core.device_events import ConfirmAsked, PowerRequested
from paperroute_device.core.device_model import (
    AppRow,
    Confirm,
    ConfirmAction,
    Menu,
    MenuRow,
    Origin,
    PanelScreen,
    PowerAction,
    ResetKind,
    menu_rows,
)
from paperroute_device.core.device_state import DeviceState, DeviceStep
from paperroute_device.core.kit_state import (
    KitState,
    KitStep,
    with_app,
    with_device,
)
from paperroute_device.core.sleep import slept
from paperroute_device.core.wifi import setup_started
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)

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


def with_menu(*, device: DeviceState, menu: Menu | None) -> DeviceState:
    """
    Set the open menu.

    Parameters
    ----------
    device : DeviceState
        Device state.
    menu : Menu | None
        The menu, or None to close it.

    Returns
    -------
    DeviceState
        Device state with the menu set.
    """
    return replace(device, panel=replace(device.panel, menu=menu))


def with_confirm(
    *, device: DeviceState, confirm: Confirm | None,
) -> DeviceState:
    """
    Set the question shown.

    Parameters
    ----------
    device : DeviceState
        Device state.
    confirm : Confirm | None
        The question, or None to clear it.

    Returns
    -------
    DeviceState
        Device state with the question set.
    """
    return replace(device, panel=replace(device.panel, confirm=confirm))


def opened(*, device: DeviceState) -> DeviceState:
    """
    Open the Settings menu on its first row.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceState
        Device state with the menu open. The caller redraws.
    """
    return with_menu(device=device, menu=Menu())


def menu_knob[S](
    *, kit: KitState[S], app: App[S], event: Touch,
) -> KitStep[S]:
    """
    Apply a knob event to the open menu.

    On the list a bottom turn moves, a bottom press opens the row and a
    top press closes the menu; on the Device and phone screens a top
    press goes back to the list. Everything else does nothing.

    Parameters
    ----------
    kit : KitState[S]
        Kit state, with the menu open.
    app : App[S]
        The app, whose rows sit among the kit's.
    event : Touch
        Knob event.

    Returns
    -------
    KitStep[S]
        New state and effects; `kit` itself when the event does nothing.
    """
    device = kit.device
    menu = device.panel.menu or Menu()
    rows = menu_rows(app_rows=app.menu_rows)
    if menu.screen is not PanelScreen.MENU:
        if event != TOP_PRESS:
            return kit, ()
        back = with_menu(
            device=device, menu=replace(menu, screen=PanelScreen.MENU),
        )
        return with_device(kit=kit, device=back), ()
    match event:
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            device = moved(
                device=device, menu=menu, steps=steps, rows=len(rows),
            )
            return with_device(kit=kit, device=device), ()
        case KnobPressed(knob=Knob.BOTTOM):
            return row_chosen(kit=kit, app=app, row=rows[menu.cursor])
        case KnobPressed(knob=Knob.TOP):
            closed = with_menu(device=device, menu=None)
            return with_device(kit=kit, device=closed), ()
    return kit, ()


def moved(
    *, device: DeviceState, menu: Menu, steps: int, rows: int,
) -> DeviceState:
    """
    Move the menu's cursor, keeping it on the rows.

    Parameters
    ----------
    device : DeviceState
        Device state.
    menu : Menu
        The open menu.
    steps : int
        Detents turned.
    rows : int
        How many rows the menu has.

    Returns
    -------
    DeviceState
        Device state with the cursor moved; `device` itself at either
        end.
    """
    cursor = min(max(menu.cursor + steps, 0), rows - 1)
    if cursor == menu.cursor:
        return device
    return with_menu(device=device, menu=replace(menu, cursor=cursor))


def row_chosen[S](
    *, kit: KitState[S], app: App[S], row: MenuRow | AppRow,
) -> KitStep[S]:
    """
    Open a menu row.

    Parameters
    ----------
    kit : KitState[S]
        Kit state, with the menu open.
    app : App[S]
        The app.
    row : MenuRow | AppRow
        The row pressed.

    Returns
    -------
    KitStep[S]
        For an app's row, what `App.on_row` does and then the Device
        screen; the Device screen with a probe when one is due, the phone
        screen, a confirm, the sleep screen, or Wi-Fi setup scanning.
    """
    device = kit.device
    menu = device.panel.menu or Menu()
    effects: tuple[object, ...] = ()
    if isinstance(row, AppRow):
        state, effects = app.on_row(state=kit.app, device=device, row=row)
        kit = with_app(kit=kit, app=state)
    if isinstance(row, AppRow) or row is MenuRow.DEVICE:
        device, probes = device_shown(device=device, menu=menu)
        return with_device(kit=kit, device=device), (*effects, *probes)
    if row in CONFIRM_FOR:
        confirm = Confirm(
            action=CONFIRM_FOR[row], origin=Origin.PANEL, asked_at=device.now,
        )
        device = with_confirm(device=device, confirm=confirm)
    elif row is MenuRow.PHONE:
        phone = replace(menu, screen=PanelScreen.PHONE)
        device = with_menu(device=device, menu=phone)
    elif row is MenuRow.SLEEP:
        device = slept(device=device)
    else:
        device, effects = setup_started(device=device)
    return with_device(kit=kit, device=device), effects


def device_shown(*, device: DeviceState, menu: Menu) -> DeviceStep:
    """
    Show the Device screen.

    Parameters
    ----------
    device : DeviceState
        Device state, with the menu open.
    menu : Menu
        The open menu.

    Returns
    -------
    DeviceStep
        Device state on the Device screen, and a probe when one is due.
    """
    menu = replace(menu, screen=PanelScreen.DEVICE)
    return probe_effects(device=with_menu(device=device, menu=menu))


def accepted[S](
    *, kit: KitState[S], app: App[S], confirm: Confirm,
) -> KitStep[S]:
    """
    Do what a confirm asked.

    Parameters
    ----------
    kit : KitState[S]
        Kit state, with the confirm shown.
    app : App[S]
        The app.
    confirm : Confirm
        The confirm accepted.

    Returns
    -------
    KitStep[S]
        Kit state with the confirm and menu gone, and the restart,
        shutdown, reset or Wi-Fi setup under way.
    """
    action = confirm.action
    device = with_menu(
        device=with_confirm(device=kit.device, confirm=None), menu=None,
    )
    kit = with_device(kit=kit, device=device)
    if action in POWER_FOR:
        request = PowerRequested(action=POWER_FOR[action])
        return on_power(kit=kit, app=app, event=request)
    if action in RESET_FOR:
        device, effects = on_reset(device=device, kind=RESET_FOR[action])
    else:
        device, effects = setup_started(device=device)
    return with_device(kit=kit, device=device), effects


def confirm_knob[S](
    *, kit: KitState[S], app: App[S], event: Touch,
) -> KitStep[S]:
    """
    Answer the confirm shown.

    Parameters
    ----------
    kit : KitState[S]
        Kit state, with a confirm shown.
    app : App[S]
        The app.
    event : Touch
        Knob event.

    Returns
    -------
    KitStep[S]
        A bottom press accepts, a top press cancels back to what was
        under it; anything else does nothing.
    """
    confirm = kit.device.panel.confirm
    if confirm is not None and event == BOTTOM_PRESS:
        return accepted(kit=kit, app=app, confirm=confirm)
    if event == TOP_PRESS:
        device = with_confirm(device=kit.device, confirm=None)
        return with_device(kit=kit, device=device), ()
    return kit, ()


def on_asked(*, device: DeviceState, event: ConfirmAsked) -> DeviceState:
    """
    Show a phone's ask on the panel.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : ConfirmAsked
        What the phone asked for.

    Returns
    -------
    DeviceState
        Device state with the confirm shown; `device` itself for an
        action outside `PHONE_ASKS`, or while another confirm shows or a
        power action or reset is under way. The caller redraws.
    """
    busy = device.power is not None or device.panel.resetting is not None
    if busy or device.panel.confirm is not None:
        return device
    if event.action not in PHONE_ASKS:
        return device
    confirm = Confirm(
        action=event.action, origin=Origin.PHONE, asked_at=device.now,
    )
    return with_confirm(device=device, confirm=confirm)


def confirm_expired(*, device: DeviceState) -> DeviceState:
    """
    Cancel a confirm left alone for `CONFIRM_WAIT`.

    Parameters
    ----------
    device : DeviceState
        Device state at the tick's time.

    Returns
    -------
    DeviceState
        Device state with the confirm gone; unchanged otherwise. The
        caller redraws.
    """
    confirm = device.panel.confirm
    if confirm is None or device.now - confirm.asked_at < CONFIRM_WAIT:
        return device
    return with_confirm(device=device, confirm=None)


def touched(*, device: DeviceState) -> DeviceState:
    """
    Note a knob touch while the menu is open.

    Parameters
    ----------
    device : DeviceState
        Device state after the touch.

    Returns
    -------
    DeviceState
        Device state with the touch time noted; unchanged with the menu
        closed.
    """
    if device.panel.menu is None:
        return device
    panel = replace(device.panel, touched_at=device.now)
    return replace(device, panel=panel)


def menu_idle(*, device: DeviceState) -> DeviceState:
    """
    Close the menu left alone for `MENU_IDLE`.

    A confirm shown keeps the menu open; it has its own wait.

    Parameters
    ----------
    device : DeviceState
        Device state at the tick's time.

    Returns
    -------
    DeviceState
        Device state with the menu closed; unchanged otherwise. The
        caller redraws.
    """
    panel = device.panel
    if panel.menu is None or panel.confirm is not None:
        return device
    if panel.touched_at and device.now - panel.touched_at < MENU_IDLE:
        return device
    return with_menu(device=device, menu=None)
