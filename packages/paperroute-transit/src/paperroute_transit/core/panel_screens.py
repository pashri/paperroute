"""Build the panel's settings screens from the state."""

from dataclasses import dataclass
from datetime import tzinfo
from typing import Final

from paperroute_device.core.device_model import (
    ConfirmAction,
    DeviceFacts,
    MenuRow,
    PanelScreen,
    WifiStatus,
)
from paperroute_device.core.panel_views import (
    LOCAL_NAME,
    MENU_VISIBLE,
    UNKNOWN,
    ConfirmCopy,
    DeviceView,
    InfoRow,
    MenuView,
    PanelView,
    PhoneView,
    confirm_view,
    strength,
)
from paperroute_device.core.text import DOT, clock_text, day_text, local_time
from paperroute_transit.core.quota import calls_today
from paperroute_transit.core.rebuild import TRANSIT_ROWS
from paperroute_transit.core.state_model import State
from paperroute_transit.core.wifi import wifi_setup_view

TRANSIT_CONFIRM_COPY: Final[ConfirmCopy] = {
    ConfirmAction.RESTART: (
        'paperroute starts again. Your pins and settings stay.'
    ),
    ConfirmAction.SHUT_DOWN: (
        "paperroute switches off. The screen says when it's safe"
        ' to unplug.'
    ),
    ConfirmAction.RESET_SETTINGS: (
        'Clears pins, settings, Wi-Fi and the key.'
        ' The timetable and map stay.'
    ),
    ConfirmAction.ERASE: (
        'Clears pins, settings, Wi-Fi, the key and the timetable.'
        ' The map stays.'
    ),
    ConfirmAction.JOIN_WIFI: (
        'paperroute leaves its Wi-Fi while you set up the new one.'
    ),
}


@dataclass(frozen=True)
class TilesFacts:
    """
    What the app's probe found about the map tiles.

    Attributes
    ----------
    built : str | None
        When the tiles were built, or None with no tiles.
    """

    built: str | None


def menu_view(*, state: State) -> MenuView:
    """
    Build the Settings menu.

    Parameters
    ----------
    state : State
        State, with the menu open.

    Returns
    -------
    MenuView
        Every row, the cursor, and the top row scrolled to keep the
        cursor in sight.
    """
    cursor = state.panel.menu.cursor if state.panel.menu else 0
    return MenuView(
        clock=clock_text(moment=state.now, zone=state.city.zone),
        rows=tuple(
            row.value if isinstance(row, MenuRow) else row.label
            for row in TRANSIT_ROWS
        ),
        cursor=cursor,
        first=max(0, cursor - MENU_VISIBLE + 1),
    )


def phone_view(*, state: State) -> PhoneView:
    """
    Build the Open on your phone screen.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    PhoneView
        The code opens the IP when known, as phones often can't find
        ``paperroute.local``; that name is always listed.
    """
    facts = state.panel.facts
    wifi = facts.wifi if facts else WifiStatus()
    url = f'http://{wifi.ip}/' if wifi.ip else f'http://{LOCAL_NAME}/'
    ips = (f'http://{wifi.ip}',) if wifi.ip else ()
    return PhoneView(
        clock=clock_text(moment=state.now, zone=state.city.zone),
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


def timetable_value(*, state: State) -> str:
    """
    Describe the timetable in use.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str
        'rebuilding' while a rebuild runs; else its day, build time and
        schema, after 'last build failed' when the last rebuild failed;
        a dash, or 'last build failed' alone, when unknown.
    """
    if state.rebuild.running:
        return 'rebuilding'
    parts = ['last build failed'] if state.rebuild.failed else []
    built = local_time(stamp=state.saving.timetable, zone=state.city.zone)
    if built is None:
        return DOT.join(parts) or UNKNOWN
    clock = clock_text(moment=built, zone=state.city.zone)
    parts += [day_text(moment=built), f'built {clock}']
    if state.rebuild.schema is not None:
        parts.append(f'version {state.rebuild.schema}')
    return DOT.join(parts)


def live_value(*, state: State) -> str:
    """
    Describe the live data.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str
        The last fetch's age, when there was one, and the calls today.
    """
    calls = calls_today(quota=state.quota, day=state.now.date())
    counted = f'{calls:,} calls today'
    if state.last_fetch is None:
        return counted
    age = int((state.now - state.last_fetch).total_seconds())
    return f'{age} s old{DOT}{counted}'


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


def facts_values(
    *, facts: DeviceFacts | None, zone: tzinfo,
) -> tuple[str, str, str, str]:
    """
    Describe what the probe found.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.
    zone : tzinfo
        Local time zone the tiles' build day is given in.

    Returns
    -------
    tuple[str, str, str, str]
        Wi-Fi, clock, map tiles and software; dashes when unknown.
    """
    if facts is None:
        return UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN
    synced = {True: 'synced', False: 'not synced', None: UNKNOWN}
    app = facts.app
    stamp = app.built if isinstance(app, TilesFacts) else None
    tiles = local_time(stamp=stamp, zone=zone)
    return (
        wifi_value(wifi=facts.wifi),
        synced[facts.clock_synced],
        UNKNOWN if tiles is None else f'built {day_text(moment=tiles)}',
        software_value(facts=facts),
    )


def device_rows(*, state: State) -> tuple[InfoRow, ...]:
    """
    Gather the Device screen's lines.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[InfoRow, ...]
        Wi-Fi, clock, timetable, map tiles, live data, software and the
        last error.
    """
    facts = state.panel.facts
    wifi, clock, tiles, software = facts_values(
        facts=facts, zone=state.city.zone,
    )
    error = UNKNOWN if facts is None else (facts.last_error or 'none')
    return (
        InfoRow(label='Wi-Fi', value=wifi),
        InfoRow(label='Clock', value=clock),
        InfoRow(label='Timetable', value=timetable_value(state=state)),
        InfoRow(label='Map tiles', value=tiles),
        InfoRow(label='Live data', value=live_value(state=state)),
        InfoRow(label='Software', value=software),
        InfoRow(label='Last error', value=error),
    )


def device_view(*, state: State) -> DeviceView:
    """
    Build the Device screen.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    DeviceView
        The clock and the lines.
    """
    return DeviceView(
        clock=clock_text(moment=state.now, zone=state.city.zone),
        rows=device_rows(state=state),
    )


def panel_view(*, state: State) -> PanelView | None:
    """
    Build the panel screen shown over the Live view, if any.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    PanelView | None
        The confirm, else Wi-Fi setup, else the open menu's screen;
        None with none of them.
    """
    if state.panel.confirm is not None:
        return confirm_view(
            confirm=state.panel.confirm, copy=TRANSIT_CONFIRM_COPY,
        )
    if state.panel.wifi is not None:
        return wifi_setup_view(state=state)
    menu = state.panel.menu
    if menu is None:
        return None
    match menu.screen:
        case PanelScreen.DEVICE:
            return device_view(state=state)
        case PanelScreen.PHONE:
            return phone_view(state=state)
    return menu_view(state=state)
