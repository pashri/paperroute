"""Build the panel's settings screens from the device state."""

# An app package keeps a twin of this module until it switches over.
# pylint: disable=duplicate-code

from collections.abc import Sequence
from datetime import tzinfo

from paperroute_device.core.app import App
from paperroute_device.core.device_model import (
    AppRow,
    DeviceFacts,
    MenuRow,
    PanelScreen,
    WifiStatus,
    menu_rows,
)
from paperroute_device.core.device_state import DeviceState
from paperroute_device.core.panel_views import (
    LOCAL_NAME,
    MENU_VISIBLE,
    UNKNOWN,
    DeviceView,
    InfoRow,
    MenuView,
    PanelView,
    PhoneView,
    confirm_view,
    strength,
)
from paperroute_device.core.text import DOT, clock_text
from paperroute_device.core.wifi import wifi_setup_view


def menu_view(
    *,
    device: DeviceState,
    rows: Sequence[MenuRow | AppRow],
    zone: tzinfo,
) -> MenuView:
    """
    Build the Settings menu.

    Parameters
    ----------
    device : DeviceState
        Device state, with the menu open.
    rows : Sequence[MenuRow | AppRow]
        The menu's rows, top to bottom.
    zone : tzinfo
        Time zone of the header's clock.

    Returns
    -------
    MenuView
        Every row, the cursor, and the top row scrolled to keep the
        cursor in sight.
    """
    menu = device.panel.menu
    cursor = menu.cursor if menu else 0
    return MenuView(
        clock=clock_text(moment=device.now, zone=zone),
        rows=tuple(
            row.value if isinstance(row, MenuRow) else row.label
            for row in rows
        ),
        cursor=cursor,
        first=max(0, cursor - MENU_VISIBLE + 1),
    )


def phone_view(*, device: DeviceState, zone: tzinfo) -> PhoneView:
    """
    Build the Open on your phone screen.

    Parameters
    ----------
    device : DeviceState
        Device state.
    zone : tzinfo
        Time zone of the header's clock.

    Returns
    -------
    PhoneView
        The code opens the IP when known, as phones often can't find
        ``paperroute.local``; that name is always listed.
    """
    facts = device.panel.facts
    wifi = facts.wifi if facts else WifiStatus()
    url = f'http://{wifi.ip}/' if wifi.ip else f'http://{LOCAL_NAME}/'
    ips = (f'http://{wifi.ip}',) if wifi.ip else ()
    return PhoneView(
        clock=clock_text(moment=device.now, zone=zone),
        url=url,
        addresses=(LOCAL_NAME, *ips),
        network=wifi.ssid,
    )


def wifi_value(*, wifi: WifiStatus) -> str:
    """
    Describe the Wi-Fi link.

    Parameters
    ----------
    wifi : WifiStatus
        The link.

    Returns
    -------
    str
        Network, strength and IP, those known; a dash with no network.
    """
    if wifi.ssid is None:
        return UNKNOWN
    level = None if wifi.dbm is None else strength(dbm=wifi.dbm)
    parts = (wifi.ssid, level, wifi.ip)
    return DOT.join(part for part in parts if part)


def software_value(*, facts: DeviceFacts) -> str:
    """
    Describe the release and how long it has run.

    Parameters
    ----------
    facts : DeviceFacts
        The latest probe.

    Returns
    -------
    str
        The version, and the uptime in hours or minutes when known.
    """
    if facts.uptime_s is None:
        return facts.version
    hours, minutes = int(facts.uptime_s // 3600), int(facts.uptime_s // 60)
    up = f'up {hours} h' if hours else f'up {minutes} min'
    return f'{facts.version}{DOT}{up}'


def facts_values(*, facts: DeviceFacts | None) -> tuple[str, str, str]:
    """
    Describe what the probe found.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    tuple[str, str, str]
        Wi-Fi, clock and software; dashes when unknown.
    """
    if facts is None:
        return UNKNOWN, UNKNOWN, UNKNOWN
    synced = {True: 'synced', False: 'not synced', None: UNKNOWN}
    return (
        wifi_value(wifi=facts.wifi),
        synced[facts.clock_synced],
        software_value(facts=facts),
    )


def device_view(
    *,
    device: DeviceState,
    app_rows: Sequence[InfoRow],
    zone: tzinfo,
) -> DeviceView:
    """
    Build the Device screen.

    Parameters
    ----------
    device : DeviceState
        Device state.
    app_rows : Sequence[InfoRow]
        The app's own lines, from `App.device_rows`.
    zone : tzinfo
        Time zone of the header's clock.

    Returns
    -------
    DeviceView
        The clock and the lines: Wi-Fi, clock, the app's lines, software
        and the last error.
    """
    facts = device.panel.facts
    wifi, clock, software = facts_values(facts=facts)
    error = UNKNOWN if facts is None else (facts.last_error or 'none')
    return DeviceView(
        clock=clock_text(moment=device.now, zone=zone),
        rows=(
            InfoRow(label='Wi-Fi', value=wifi),
            InfoRow(label='Clock', value=clock),
            *app_rows,
            InfoRow(label='Software', value=software),
            InfoRow(label='Last error', value=error),
        ),
    )


def panel_view[S](
    *, device: DeviceState, app: App[S], state: S, zone: tzinfo,
) -> PanelView | None:
    """
    Build the panel screen shown over the live view, if any.

    Parameters
    ----------
    device : DeviceState
        Device state.
    app : App[S]
        The app.
    state : S
        The app's state.
    zone : tzinfo
        Time zone of the header's clock.

    Returns
    -------
    PanelView | None
        The confirm, else Wi-Fi setup, else the open menu's screen;
        None with none of them.
    """
    panel = device.panel
    if panel.confirm is not None:
        return confirm_view(confirm=panel.confirm, copy=app.confirm_copy)
    if panel.wifi is not None:
        return wifi_setup_view(device=device, zone=zone)
    if panel.menu is None:
        return None
    match panel.menu.screen:
        case PanelScreen.DEVICE:
            rows = app.device_rows(state=state, device=device)
            return device_view(device=device, app_rows=rows, zone=zone)
        case PanelScreen.PHONE:
            return phone_view(device=device, zone=zone)
    listed = menu_rows(app_rows=app.menu_rows)
    return menu_view(device=device, rows=listed, zone=zone)
