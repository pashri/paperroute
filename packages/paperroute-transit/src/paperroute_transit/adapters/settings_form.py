"""Read and write the Settings page's board fields."""

from collections.abc import Mapping
from dataclasses import fields as dataclass_fields
from typing import Final

from paperroute_device.adapters.device_form import (
    BadInput,
    clock_text,
    device_values,
    optional_enum,
    switches_of,
    window_of,
)
from paperroute_device.core.device_settings import Window
from paperroute_transit.core.messages import SettingsChanged
from paperroute_transit.core.saved import (
    BoardSettings,
    ConnectionMap,
    Lookahead,
    Rate,
    Settings,
    with_changes,
)

BOARD_SWITCHES: Final[tuple[str, ...]] = (
    'auto_rebuild',
    'skip_school_buses',
    'skip_school_on_route_pins',
)
BOARD_RADIOS: Final[tuple[str, ...]] = ('connection_map',)
SLOTS: Final[tuple[str, ...]] = ('peak_1', 'peak_2')
CHOICES: Final[frozenset[str]] = frozenset(
    {'peak_rate', 'offpeak_rate', 'alert_lookahead'}
)
STATUS_SWITCHES: Final[tuple[str, ...]] = (
    'auto_rebuild',
    'rotate',
    'larger_text',
    'inverted_lists',
    'skip_school_buses',
    'skip_school_on_route_pins',
    'swap_knobs',
)
STATUS_RADIOS: Final[tuple[str, ...]] = ('quiet_mode', 'connection_map')
STATUS_TIMES: Final[tuple[str, ...]] = (
    'clean_after', 'quiet_start', 'quiet_end',
)
PEAK: Final[str] = 'peak'
ERRORS: Final[dict[str, str]] = {
    PEAK: (
        'Peak hours not saved: each start must be before its end, '
        'as HH:MM. Windows must not overlap.'
    ),
}


def peak_values(*, settings: BoardSettings) -> dict[str, str]:
    """
    Write the peak windows as the page's start and end times.

    Parameters
    ----------
    settings : BoardSettings
        The board settings.

    Returns
    -------
    dict[str, str]
        `HH:MM` start and end for each slot; empty strings for an unused
        slot.
    """
    values: dict[str, str] = {}
    for index, slot in enumerate(SLOTS):
        window = settings.peak_windows[index:index + 1]
        values[f'{slot}_start'] = (
            clock_text(moment=window[0].start) if window else ''
        )
        values[f'{slot}_end'] = (
            clock_text(moment=window[0].end) if window else ''
        )
    return values


def board_values(*, settings: BoardSettings) -> dict[str, str]:
    """
    Write the board settings as the strings the page's controls hold.

    Parameters
    ----------
    settings : BoardSettings
        The board settings.

    Returns
    -------
    dict[str, str]
        `on` or `off` for each switch, the rate, map and lookahead
        values, and `HH:MM` peak times; an unused peak slot holds empty
        strings.
    """
    values = {
        name: 'on' if getattr(settings, name) else 'off'
        for name in BOARD_SWITCHES
    }
    values |= {
        name: getattr(settings, name).value
        for name in (*CHOICES, *BOARD_RADIOS)
    }
    return values | peak_values(settings=settings)


def form_values(*, settings: Settings) -> dict[str, str]:
    """
    Write every setting as the status page reports it.

    Parameters
    ----------
    settings : Settings
        The device and board settings.

    Returns
    -------
    dict[str, str]
        The device's values and the board's together: the switches,
        the choices, the radios, the cleaning count, the quiet times,
        then the peak times.
    """
    values = {
        **device_values(settings=settings.device),
        **board_values(settings=settings.board),
    }
    order = (
        *STATUS_SWITCHES,
        *CHOICES,
        *STATUS_RADIOS,
        *STATUS_TIMES,
        *peak_values(settings=settings.board),
    )
    return {name: values[name] for name in order}


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
                section=PEAK,
            )
        )
    ]
    ordered = sorted(windows)
    if any(window.start >= window.end for window in windows) or any(
        first.end > second.start
        for first, second in zip(ordered, ordered[1:])
    ):
        raise BadInput(PEAK)
    return tuple(windows)


def changed_from(*, fields: Mapping[str, str]) -> SettingsChanged | None:
    """
    Turn posted fields into a board settings change.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields, the device's among them; only the board's
        are read.

    Returns
    -------
    SettingsChanged | None
        The change, settings with no field left as they are; None when
        no board field was posted.

    Raises
    ------
    UnknownValue
        If a switch, rate, map choice or lookahead holds an unknown
        value.
    BadInput
        If a peak time is not `HH:MM` or a peak window is unusable.
    """
    switches = switches_of(fields=fields, names=BOARD_SWITCHES)
    change = SettingsChanged(
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
        alert_lookahead=optional_enum(
            kind=Lookahead, fields=fields, name='alert_lookahead',
        ),
        connection_map=optional_enum(
            kind=ConnectionMap, fields=fields, name='connection_map',
        ),
        skip_school_buses=switches.get('skip_school_buses'),
        skip_school_on_route_pins=switches.get('skip_school_on_route_pins'),
    )
    return None if change == SettingsChanged() else change


def posted_values(
    *, fields: Mapping[str, str], event: SettingsChanged, defaults: Settings,
) -> dict[str, str]:
    """
    Write the posted board fields the way the app will report them.

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
        The form values of the posted board fields; a posted peak slot
        is written for every slot, as the app keeps them.
    """
    given = {
        item.name: getattr(event, item.name)
        for item in dataclass_fields(event)
        if getattr(event, item.name) is not None
    }
    full = board_values(
        settings=with_changes(settings=defaults, changes=given).board,
    )
    keys = set(fields)
    if event.peak_windows is not None:
        keys |= {f'{slot}_{end}' for slot in SLOTS for end in ('start', 'end')}
    return {key: full[key] for key in keys if key in full}
