"""Build the panel's settings screens from the state."""

from datetime import datetime
from typing import Final

from paperroute.core.device_model import (
    MENU_ROWS,
    DeviceFacts,
    PanelScreen,
    WifiStatus,
)
from paperroute.core.model import SYDNEY
from paperroute.core.panel_views import (
    MENU_VISIBLE,
    DeviceView,
    InfoRow,
    MenuView,
    PanelView,
    PhoneView,
    confirm_view,
)
from paperroute.core.quota import calls_today
from paperroute.core.state_model import State
from paperroute.core.stop_view import DOT, clock_text

LOCAL_NAME: Final[str] = 'paperroute.local'
UNKNOWN: Final[str] = '-'
STRONG_DBM: Final[int] = -60
GOOD_DBM: Final[int] = -70


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
        clock=clock_text(moment=state.now),
        rows=tuple(row.value for row in MENU_ROWS),
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
        clock=clock_text(moment=state.now),
        url=url,
        addresses=(LOCAL_NAME, *ips),
        network=wifi.ssid,
    )


def strength(*, dbm: int) -> str:
    """
    Name a signal level.

    Parameters
    ----------
    dbm : int
        Signal level in dBm.

    Returns
    -------
    str
        'strong', 'good' or 'weak'.
    """
    if dbm >= STRONG_DBM:
        return 'strong'
    return 'good' if dbm >= GOOD_DBM else 'weak'


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


def local_time(*, stamp: str | None) -> datetime | None:
    """
    Read an ISO time in Sydney.

    Parameters
    ----------
    stamp : str | None
        An ISO 8601 time, or None.

    Returns
    -------
    datetime | None
        The time in Sydney; None when missing or unreadable.
    """
    if stamp is None:
        return None
    try:
        return datetime.fromisoformat(stamp).astimezone(SYDNEY)
    except ValueError:
        return None


def day_text(*, moment: datetime) -> str:
    """
    Give a day as '21 Oct'.

    Parameters
    ----------
    moment : datetime
        The time.

    Returns
    -------
    str
        Day of the month and short month name.
    """
    return f'{moment.day} {moment:%b}'


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
    built = local_time(stamp=state.saving.timetable)
    if built is None:
        return DOT.join(parts) or UNKNOWN
    parts += [day_text(moment=built), f'built {clock_text(moment=built)}']
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


def facts_values(*, facts: DeviceFacts | None) -> tuple[str, str, str, str]:
    """
    Describe what the probe found.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    tuple[str, str, str, str]
        Wi-Fi, clock, map tiles and software; dashes when unknown.
    """
    if facts is None:
        return UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN
    synced = {True: 'synced', False: 'not synced', None: UNKNOWN}
    tiles = local_time(stamp=facts.tiles_built)
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
    wifi, clock, tiles, software = facts_values(facts=facts)
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
        clock=clock_text(moment=state.now), rows=device_rows(state=state),
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
        The confirm, else the open menu's screen; None with neither.
    """
    if state.panel.confirm is not None:
        return confirm_view(confirm=state.panel.confirm)
    menu = state.panel.menu
    if menu is None:
        return None
    match menu.screen:
        case PanelScreen.DEVICE:
            return device_view(state=state)
        case PanelScreen.PHONE:
            return phone_view(state=state)
    return menu_view(state=state)
