"""Read and write the Settings page's form fields."""

import re
from collections.abc import Mapping
from datetime import time
from enum import StrEnum
from html import escape
from typing import Any, Final

from paperroute.core.messages import SettingsChanged
from paperroute.core.saved import (
    DEVICE_FIELDS,
    ConnectionMap,
    Lookahead,
    Rate,
    Settings,
    with_changes,
)
from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    QuietMode,
    Window,
)

SWITCHES: Final[tuple[str, ...]] = (
    'auto_rebuild',
    'rotate',
    'larger_text',
    'inverted_lists',
    'skip_school_buses',
    'skip_school_on_route_pins',
    'swap_knobs',
)
SLOTS: Final[tuple[str, ...]] = ('peak_1', 'peak_2')
CHOICES: Final[frozenset[str]] = frozenset(
    {'peak_rate', 'offpeak_rate', 'alert_lookahead'}
)
RADIOS: Final[tuple[str, ...]] = ('quiet_mode', 'connection_map')
COUNTS: Final[dict[str, int]] = {str(count): count for count in CLEAN_CHOICES}
SWITCH: Final[dict[str, bool]] = {'on': True, 'off': False}
CLOCK: Final[re.Pattern[str]] = re.compile(r'(\d\d):(\d\d)')
ERRORS: Final[dict[str, str]] = {
    'peak': (
        'Peak hours not saved: each start must be before its end, '
        'as HH:MM. Windows must not overlap.'
    ),
    'quiet': 'Quiet hours not saved: use HH:MM times.',
}


class Section(StrEnum):
    """The parts of the page whose input can be refused."""

    PEAK = 'peak'
    QUIET = 'quiet'


class UnknownValue(ValueError):
    """A field held a value the page never sends."""


class BadInput(ValueError):
    """
    A time or window the user typed was not usable.

    Parameters
    ----------
    section : str
        `peak` or `quiet`, the part of the page to show the error in.

    Attributes
    ----------
    section : str
        `peak` or `quiet`.
    """

    def __init__(self, section: str) -> None:
        super().__init__(section)
        self.section = section


def clock_text(*, moment: time) -> str:
    """
    Write a time as `HH:MM`.

    Parameters
    ----------
    moment : time
        The time.

    Returns
    -------
    str
        The time to the minute.
    """
    return f'{moment.hour:02d}:{moment.minute:02d}'


def form_values(*, settings: Settings) -> dict[str, str]:
    """
    Write the settings as the strings the page's controls hold.

    Parameters
    ----------
    settings : Settings
        The settings.

    Returns
    -------
    dict[str, str]
        `on` or `off` for each switch, the rate, mode and lookahead values,
        the cleaning count, and `HH:MM` times; an unused peak slot holds
        empty strings.
    """
    values = {
        name: 'on' if setting_of(settings=settings, name=name) else 'off'
        for name in SWITCHES
    }
    values |= {
        name: setting_of(settings=settings, name=name).value
        for name in (*CHOICES, *RADIOS)
    }
    device = settings.device
    values['clean_after'] = str(device.clean_after)
    values['quiet_start'] = clock_text(moment=device.my_hours.start)
    values['quiet_end'] = clock_text(moment=device.my_hours.end)
    return values | peak_values(settings=settings)


def setting_of(*, settings: Settings, name: str) -> Any:
    """
    Read one setting by its flat Settings page name.

    Parameters
    ----------
    settings : Settings
        The settings.
    name : str
        The field's name, on either part.

    Returns
    -------
    Any
        The field's value.
    """
    part = settings.device if name in DEVICE_FIELDS else settings.board
    return getattr(part, name)


def peak_values(*, settings: Settings) -> dict[str, str]:
    """
    Write the peak windows as the page's start and end times.

    Parameters
    ----------
    settings : Settings
        The settings.

    Returns
    -------
    dict[str, str]
        `HH:MM` start and end for each slot; empty strings for an unused
        slot.
    """
    values: dict[str, str] = {}
    for index, slot in enumerate(SLOTS):
        window = settings.board.peak_windows[index:index + 1]
        values[f'{slot}_start'] = (
            clock_text(moment=window[0].start) if window else ''
        )
        values[f'{slot}_end'] = (
            clock_text(moment=window[0].end) if window else ''
        )
    return values


def clock_of(*, text: str, section: str) -> time:
    """
    Read an `HH:MM` time.

    Parameters
    ----------
    text : str
        The text.
    section : str
        The section to blame when the text is not a time.

    Returns
    -------
    time
        The time.

    Raises
    ------
    BadInput
        If the text is not a real `HH:MM` time.
    """
    found = CLOCK.fullmatch(text)
    if found is None:
        raise BadInput(section)
    try:
        return time(int(found[1]), int(found[2]))
    except ValueError as error:
        raise BadInput(section) from error


def window_of(
    *, start: str, end: str, section: str
) -> Window | None:
    """
    Read a pair of times.

    Parameters
    ----------
    start : str
        The first time, or empty.
    end : str
        The second time, or empty.
    section : str
        The section to blame for bad input.

    Returns
    -------
    Window | None
        The window, or None when both are empty.

    Raises
    ------
    BadInput
        If only one is empty or either is not a time.
    """
    if not start and not end:
        return None
    if not start or not end:
        raise BadInput(section)
    return Window(
        start=clock_of(text=start, section=section),
        end=clock_of(text=end, section=section),
    )


def peak_windows_of(*, fields: Mapping[str, str]) -> tuple[Window, ...]:
    """
    Read the peak slots.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.

    Returns
    -------
    tuple[Window, ...]
        The windows of the filled slots.

    Raises
    ------
    BadInput
        If a slot is half empty, a start is not before its end, or
        two windows overlap.
    """
    windows = [
        window
        for slot in SLOTS
        if (
            window := window_of(
                start=fields.get(f'{slot}_start', ''),
                end=fields.get(f'{slot}_end', ''),
                section=Section.PEAK,
            )
        )
    ]
    ordered = sorted(windows)
    if any(window.start >= window.end for window in windows) or any(
        first.end > second.start
        for first, second in zip(ordered, ordered[1:])
    ):
        raise BadInput(Section.PEAK)
    return tuple(windows)


def enum_of[E: StrEnum](*, kind: type[E], text: str) -> E:
    """
    Read one of an enumeration's values.

    Parameters
    ----------
    kind : type[E]
        The enumeration.
    text : str
        The posted value.

    Returns
    -------
    E
        The member.

    Raises
    ------
    UnknownValue
        If the text is not a value of the enumeration.
    """
    try:
        return kind(text)
    except ValueError as error:
        raise UnknownValue(text) from error


def switches_of(*, fields: Mapping[str, str]) -> dict[str, bool]:
    """
    Read the switches that were posted.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.

    Returns
    -------
    dict[str, bool]
        Each posted switch by name.

    Raises
    ------
    UnknownValue
        If a switch is neither `on` nor `off`.
    """
    found = {}
    for name in SWITCHES:
        if name not in fields:
            continue
        if fields[name] not in SWITCH:
            raise UnknownValue(fields[name])
        found[name] = SWITCH[fields[name]]
    return found


def clean_after_of(*, fields: Mapping[str, str]) -> int | None:
    """
    Read the cleaning count if it was posted.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.

    Returns
    -------
    int | None
        The count, or None when the field is absent.

    Raises
    ------
    UnknownValue
        If the field is not one of the offered counts.
    """
    if 'clean_after' not in fields:
        return None
    if fields['clean_after'] not in COUNTS:
        raise UnknownValue(fields['clean_after'])
    return COUNTS[fields['clean_after']]


def changed_from(*, fields: Mapping[str, str]) -> SettingsChanged:
    """
    Turn posted fields into a settings change.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields; only those present are read.

    Returns
    -------
    SettingsChanged
        The change; settings with no field are left as they are.

    Raises
    ------
    UnknownValue
        If a switch, rate, mode, lookahead or cleaning count holds an
        unknown value.
    BadInput
        If a time is not `HH:MM` or a peak window is unusable.
    """
    switches = switches_of(fields=fields)
    return SettingsChanged(
        auto_rebuild=switches.get('auto_rebuild'),
        peak_rate=optional_enum(kind=Rate, fields=fields, name='peak_rate'),
        offpeak_rate=optional_enum(
            kind=Rate, fields=fields, name='offpeak_rate',
        ),
        peak_windows=(
            peak_windows_of(fields=fields)
            if any(f'{slot}_start' in fields for slot in SLOTS)
            else None
        ),
        quiet_mode=optional_enum(
            kind=QuietMode, fields=fields, name='quiet_mode',
        ),
        my_hours=window_of(
            start=fields.get('quiet_start', ''),
            end=fields.get('quiet_end', ''),
            section=Section.QUIET,
        ),
        rotate=switches.get('rotate'),
        larger_text=switches.get('larger_text'),
        inverted_lists=switches.get('inverted_lists'),
        alert_lookahead=optional_enum(
            kind=Lookahead, fields=fields, name='alert_lookahead',
        ),
        connection_map=optional_enum(
            kind=ConnectionMap, fields=fields, name='connection_map',
        ),
        skip_school_buses=switches.get('skip_school_buses'),
        skip_school_on_route_pins=switches.get('skip_school_on_route_pins'),
        clean_after=clean_after_of(fields=fields),
        swap_knobs=switches.get('swap_knobs'),
    )


def optional_enum[E: StrEnum](
    *, kind: type[E], fields: Mapping[str, str], name: str
) -> E | None:
    """
    Read an enumeration field if it was posted.

    Parameters
    ----------
    kind : type[E]
        The enumeration.
    fields : Mapping[str, str]
        The posted fields.
    name : str
        The field.

    Returns
    -------
    E | None
        The member, or None when the field is absent.

    Raises
    ------
    UnknownValue
        If the field holds an unknown value.
    """
    if name not in fields:
        return None
    return enum_of(kind=kind, text=fields[name])


def posted_values(
    *, fields: Mapping[str, str], event: SettingsChanged, defaults: Settings,
) -> dict[str, str]:
    """
    Write the posted fields the way the app will report them.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.
    event : SettingsChanged
        The change read from them.
    defaults : Settings
        The settings the change is applied to.

    Returns
    -------
    dict[str, str]
        The form values of the posted fields; a posted peak slot or
        quiet pair is written for every slot, as the app keeps them.
    """
    given = {k: v for k, v in vars(event).items() if v is not None}
    full = form_values(
        settings=with_changes(settings=defaults, changes=given),
    )
    keys = set(fields)
    if event.peak_windows is not None:
        keys |= {f'{slot}_{end}' for slot in SLOTS for end in ('start', 'end')}
    return {key: full[key] for key in keys if key in full}


def page_text(
    *, template: str, values: Mapping[str, str], error: str | None
) -> str:
    """
    Fill the Settings page's marks.

    Parameters
    ----------
    template : str
        The page with `{{field}}` marks. A switch mark becomes
        ` checked` when on; `{{field:choice}}` becomes ` checked` (a
        radio) or ` selected` (a list) when the field holds that choice;
        any other becomes the value.
        `{{error_peak}}` and `{{error_quiet}}` become a message.
    values : Mapping[str, str]
        The form values.
    error : str | None
        The section to show an error for, if any.

    Returns
    -------
    str
        The page.
    """
    def fill(found: re.Match[str]) -> str:
        """
        Give the text for one mark.

        Parameters
        ----------
        found : re.Match[str]
            The mark's match.

        Returns
        -------
        str
            What replaces the mark.
        """
        name, _, choice = found[1].partition(':')
        if name.startswith('error_'):
            section = name.removeprefix('error_')
            return error_line(shown=error == section, section=section)
        value = values.get(name, '')
        if choice:
            mark = ' checked' if name in RADIOS else ' selected'
            return mark if value == choice else ''
        if name in SWITCHES:
            return ' checked' if value == 'on' else ''
        return escape(value)

    return re.sub(r'\{\{([a-z0-9_:]+)\}\}', fill, template)


def error_line(*, shown: bool, section: str) -> str:
    """
    Write a section's error paragraph.

    Parameters
    ----------
    shown : bool
        Whether the section has an error.
    section : str
        `peak` or `quiet`.

    Returns
    -------
    str
        The paragraph, or an empty string.
    """
    if not shown:
        return ''
    return f'<p class="error" role="alert">{ERRORS[section]}</p>'
