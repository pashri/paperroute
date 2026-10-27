"""Steps that bring an older state.json up to the current schema."""

import hashlib
import json
from collections.abc import Callable
from typing import Final

from paperroute.adapters.state_auto import add_auto
from paperroute.adapters.state_change import add_connections, before_rewritten
from paperroute.adapters.state_settings import default_settings
from paperroute.core.model import TRAINLINK

SCHEMA_VERSION: Final[int] = 10


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


def v9_ref(*, pin: object) -> str | None:
    """
    Give a stored schema 9 pin's ref as schema 9 worked it out.

    Parameters
    ----------
    pin : object
        A stored schema 9 pin.

    Returns
    -------
    str | None
        Twelve hex digits, or None when the pin is not shaped well
        enough to have one.
    """
    if not isinstance(pin, dict):
        return None
    origin = pin.get('origin')
    destination = pin.get('destination')
    change = pin.get('change')
    try:
        parts: list[object] = [
            origin['stop_id'],  # type: ignore[index]
            None if destination is None else destination['stop_id'],
            pin.get('kind'),
            list(pin['routes']),
            list(pin['only']),
        ]
        if change is not None:
            parts.append([
                change['alight']['stop_id'],
                change['board']['stop_id'],
                change['allow'],
                list(change['only']),
            ])
        text = json.dumps(parts)
    except (KeyError, TypeError, ValueError):
        return None
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def with_ways(*, pin: object) -> object:
    """
    Rewrite one stored schema 9 pin's change as ways.

    Parameters
    ----------
    pin : object
        A stored pin; anything that is not an object is returned as it is.

    Returns
    -------
    object
        The pin without `change`: a change becomes one ticked way
        without its leg-2 filter, a pin with a To loses its routes and
        filter, and every pin gets an empty never list.
    """
    if not isinstance(pin, dict):
        return pin
    fixed = {key: value for key, value in pin.items() if key != 'change'}
    change = pin.get('change')
    if isinstance(change, dict):
        change = {key: value for key, value in change.items() if key != 'only'}
    if change is not None:
        fixed['ways'] = [{'changes': [change], 'ticked': True}]
    if pin.get('destination') is not None:
        fixed.update(routes=[], only=[])
    return {'ways': [], 'never': [], **fixed}


def rewritten_ref(
    *, auto: object, refs: dict[str, str | None],
) -> object:
    """
    Key the running occurrence by its pin's new ref.

    Parameters
    ----------
    auto : object
        The stored auto block.
    refs : dict[str, str | None]
        New ref by schema 9 ref.

    Returns
    -------
    object
        The block; an occurrence matching no schema 9 pin is kept.
    """
    if not isinstance(auto, dict):
        return auto
    occurrence = auto.get('occurrence')
    if not isinstance(occurrence, dict):
        return auto
    ref = refs.get(str(occurrence.get('ref')))
    if ref is None:
        return auto
    return {**auto, 'occurrence': {**occurrence, 'ref': ref}}


def with_before_ways(*, auto: object) -> object:
    """
    Rewrite the place kept by the auto pin state as a pin with ways.

    Parameters
    ----------
    auto : object
        The stored auto block.

    Returns
    -------
    object
        The block with its `before.place` rewritten by `with_ways`.
    """
    return before_rewritten(
        auto=auto, rewrite=lambda pin: with_ways(pin=pin),
    )


def renamed_map(*, settings: object) -> object:
    """
    Rename the schema 9 `change` map setting to `changes`.

    Parameters
    ----------
    settings : object
        The stored settings.

    Returns
    -------
    object
        The settings; anything else is returned as it is.
    """
    if not isinstance(settings, dict):
        return settings
    if settings.get('connection_map') != 'change':
        return settings
    return {**settings, 'connection_map': 'changes'}


def new_refs(*, pins: list[object]) -> dict[str, str | None]:
    """
    Pair each stored schema 9 pin's ref with its schema 10 ref.

    Parameters
    ----------
    pins : list[object]
        The stored schema 9 pins.

    Returns
    -------
    dict[str, str | None]
        New ref by old ref, for pins shaped well enough to have one.
    """
    refs: dict[str, str | None] = {}
    for pin in pins:
        old = v9_ref(pin=pin)
        new = with_ways(pin=pin)
        if old is not None and isinstance(new, dict):
            refs[old] = v9_ref(pin={**new, 'change': None})
    return refs


def add_ways(data: dict[str, object]) -> dict[str, object]:
    """
    Give pins ways and a never list, and rename the change map setting.

    Parameters
    ----------
    data : dict[str, object]
        Version 9 data.

    Returns
    -------
    dict[str, object]
        The data with each pin, the unpinned place and the auto before
        place rewritten by `with_ways`, the running occurrence keyed by
        its pin's new ref and the `change` map setting as `changes`.
    """
    pins = data.get('pins')
    refs = new_refs(pins=pins) if isinstance(pins, list) else {}
    return {
        **data,
        'pins': (
            [with_ways(pin=pin) for pin in pins]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': with_ways(pin=data.get('unpinned')),
        'auto': with_before_ways(
            auto=rewritten_ref(auto=data.get('auto'), refs=refs),
        ),
        'settings': renamed_map(settings=data.get('settings')),
    }


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
    9: add_ways,
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
