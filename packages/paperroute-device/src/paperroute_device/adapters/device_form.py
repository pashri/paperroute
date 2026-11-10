"""Read and write the Settings page's device fields."""

# A copy of the board app's form half, until it uses this one.
# pylint: disable=duplicate-code

import re
from collections.abc import Collection, Mapping
from datetime import time
from enum import StrEnum
from html import escape
from typing import Final

from paperroute_device.core.device_settings import (
    CLEAN_CHOICES,
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.kit_events import DeviceChange

DEVICE_SWITCHES: Final[tuple[str, ...]] = (
    'rotate', 'larger_text', 'inverted_lists', 'swap_knobs',
)
DEVICE_RADIOS: Final[tuple[str, ...]] = ('quiet_mode',)
COUNTS: Final[dict[str, int]] = {str(count): count for count in CLEAN_CHOICES}
SWITCH: Final[dict[str, bool]] = {'on': True, 'off': False}
CLOCK: Final[re.Pattern[str]] = re.compile(r'(\d\d):(\d\d)')
QUIET: Final[str] = 'quiet'
ERRORS: Final[dict[str, str]] = {
    QUIET: 'Quiet hours not saved: use HH:MM times.',
}


class UnknownValue(ValueError):
    """A field held a value the page never sends."""


class BadInput(ValueError):
    """
    A time or window the user typed was not usable.

    Parameters
    ----------
    section : str
        The part of the page to show the error in.

    Attributes
    ----------
    section : str
        The part of the page to show the error in.
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


def device_values(*, settings: DeviceSettings) -> dict[str, str]:
    """
    Write the device settings as the strings the page's controls hold.

    Parameters
    ----------
    settings : DeviceSettings
        The device settings.

    Returns
    -------
    dict[str, str]
        `on` or `off` for each switch, the quiet mode, the cleaning count
        and the quiet hours as `HH:MM`.
    """
    values = {
        name: 'on' if getattr(settings, name) else 'off'
        for name in DEVICE_SWITCHES
    }
    return values | {
        'quiet_mode': settings.quiet_mode.value,
        'clean_after': str(settings.clean_after),
        'quiet_start': clock_text(moment=settings.my_hours.start),
        'quiet_end': clock_text(moment=settings.my_hours.end),
    }


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


def window_of(*, start: str, end: str, section: str) -> Window | None:
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


def optional_enum[E: StrEnum](
    *, kind: type[E], fields: Mapping[str, str], name: str,
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


def switches_of(
    *, fields: Mapping[str, str], names: Collection[str],
) -> dict[str, bool]:
    """
    Read the switches that were posted.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.
    names : Collection[str]
        The switches to read.

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
    for name in names:
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


def quiet_mode_of(
    *, fields: Mapping[str, str], quiet_label: str | None,
) -> QuietMode | None:
    """
    Read the quiet mode if it was posted.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.
    quiet_label : str | None
        The label of the app's quiet mode, or None when it has none.

    Returns
    -------
    QuietMode | None
        The mode, or None when the field is absent.

    Raises
    ------
    UnknownValue
        If the field holds an unknown mode, or `app` for an app with no
        quiet mode.
    """
    mode = optional_enum(kind=QuietMode, fields=fields, name='quiet_mode')
    if mode is QuietMode.APP and quiet_label is None:
        raise UnknownValue(mode.value)
    return mode


def device_change_from(
    *, fields: Mapping[str, str], quiet_label: str | None,
) -> DeviceChange:
    """
    Turn posted fields into a change to the device settings.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields; only the device's that are present are read.
    quiet_label : str | None
        The label of the app's quiet mode, or None when it has none.

    Returns
    -------
    DeviceChange
        The change; settings with no field are left as they are.

    Raises
    ------
    UnknownValue
        If a switch, the quiet mode or the cleaning count holds a value
        the page never sends.
    BadInput
        If a quiet hours time is not `HH:MM`.
    """
    switches = switches_of(fields=fields, names=DEVICE_SWITCHES)
    return DeviceChange(
        quiet_mode=quiet_mode_of(fields=fields, quiet_label=quiet_label),
        my_hours=window_of(
            start=fields.get('quiet_start', ''),
            end=fields.get('quiet_end', ''),
            section=QUIET,
        ),
        rotate=switches.get('rotate'),
        larger_text=switches.get('larger_text'),
        inverted_lists=switches.get('inverted_lists'),
        clean_after=clean_after_of(fields=fields),
        swap_knobs=switches.get('swap_knobs'),
    )


# pylint: disable-next=too-many-arguments
def page_text(
    *,
    template: str,
    values: Mapping[str, str],
    error: str | None,
    switches: Collection[str],
    radios: Collection[str],
    errors: Mapping[str, str] | None = None,
) -> str:
    """
    Fill the Settings page's marks.

    Parameters
    ----------
    template : str
        The page with `{{field}}` marks. A switch mark becomes
        ` checked` when on; `{{field:choice}}` becomes ` checked` (a
        radio) or ` selected` (a list) when the field holds that choice;
        `{{error_<section>}}` becomes that section's message when it is
        in error; any other becomes the value.
    values : Mapping[str, str]
        The form values.
    error : str | None
        The section to show an error for, if any.
    switches : Collection[str]
        Every switch on the page, the device's and the app's.
    radios : Collection[str]
        Every radio group on the page, the device's and the app's.
    errors : Mapping[str, str] | None
        Each section's message; None gives the device's own.

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
            return error_line(
                shown=error == section, section=section, errors=errors,
            )
        value = values.get(name, '')
        if choice:
            mark = ' checked' if name in radios else ' selected'
            return mark if value == choice else ''
        if name in switches:
            return ' checked' if value == 'on' else ''
        return escape(value)

    return re.sub(r'\{\{([a-z0-9_:]+)\}\}', fill, template)


def error_line(
    *, shown: bool, section: str, errors: Mapping[str, str] | None = None,
) -> str:
    """
    Write a section's error paragraph.

    Parameters
    ----------
    shown : bool
        Whether the section has an error.
    section : str
        The section.
    errors : Mapping[str, str] | None
        Each section's message; None gives the device's own.

    Returns
    -------
    str
        The paragraph, or an empty string.
    """
    if not shown:
        return ''
    message = (ERRORS if errors is None else errors)[section]
    return f'<p class="error" role="alert">{message}</p>'
