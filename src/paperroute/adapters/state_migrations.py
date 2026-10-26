"""Steps that bring an older state.json up to the current schema."""

from collections.abc import Callable
from typing import Final

from paperroute.adapters.state_auto import add_auto
from paperroute.adapters.state_change import add_connections
from paperroute.adapters.state_settings import default_settings
from paperroute.core.model import TRAINLINK

SCHEMA_VERSION: Final[int] = 9


def coach_pin(*, pin: object) -> object:
    """
    Move one stored Regional pin to Coach, or to Train for a TrainLink line.

    Parameters
    ----------
    pin : object
        A stored pin; anything that is not a Regional pin is returned as
        it is.

    Returns
    -------
    object
        The pin with its kind rewritten.
    """
    if not isinstance(pin, dict) or pin.get('kind') != 'regional':
        return pin
    routes = pin.get('routes')
    trainlink = (
        isinstance(routes, list)
        and bool(routes)
        and all(route in TRAINLINK for route in routes)
    )
    return {**pin, 'kind': 'train' if trainlink else 'coach'}


def regional_to_coach(data: dict[str, object]) -> dict[str, object]:
    """
    Rewrite every Regional pin, and the unpinned place, as Coach or Train.

    Parameters
    ----------
    data : dict[str, object]
        Version 1 data.

    Returns
    -------
    dict[str, object]
        The data with its pins rewritten.
    """
    pins = data.get('pins')
    return {
        **data,
        'pins': (
            [coach_pin(pin=pin) for pin in pins]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': coach_pin(pin=data.get('unpinned')),
    }


def add_settings(data: dict[str, object]) -> dict[str, object]:
    """
    Add the settings, with the timetable rebuilt automatically.

    Parameters
    ----------
    data : dict[str, object]
        Version 2 data.

    Returns
    -------
    dict[str, object]
        The data with default settings.
    """
    return {**data, 'settings': {'auto_rebuild': True}}


def add_quota(data: dict[str, object]) -> dict[str, object]:
    """
    Add the quota, with no calls counted.

    Parameters
    ----------
    data : dict[str, object]
        Version 3 data.

    Returns
    -------
    dict[str, object]
        The data with an empty quota.
    """
    return {**data, 'quota': {'day': None, 'calls': 0}}


def add_display_settings(data: dict[str, object]) -> dict[str, object]:
    """
    Add the rate, quiet-hours and display settings at their defaults.

    Parameters
    ----------
    data : dict[str, object]
        Version 4 data.

    Returns
    -------
    dict[str, object]
        The data with every new setting at its default; settings that
        are not an object are left for validation to fail.
    """
    settings = data.get('settings')
    if not isinstance(settings, dict):
        return data
    return {**data, 'settings': {**default_settings(), **settings}}


def add_pin_fields(data: dict[str, object]) -> dict[str, object]:
    """
    Give every pin an empty nickname and route filter.

    Parameters
    ----------
    data : dict[str, object]
        Version 5 data.

    Returns
    -------
    dict[str, object]
        The data with `nickname` None and `only` empty on each pin and
        on the unpinned place; anything that is not an object is left
        for validation to fail.
    """
    fields: dict[str, object] = {'nickname': None, 'only': []}
    pins = data.get('pins')
    unpinned = data.get('unpinned')
    return {
        **data,
        'pins': (
            [
                {**fields, **pin} if isinstance(pin, dict) else pin
                for pin in pins
            ]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': (
            {**fields, **unpinned} if isinstance(unpinned, dict) else unpinned
        ),
    }


def add_alert_settings(data: dict[str, object]) -> dict[str, object]:
    """
    Add the alert lookahead at its default.

    Parameters
    ----------
    data : dict[str, object]
        Version 6 data.

    Returns
    -------
    dict[str, object]
        The data with every new setting at its default; settings that
        are not an object are left for validation to fail.
    """
    return add_display_settings(data)


MIGRATIONS: Final[
    dict[int, Callable[[dict[str, object]], dict[str, object]]]
] = {
    1: regional_to_coach,
    2: add_settings,
    3: add_quota,
    4: add_display_settings,
    5: add_pin_fields,
    6: add_alert_settings,
    7: add_auto,
    8: add_connections,
}


def migrate(*, data: dict[str, object]) -> dict[str, object]:
    """
    Bring stored data up to the current schema version.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.

    Returns
    -------
    dict[str, object]
        The data at `SCHEMA_VERSION`.

    Raises
    ------
    ValueError
        When the version is missing, above `SCHEMA_VERSION`, or has no
        migration step.
    """
    version = data.get('schema_version')
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f'bad schema version {version!r}')
    if version > SCHEMA_VERSION:
        raise ValueError(f'schema version {version} is from the future')
    while version < SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise ValueError(f'no migration from version {version}')
        data = MIGRATIONS[version](data)
        version += 1
        data['schema_version'] = version
    return data
