"""Draw the Device page's facts and news."""

import html
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from paperroute_device.core.device_model import (
    POWER_ACTIONS,
    ConfirmAction,
    DeviceFacts,
    InstalledApp,
    PowerAction,
)
from paperroute_device.core.panel_views import (
    CANCEL_LINE,
    CONFIRM_WORDS,
    LOCAL_NAME,
    UNKNOWN,
)
from paperroute_device.core.panel_views import strength as dbm_strength
from paperroute_device.core.text import DOT, HINT_DOT
from paperroute_hardware.ports import PanelTimes

POWER_STATUS: Final[dict[PowerAction, str]] = {
    PowerAction.OFF: 'Shutting down...',
    PowerAction.RESTART: 'Restarting...',
}
# The asks name the same buttons the panel's confirm screen draws.
ASKED: Final[dict[str, str]] = {
    action.value: (
        'Confirm on the device. Bottom: press to '
        f'{CONFIRM_WORDS[action][1]}{HINT_DOT}{CANCEL_LINE}'
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
GB: Final[int] = 1_000_000_000  # bytes
MB: Final[int] = 1_000_000  # bytes
DEGREES_C: Final[str] = '\u00b0C'
ROW: Final[str] = """        <div>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>"""
SWITCH_QUESTION: Final[str] = 'Switch this panel to {name}?'
SWITCH_KEPT: Final[str] = 'Everything saved for {name} is kept.'
SWITCHING: Final[str] = (
    'Switching to {name}... The panel restarts in a few seconds.'
)
NOT_SWITCHED: Final[str] = (
    'Not switched. The panel is installing or getting an app ready. '
    'Try again when it has finished.'
)
APP_SECTION: Final[str] = (
    '<section class="card setting" aria-labelledby="app">\n'
    '      <h2 id="app">App</h2>\n'
    '      <form method="post" action="/device/switch" data-switch>\n'
    '        <fieldset class="apps">\n'
    '          <legend>Installed on this panel</legend>\n'
    '{labels}\n'
    '        </fieldset>{button}\n'
    '      </form>\n'
    '      <p id="switch-status" class="hint" aria-live="polite">{status}'
    '</p>{dialogs}\n'
    '    </section>'
)
APP_LABEL: Final[str] = (
    '          <label>\n'
    '            <input type="radio" name="app" value="{key}"{choice}>\n'
    '            <span>{name}{status}</span>{now}\n'
    '          </label>'
)
SHOWING: Final[str] = '\n            <strong class="now">Showing</strong>'
SWITCH_BUTTON: Final[str] = '\n        <button type="submit">Switch</button>'
SWITCH_DIALOG: Final[str] = (
    '\n      <dialog id="switch-{key}-dialog" data-switch="{key}" '
    'aria-labelledby="switch-{key}-question">\n'
    '        <form method="dialog">\n'
    '          <p id="switch-{key}-question">{question}</p>{lines}\n'
    '          <div class="buttons">\n'
    '            <button value="cancel" class="secondary" autofocus>'
    'Cancel</button>\n'
    '            <button value="confirm">Switch</button>\n'
    '          </div>\n'
    '        </form>\n'
    '      </dialog>'
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


def key_text(*, present: bool, last_used: float | None, now: float) -> str:
    """
    Describe the API key without showing it.

    Parameters
    ----------
    present : bool
        Whether a key is set.
    last_used : float | None
        Wall-clock time the key last worked, or None.
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


def paragraphs(*, lines: Iterable[str], indent: str) -> str:
    """
    Draw lines of text as paragraphs, each on a line of its own.

    Parameters
    ----------
    lines : Iterable[str]
        The lines; escaped here.
    indent : str
        The space before each paragraph.

    Returns
    -------
    str
        A newline, the indent and a paragraph for each line; empty with
        no lines.
    """
    return ''.join(f'\n{indent}<p>{html.escape(line)}</p>' for line in lines)


def switch_lines(
    *, app: InstalledApp, apps: Sequence[InstalledApp],
) -> tuple[str, ...]:
    """
    Say what switching to an app takes and keeps.

    Parameters
    ----------
    app : InstalledApp
        The app switched to.
    apps : Sequence[InstalledApp]
        The installed apps.

    Returns
    -------
    tuple[str, ...]
        The app's wait, when it has one, then that the app showing keeps
        its data, when one is showing.
    """
    kept = next(
        (
            SWITCH_KEPT.format(name=other.card.name)
            for other in apps
            if other.active
        ),
        '',
    )
    return tuple(line for line in (app.card.wait, kept) if line)


def switch_text(*, app: InstalledApp, apps: Sequence[InstalledApp]) -> str:
    """
    Draw the confirm page's question, the wait and what is kept.

    Parameters
    ----------
    app : InstalledApp
        The app switched to.
    apps : Sequence[InstalledApp]
        The installed apps.

    Returns
    -------
    str
        Paragraphs, each after a newline.
    """
    return paragraphs(
        lines=(
            SWITCH_QUESTION.format(name=app.card.name),
            *switch_lines(app=app, apps=apps),
        ),
        indent=' ' * 8,
    )


def app_label(*, app: InstalledApp, checked: bool) -> str:
    """
    Draw one app's radio, name and status line.

    Parameters
    ----------
    app : InstalledApp
        The app.
    checked : bool
        Whether its radio starts chosen.

    Returns
    -------
    str
        A label; the app showing is marked and can't be chosen.
    """
    name = html.escape(app.card.name)
    if app.active:
        choice, now = ' disabled', SHOWING
    else:
        choice = f' data-name="{name}" required' + (
            ' checked' if checked else ''
        )
        now = ''
    status = html.escape(app.card.status)
    return APP_LABEL.format(
        key=html.escape(app.key),
        choice=choice,
        name=name,
        status=status and f'<small>{status}</small>',
        now=now,
    )


def switch_dialog(
    *, app: InstalledApp, apps: Sequence[InstalledApp],
) -> str:
    """
    Draw the dialog that confirms a switch to an app.

    Parameters
    ----------
    app : InstalledApp
        The app switched to.
    apps : Sequence[InstalledApp]
        The installed apps.

    Returns
    -------
    str
        The dialog, after a newline.
    """
    return SWITCH_DIALOG.format(
        key=html.escape(app.key),
        question=html.escape(SWITCH_QUESTION.format(name=app.card.name)),
        lines=paragraphs(
            lines=switch_lines(app=app, apps=apps), indent=' ' * 10,
        ),
    )


def apps_section(
    *, apps: Sequence[InstalledApp], status: str = '',
) -> str:
    """
    Draw the App section: the installed apps and the switch.

    Parameters
    ----------
    apps : Sequence[InstalledApp]
        The installed apps, in list order.
    status : str
        The switch under way, or empty; escaped here.

    Returns
    -------
    str
        The section, with a button and a dialog per app not showing when
        there is more than one app; empty with no apps.
    """
    if not apps:
        return ''
    chosen = next((app.key for app in apps if not app.active), None)
    several = len(apps) > 1
    return APP_SECTION.format(
        labels='\n'.join(
            app_label(app=app, checked=app.key == chosen) for app in apps
        ),
        button=SWITCH_BUTTON if several else '',
        status=html.escape(status),
        dialogs=''.join(
            switch_dialog(app=app, apps=apps)
            for app in apps
            if several and not app.active
        ),
    )


def switchable(
    *, apps: Sequence[InstalledApp], key: str,
) -> InstalledApp | None:
    """
    Find an installed app that is not the one showing.

    Parameters
    ----------
    apps : Sequence[InstalledApp]
        The installed apps.
    key : str
        The app.

    Returns
    -------
    InstalledApp | None
        The app, or None when it is not listed or is showing.
    """
    return next(
        (app for app in apps if app.key == key and not app.active), None,
    )


def switch_status(
    *, query: Mapping[str, str], apps: Sequence[InstalledApp],
) -> str:
    """
    Say which app a switch is under way to.

    Parameters
    ----------
    query : Mapping[str, str]
        The page's query; `switch` names the app.
    apps : Sequence[InstalledApp]
        The installed apps.

    Returns
    -------
    str
        `SWITCHING` for an app listed, else empty.
    """
    key = query.get('switch')
    app = next((app for app in apps if app.key == key), None)
    return '' if app is None else SWITCHING.format(name=app.card.name)
