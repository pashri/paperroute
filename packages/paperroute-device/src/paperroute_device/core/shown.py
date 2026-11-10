"""What the panel shows, and how it is drawn."""

from dataclasses import replace
from datetime import tzinfo

from paperroute_device.core.app import App
from paperroute_device.core.banners import banner_line
from paperroute_device.core.device_state import DeviceState
from paperroute_device.core.kit_effects import Render, Stop
from paperroute_device.core.kit_state import KitState, KitStep, with_device
from paperroute_device.core.panel_screens import panel_view


def overlaid(*, device: DeviceState) -> bool:
    """
    Say whether a panel screen shows over the live view.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    bool
        True with a confirm, Wi-Fi setup or menu open.
    """
    panel = device.panel
    return (panel.confirm, panel.wifi, panel.menu) != (None, None, None)


def sleeping(*, device: DeviceState) -> bool:
    """
    Say whether the panel shows the sleep screen.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    bool
        True while asleep with no panel screen shown.
    """
    return device.panel.asleep and not overlaid(device=device)


def current_view[S](
    *, kit: KitState[S], app: App[S], zone: tzinfo,
) -> object | None:
    """
    Build the view for the current state.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    zone : tzinfo
        Time zone of the panel screens' clock.

    Returns
    -------
    object | None
        The confirm, Wi-Fi setup or open menu's screen, else the app's
        sleep screen while asleep, else the app's live view under the
        banner line; None when the app keeps the last frame.
    """
    device, state = kit.device, kit.app
    overlay = panel_view(device=device, app=app, state=state, zone=zone)
    if overlay is not None:
        return overlay
    if device.panel.asleep:
        return app.sleep_view(state=state, device=device)
    banner = banner_line(
        panel=device.panel, app=app, state=state, device=device,
    )
    return app.render(state=state, device=device, banner=banner)


def render_of[S](
    *, device: DeviceState, app: App[S], view: object, menu: bool,
) -> Render:
    """
    Choose how the panel draws a changed view.

    Parameters
    ----------
    device : DeviceState
        Device state after the event, with the last view still recorded.
    app : App[S]
        The app.
    view : object
        The view to draw.
    menu : bool
        Whether the view is a menu: a panel screen or the app's own.

    Returns
    -------
    Render
        Full for the first frame and into or out of the sleep screen;
        on entering a menu clear, on leaving one full; outside menus full
        when `App.needs_full` says so. Otherwise neither.
    """
    last, was_menu = device.last_view, device.in_menu
    if last is None or sleeping(device=device) or device.last_was_sleep:
        return Render(view=view, full=True)
    if menu != was_menu:
        return Render(view=view, full=was_menu, clear=menu)
    full = app.needs_full(before=last, after=view)
    return Render(view=view, full=full and not menu)


def rendered[S](
    *,
    kit: KitState[S],
    app: App[S],
    zone: tzinfo,
    effects: tuple[object, ...] = (),
) -> KitStep[S]:
    """
    Add a Render when the view changed.

    Parameters
    ----------
    kit : KitState[S]
        Kit state after the event.
    app : App[S]
        The app.
    zone : tzinfo
        Time zone of the panel screens' clock.
    effects : tuple[object, ...]
        Effects so far.

    Returns
    -------
    KitStep[S]
        Kit state with the view, whether it is the sleep screen and
        whether it is a menu recorded, and the effects with the Render
        last; unchanged when the view is None or the one last drawn.
    """
    view = current_view(kit=kit, app=app, zone=zone)
    device = kit.device
    if view is None or view == device.last_view:
        return kit, effects
    menu = overlaid(device=device) or app.in_menu(state=kit.app)
    render = render_of(device=device, app=app, view=view, menu=menu)
    device = replace(
        device,
        last_view=view,
        last_was_sleep=sleeping(device=device),
        in_menu=menu,
    )
    return with_device(kit=kit, device=device), (*effects, render)


def settled[S](
    *,
    kit: KitState[S],
    app: App[S],
    zone: tzinfo,
    effects: tuple[object, ...] = (),
) -> KitStep[S]:
    """
    Redraw after a handler, unless it drew already or stops the board.

    Parameters
    ----------
    kit : KitState[S]
        Kit state after the handler.
    app : App[S]
        The app.
    zone : tzinfo
        Time zone of the panel screens' clock.
    effects : tuple[object, ...]
        The handler's effects.

    Returns
    -------
    KitStep[S]
        As `rendered`; unchanged when the effects hold a Render already,
        as a notice or a turned frame does, or a Stop.
    """
    if any(isinstance(effect, Render | Stop) for effect in effects):
        return kit, effects
    return rendered(kit=kit, app=app, zone=zone, effects=effects)
