"""Draw the Device page's facts and news."""

import html
from collections.abc import Mapping
from datetime import datetime
from typing import Final

from paperroute.core.device_model import (
    ConfirmAction,
    DeviceFacts,
    DeviceStatus,
)
from paperroute.core.feeds import feed_label
from paperroute.core.messages import POWER_ACTIONS, PowerAction
from paperroute.core.model import SYDNEY
from paperroute.core.panel_screens import (
    LOCAL_NAME,
    UNKNOWN,
    day_text,
    local_time,
)
from paperroute.core.panel_screens import strength as dbm_strength
from paperroute.core.panel_views import CANCEL_LINE, CONFIRM_COPY
from paperroute.core.quota import DAILY_LIMIT
from paperroute.core.stop_view import DOT, HINT_DOT, clock_text
from paperroute.observers import PanelTimes

POWER_STATUS: Final[dict[PowerAction, str]] = {
    PowerAction.OFF: 'Shutting down...',
    PowerAction.RESTART: 'Restarting...',
}
# The asks name the same buttons the panel's confirm screen draws.
ASKED: Final[dict[str, str]] = {
    action.value: (
        'Confirm on the device. Bottom: press to '
        f'{CONFIRM_COPY[action][2]}{HINT_DOT}{CANCEL_LINE}'
    )
    for action in (
        ConfirmAction.JOIN_WIFI,
        ConfirmAction.RESET_SETTINGS,
        ConfirmAction.ERASE,
    )
}
RESETS: Final[dict[str, ConfirmAction]] = {
    'settings': ConfirmAction.RESET_SETTINGS,
    'erase': ConfirmAction.ERASE,
}
KEY_SAVED: Final[str] = 'Key saved.'
KEY_REFUSED: Final[str] = (
    "Not saved: that doesn't look like a TfNSW API key."
)
GB: Final[int] = 1_000_000_000  # bytes
MB: Final[int] = 1_000_000  # bytes
DEGREES_C: Final[str] = '\u00b0C'
ROW: Final[str] = """        <div>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>"""
FEED_ROW: Final[str] = (
    '            <tr><th scope="row">{label}</th>'
    '<td>{last}</td><td>{age}</td></tr>'
)
NO_FEEDS: Final[str] = (
    '            <tr><td colspan="3">none yet</td></tr>'
)


def notice(*, text: str, alert: bool = False) -> str:
    """
    Draw a line of news for the top of the page.

    Parameters
    ----------
    text : str
        The words; escaped here.
    alert : bool
        Whether it reports a refusal, so screen readers say it at once.

    Returns
    -------
    str
        A paragraph.
    """
    role = ' role="alert"' if alert else ''
    return f'<p class="notice"{role}>{html.escape(text)}</p>'


def row(*, label: str, value: str) -> str:
    """
    Draw one labelled fact.

    Parameters
    ----------
    label : str
        The label; markup as given.
    value : str
        The value; escaped here.

    Returns
    -------
    str
        A `dt` and `dd` pair in a `div`.
    """
    return ROW.format(label=label, value=html.escape(value))


def wifi_text(*, facts: DeviceFacts | None) -> str:
    """
    Describe the Wi-Fi link.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        The network, its strength and level; `not connected` with no
        network; a dash before the first probe.
    """
    if facts is None:
        return UNKNOWN
    wifi = facts.wifi
    if wifi.ssid is None:
        return 'not connected'
    if wifi.dbm is None:
        return wifi.ssid
    level = dbm_strength(dbm=wifi.dbm)
    return f'{wifi.ssid}{DOT}{level} ({wifi.dbm} dBm)'


def address_text(*, facts: DeviceFacts | None) -> str:
    """
    Give the device's addresses.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        The IP address when known, then the local name.
    """
    ip = None if facts is None else facts.wifi.ip
    return f'{ip}{DOT}{LOCAL_NAME}' if ip else LOCAL_NAME


def clock_value(*, facts: DeviceFacts | None) -> str:
    """
    Say whether the clock is set from the network.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        `Synced`, `Not synced`, or `Unknown` when there is no probe or it
        couldn't tell.
    """
    if facts is None or facts.clock_synced is None:
        return 'Unknown'
    return 'Synced' if facts.clock_synced else 'Not synced'


def timetable_text(*, status: DeviceStatus) -> str:
    """
    Describe the timetable in use.

    Parameters
    ----------
    status : DeviceStatus
        The device status.

    Returns
    -------
    str
        `rebuilding` while a rebuild runs; else its version and build
        time, then `ok` or `last build failed`; with no timetable,
        `none yet` or `last build failed`.
    """
    failed = 'last build failed'
    if status.rebuilding:
        return 'rebuilding'
    built = local_time(stamp=status.timetable)
    if built is None:
        return failed if status.rebuild_failed else 'none yet'
    parts = [] if status.schema is None else [f'version {status.schema}']
    parts.append(f'built {day_text(moment=built)} {clock_text(moment=built)}')
    parts.append(failed if status.rebuild_failed else 'ok')
    return DOT.join(parts)


def tiles_text(*, facts: DeviceFacts | None) -> str:
    """
    Say when the map tiles were built.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        `built 19 Oct`, `none`, or a dash before the first probe.
    """
    if facts is None:
        return UNKNOWN
    built = local_time(stamp=facts.tiles_built)
    return 'none' if built is None else f'built {day_text(moment=built)}'


def uptime_text(*, seconds: float | None) -> str:
    """
    Give how long the device has been up.

    Parameters
    ----------
    seconds : float | None
        Seconds since boot, or None.

    Returns
    -------
    str
        Hours and minutes, minutes alone under an hour, or a dash.
    """
    if seconds is None:
        return UNKNOWN
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f'{hours} h {minutes} min' if hours else f'{minutes} min'



def ago_text(*, seconds: float) -> str:
    """
    Write an age in whole seconds, minutes or hours.

    Parameters
    ----------
    seconds : float
        Seconds since; a negative age, from a clock step, counts as 0.

    Returns
    -------
    str
        For example ``12 s``, ``4 min`` or ``3 h``.
    """
    whole = max(0, int(seconds))
    if whole < 60:
        return f'{whole} s'
    if whole < 3_600:
        return f'{whole // 60} min'
    return f'{whole // 3_600} h'


def free_text(*, facts: DeviceFacts | None) -> str:
    """
    Give the free disk and memory.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        Whole gigabytes of disk and megabytes of memory, those known;
        a dash with neither.
    """
    if facts is None:
        return UNKNOWN
    parts = []
    if facts.disk_free is not None:
        parts.append(f'{facts.disk_free // GB} GB')
    if facts.mem_free is not None:
        parts.append(f'memory {facts.mem_free // MB} MB')
    return DOT.join(parts) or UNKNOWN


def network_rows(*, facts: DeviceFacts | None) -> str:
    """
    Draw the Network section's facts.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        Wi-Fi, address and clock.
    """
    return '\n'.join((
        row(label='Wi-Fi', value=wifi_text(facts=facts)),
        row(label='Address', value=address_text(facts=facts)),
        row(label='Clock', value=clock_value(facts=facts)),
    ))


def data_rows(*, status: DeviceStatus | None) -> str:
    """
    Draw the Data section's facts.

    Parameters
    ----------
    status : DeviceStatus | None
        The device status, or None before the app reports it.

    Returns
    -------
    str
        Timetable, map tiles and the API calls made today.
    """
    if status is None:
        timetable, calls = UNKNOWN, UNKNOWN
    else:
        timetable = timetable_text(status=status)
        calls = f'{status.calls_today:,} of {DAILY_LIMIT:,}'
    facts = None if status is None else status.facts
    return '\n'.join((
        row(label='Timetable', value=timetable),
        row(label='Map tiles', value=tiles_text(facts=facts)),
        row(label='API calls today', value=calls),
    ))


def system_rows(*, facts: DeviceFacts | None) -> str:
    """
    Draw the System section's facts.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.

    Returns
    -------
    str
        Software, uptime, CPU temperature, free space and last error;
        dashes before the first probe.
    """
    cpu = None if facts is None else facts.cpu_c
    return '\n'.join((
        row(label='Software', value=facts.version if facts else UNKNOWN),
        row(
            label='Up',
            value=uptime_text(seconds=facts.uptime_s if facts else None),
        ),
        row(
            label='CPU',
            value=UNKNOWN if cpu is None else f'{round(cpu)} {DEGREES_C}',
        ),
        row(label='Free disk', value=free_text(facts=facts)),
        row(
            label='Last error',
            value=(facts.last_error or 'none') if facts else UNKNOWN,
        ),
    ))


def panel_text(*, panel: PanelTimes) -> str:
    """
    Say how the panel's refreshes are going.

    Parameters
    ----------
    panel : PanelTimes
        The panel's times.

    Returns
    -------
    str
        The partial and full refresh times that are known, a note that
        none is yet, or that the panel is not answering.
    """
    if not panel.ok:
        return 'not answering'
    parts = [
        f'{name} {seconds:.2f} s'
        for name, seconds in (
            ('partial', panel.partial_s),
            ('full', panel.full_s),
        )
        if seconds is not None
    ]
    return ', '.join(parts) or 'waiting for first refresh'


def panel_rows(*, panel: PanelTimes | None) -> str:
    """
    Draw the Panel fact.

    Parameters
    ----------
    panel : PanelTimes | None
        The panel's times, or None when there is no panel.

    Returns
    -------
    str
        One row; empty when there is no panel.
    """
    if panel is None:
        return ''
    return row(label='Panel', value=panel_text(panel=panel))


def knob_rows(*, knobs: bool | None) -> str:
    """
    Draw the Knobs fact.

    Parameters
    ----------
    knobs : bool | None
        Whether the knobs' lines opened, or None when not asked for.

    Returns
    -------
    str
        One row; empty when the knobs were not asked for.
    """
    if knobs is None:
        return ''
    return row(label='Knobs', value='on' if knobs else 'not answering')


def feed_rows(*, feeds: Mapping[str, float], now: float) -> str:
    """
    Draw the feed table's body.

    Parameters
    ----------
    feeds : Mapping[str, float]
        Wall-clock time each feed last worked.
    now : float
        The wall-clock time now.

    Returns
    -------
    str
        One row per feed, by name, with its time in Sydney and its
        age; one `none yet` row with no feeds.
    """
    rows = sorted(
        (feed_label(feed=feed), last)
        for feed, last in feeds.items()
    )
    if not rows:
        return NO_FEEDS
    return '\n'.join(
        FEED_ROW.format(
            label=html.escape(label),
            last=f'{datetime.fromtimestamp(last, tz=SYDNEY):%H:%M:%S}',
            age=ago_text(seconds=now - last),
        )
        for label, last in rows
    )


def key_text(*, present: bool, last_used: float | None, now: float) -> str:
    """
    Describe the API key without showing it.

    Parameters
    ----------
    present : bool
        Whether a key is set.
    last_used : float | None
        Wall-clock time a feed last worked with it, or None.
    now : float
        The wall-clock time now.

    Returns
    -------
    str
        Whether a key is set and how long since it was last used.
    """
    if not present:
        return 'No key set'
    if last_used is None:
        return f'Key set{DOT}not used yet'
    return f'Key set{DOT}last used {ago_text(seconds=now - last_used)} ago'


def device_notice(*, query: Mapping[str, str]) -> str:
    """
    Draw the news a redirect back to the Device page carries.

    Parameters
    ----------
    query : Mapping[str, str]
        The page's query: `asked` or `key`.

    Returns
    -------
    str
        A notice, or empty.
    """
    asked = ASKED.get(query.get('asked', ''))
    if asked is not None:
        return notice(text=asked)
    return notice(text=KEY_SAVED) if query.get('key') == 'saved' else ''


def power_status(*, query: Mapping[str, str]) -> str:
    """
    Say which power action was just sent.

    Parameters
    ----------
    query : Mapping[str, str]
        The page's query; `power` names the action.

    Returns
    -------
    str
        The action under way, or empty.
    """
    action = POWER_ACTIONS.get(query.get('power', ''))
    return '' if action is None else POWER_STATUS[action]
