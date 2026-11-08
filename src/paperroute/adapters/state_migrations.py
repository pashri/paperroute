"""Steps that bring an older state.json up to the current schema."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final, Protocol

from paperroute.adapters.state_auto import add_auto
from paperroute.adapters.state_change import add_connections, before_rewritten
from paperroute.adapters.state_settings import default_settings
from paperroute_device.core.sections import (
    DEVICE_MIGRATIONS,
    DEVICE_SCHEMA,
    SectionStep,
    migrate_section,
)
from paperroute_transit.core.city import City

SCHEMA_VERSION: Final[int] = 12
BOARD_SCHEMA: Final[int] = 1
BOARD_MIGRATIONS: Final[Mapping[int, SectionStep]] = {}
BOARD_KEYS: Final[tuple[str, ...]] = (
    'pins', 'current', 'unpinned', 'view', 'quota', 'auto',
)
V11_DEVICE_FIELDS: Final[frozenset[str]] = frozenset({
    'auto_rebuild', 'clean_after', 'inverted_lists', 'larger_text',
    'my_hours', 'quiet_mode', 'rotate', 'swap_knobs',
})
V1_TRAINLINK_LINES: Final[tuple[str, ...]] = ('NRC', 'NRW', 'STH', 'WST')


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
        and all(route in V1_TRAINLINK_LINES for route in routes)
    )
    return {**pin, 'kind': 'train' if trainlink else 'coach'}


def regional_to_coach(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Rewrite every Regional pin, and the unpinned place, as Coach or Train.

    Parameters
    ----------
    data : dict[str, object]
        Version 1 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data with its pins rewritten.
    """
    del city
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


def add_settings(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Add the settings, with the timetable rebuilt automatically.

    Parameters
    ----------
    data : dict[str, object]
        Version 2 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data with default settings.
    """
    del city
    return {**data, 'settings': {'auto_rebuild': True}}


def add_quota(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Add the quota, with no calls counted.

    Parameters
    ----------
    data : dict[str, object]
        Version 3 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data with an empty quota.
    """
    del city
    return {**data, 'quota': {'day': None, 'calls': 0}}


def add_display_settings(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Add the rate, quiet-hours and display settings at their defaults.

    Parameters
    ----------
    data : dict[str, object]
        Version 4 data.
    city : City
        The city, whose defaults new settings take.

    Returns
    -------
    dict[str, object]
        The data with every new setting at its default; settings that
        are not an object are left for validation to fail.
    """
    settings = data.get('settings')
    if not isinstance(settings, dict):
        return data
    return {**data, 'settings': {**default_settings(city=city), **settings}}


def add_pin_fields(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Give every pin an empty nickname and route filter.

    Parameters
    ----------
    data : dict[str, object]
        Version 5 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data with `nickname` None and `only` empty on each pin and
        on the unpinned place; anything that is not an object is left
        for validation to fail.
    """
    del city
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


def add_alert_settings(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Add the alert lookahead at its default.

    Parameters
    ----------
    data : dict[str, object]
        Version 6 data.
    city : City
        The city, whose defaults new settings take.

    Returns
    -------
    dict[str, object]
        The data with every new setting at its default; settings that
        are not an object are left for validation to fail.
    """
    return add_display_settings(data, city=city)


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
    try:
        text = json.dumps(v9_parts(pin=pin))
    except (KeyError, TypeError, ValueError):
        return None
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def v9_parts(*, pin: dict[str, Any]) -> list[object]:
    """
    List what a schema 9 pin's ref was worked out from.

    Parameters
    ----------
    pin : dict[str, Any]
        A stored schema 9 pin.

    Returns
    -------
    list[object]
        The From and To stop IDs, kind, routes and only list, then the
        change's stops, allow and only list when it has one.

    Raises
    ------
    KeyError
        When a part is missing.
    TypeError
        When a part has the wrong shape.
    """
    destination = pin.get('destination')
    change = pin.get('change')
    parts: list[object] = [
        pin['origin']['stop_id'],
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
    return parts


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


def merged(*, first: dict[str, object], later: dict[str, object]
           ) -> dict[str, object]:
    """
    Fold a pin into an earlier one with the same identity.

    Parameters
    ----------
    first : dict[str, object]
        The pin kept, rewritten by `with_ways`.
    later : dict[str, object]
        The pin dropped, rewritten by `with_ways`.

    Returns
    -------
    dict[str, object]
        The first pin with the later one's windows and ways added after
        its own, leaving out ones it has already.
    """
    combined = dict(first)
    for key in ('windows', 'ways'):
        mine, theirs = first.get(key), later.get(key)
        if isinstance(mine, list) and isinstance(theirs, list):
            combined[key] = mine + [
                item for item in theirs if item not in mine
            ]
    return combined


def placed(
    *,
    pin: object,
    found: list[object],
    kept: dict[str, tuple[int, str | None]],
) -> int:
    """
    Add one pin rewritten by `with_ways`, or merge it into an earlier one.

    Parameters
    ----------
    pin : object
        A stored schema 9 pin.
    found : list[object]
        The pins so far; the pin is added or merged in place.
    kept : dict[str, tuple[int, str | None]]
        Position and schema 9 ref of the first pin with each new ref;
        updated in place.

    Returns
    -------
    int
        Where in `found` the pin went.
    """
    new = with_ways(pin=pin)
    old = v9_ref(pin=pin)
    ref = v9_ref(pin={**new, 'change': None}) if isinstance(
        new, dict,
    ) else None
    index, first_old = kept.get(ref or '', (-1, old))
    if first_old != old and merged_at(found=found, index=index, pin=new):
        return index
    if ref is not None:
        kept.setdefault(ref, (len(found), old))
    found.append(new)
    return len(found) - 1


def merged_at(*, found: list[object], index: int, pin: object) -> bool:
    """
    Merge a rewritten pin into the one at a position, when both are pins.

    Parameters
    ----------
    found : list[object]
        The pins so far; changed in place.
    index : int
        The position; negative for none.
    pin : object
        The rewritten pin.

    Returns
    -------
    bool
        Whether it was merged.
    """
    first = found[index] if index >= 0 else None
    if not isinstance(first, dict) or not isinstance(pin, dict):
        return False
    found[index] = merged(first=first, later=pin)
    return True


def unique_pins(*, pins: list[object]) -> tuple[list[object], list[int]]:
    """
    Rewrite pins by `with_ways`, merging ones that come to share a ref.

    Parameters
    ----------
    pins : list[object]
        The stored schema 9 pins.

    Returns
    -------
    tuple[list[object], list[int]]
        The pins in order, and where each stored pin went. A To makes a
        pin lose its routes, so two different pins can come to share a
        ref; the later is merged into the earlier. Pins that already
        shared a ref are left alone.
    """
    found: list[object] = []
    kept: dict[str, tuple[int, str | None]] = {}
    where = [placed(pin=pin, found=found, kept=kept) for pin in pins]
    return found, where


def moved_current(*, current: object, where: list[int]) -> object:
    """
    Point the shown pin's index at where its pin went.

    Parameters
    ----------
    current : object
        The stored index.
    where : list[int]
        Where each stored pin went.

    Returns
    -------
    object
        The new index; anything out of range is returned as it is.
    """
    if isinstance(current, int) and 0 <= current < len(where):
        return where[current]
    return current


def add_ways(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Give pins ways and a never list, and rename the change map setting.

    Parameters
    ----------
    data : dict[str, object]
        Version 9 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data with each pin, the unpinned place and the auto before
        place rewritten by `with_ways`, pins sharing a new ref merged,
        the running occurrence keyed by its pin's new ref and the
        `change` map setting as `changes`.
    """
    del city
    pins = data.get('pins')
    if not isinstance(pins, list):
        return ways_added(data=data, refs={})
    found, where = unique_pins(pins=pins)
    current = moved_current(current=data.get('current'), where=where)
    return ways_added(
        data={**data, 'pins': found, 'current': current},
        refs=new_refs(pins=pins),
    )


def ways_added(
    *, data: dict[str, object], refs: dict[str, str | None],
) -> dict[str, object]:
    """
    Rewrite everything but the pins for schema 10.

    Parameters
    ----------
    data : dict[str, object]
        Version 9 data, its pins already rewritten.
    refs : dict[str, str | None]
        New ref by schema 9 ref.

    Returns
    -------
    dict[str, object]
        The data with the unpinned place and auto block rewritten and the
        map setting renamed.
    """
    return {
        **data,
        'unpinned': with_ways(pin=data.get('unpinned')),
        'auto': with_before_ways(
            auto=rewritten_ref(auto=data.get('auto'), refs=refs),
        ),
        'settings': renamed_map(settings=data.get('settings')),
    }


def drop_zoom(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Drop the saved map zoom, which is no longer kept.

    Parameters
    ----------
    data : dict[str, object]
        Version 10 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data without its ``zoom`` key, if it had one.
    """
    del city
    return {key: value for key, value in data.items() if key != 'zoom'}


def split_sections(
    data: dict[str, object], *, city: City,
) -> dict[str, object]:
    """
    Move the device's settings and the board's fields into two sections.

    Parameters
    ----------
    data : dict[str, object]
        Version 11 data.
    city : City
        The city; this step needs nothing from it.

    Returns
    -------
    dict[str, object]
        The data with a ``device`` section holding the device's settings
        and a ``board`` section holding the pins, place, view, quota,
        auto block and every other setting, each at version 1. Settings
        that are not an object go to both sections, for repair to reset;
        other unknown fields stay at the top, for validation to fail.
    """
    del city
    settings = data.get('settings')
    device_settings: object = settings
    board_settings: object = settings
    if isinstance(settings, dict):
        device_settings = {
            name: value
            for name, value in settings.items()
            if name in V11_DEVICE_FIELDS
        }
        board_settings = {
            name: value
            for name, value in settings.items()
            if name not in V11_DEVICE_FIELDS
        }
    moved = {*BOARD_KEYS, 'settings'}
    return {
        **{key: value for key, value in data.items() if key not in moved},
        'device': {'schema_version': 1, 'settings': device_settings},
        'board': {
            'schema_version': 1,
            **{key: data[key] for key in BOARD_KEYS if key in data},
            'settings': board_settings,
        },
    }


class MigrationStep(Protocol):
    """Brings stored data up one schema version."""

    def __call__(
        self, data: dict[str, object], *, city: City,
    ) -> dict[str, object]:
        """
        Migrate the data.

        Parameters
        ----------
        data : dict[str, object]
            Data at one version.
        city : City
            The city, whose defaults new settings take.

        Returns
        -------
        dict[str, object]
            The data at the next version.
        """


MIGRATIONS: Final[dict[int, MigrationStep]] = {
    1: regional_to_coach,
    2: add_settings,
    3: add_quota,
    4: add_display_settings,
    5: add_pin_fields,
    6: add_alert_settings,
    7: add_auto,
    8: add_connections,
    9: add_ways,
    10: drop_zoom,
    11: split_sections,
}


def stored_version(*, data: dict[str, object]) -> int:
    """
    Read the stored schema version.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.

    Returns
    -------
    int
        The version.

    Raises
    ------
    ValueError
        When it is missing, not a whole number or above `SCHEMA_VERSION`.
    """
    version = data.get('schema_version')
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f'bad schema version {version!r}')
    if version > SCHEMA_VERSION:
        raise ValueError(f'schema version {version} is from the future')
    return version


def migrate(*, data: dict[str, object], city: City) -> dict[str, object]:
    """
    Bring stored data up to the current schema version.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.
    city : City
        The city, whose defaults new settings take.

    Returns
    -------
    dict[str, object]
        The data at `SCHEMA_VERSION`.

    Raises
    ------
    ValueError
        When the version is missing, above `SCHEMA_VERSION`, or has no
        migration step, or the same holds for a section.
    """
    version = stored_version(data=data)
    while version < SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise ValueError(f'no migration from version {version}')
        data = MIGRATIONS[version](data, city=city)
        version += 1
        data['schema_version'] = version
    return migrated_sections(data=data)


def migrated_sections(*, data: dict[str, object]) -> dict[str, object]:
    """
    Bring the device and board sections up to their latest versions.

    Parameters
    ----------
    data : dict[str, object]
        Data at `SCHEMA_VERSION`.

    Returns
    -------
    dict[str, object]
        The data with each section migrated; a missing or non-object
        section is left for repair to reset.

    Raises
    ------
    ValueError
        When a section's version is missing, too new or has no step.
    """
    device = data.get('device')
    board = data.get('board')
    if isinstance(device, dict):
        data = {**data, 'device': migrate_section(
            data=device,
            steps=DEVICE_MIGRATIONS,
            latest=DEVICE_SCHEMA,
            name='device',
        )}
    if isinstance(board, dict):
        data = {**data, 'board': migrate_section(
            data=board,
            steps=BOARD_MIGRATIONS,
            latest=BOARD_SCHEMA,
            name='board',
        )}
    return data
