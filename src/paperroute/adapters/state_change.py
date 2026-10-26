"""Connection changes on stored pins: the v9 step and per-field repair."""

from typing import Final

from paperroute.adapters.state_settings import default_settings

ALLOW_MIN: Final[int] = 1  # minutes
ALLOW_MAX: Final[int] = 60  # minutes


def with_change(*, pin: object) -> object:
    """
    Give one stored pin no change.

    Parameters
    ----------
    pin : object
        A stored pin; anything that is not an object is returned as it is.

    Returns
    -------
    object
        The pin with `change` None unless it already has one.
    """
    if not isinstance(pin, dict):
        return pin
    return {'change': None, **pin}


def with_before_change(*, auto: object) -> object:
    """
    Give the place kept by the auto pin state no change.

    Parameters
    ----------
    auto : object
        The stored auto block.

    Returns
    -------
    object
        The block with its `before.place` given no change; anything that
        is not shaped that way is left for validation.
    """
    if not isinstance(auto, dict):
        return auto
    before = auto.get('before')
    if not isinstance(before, dict):
        return auto
    place = with_change(pin=before.get('place'))
    return {**auto, 'before': {**before, 'place': place}}


def add_connections(data: dict[str, object]) -> dict[str, object]:
    """
    Give every stored pin no change and add the new settings.

    Parameters
    ----------
    data : dict[str, object]
        Version 8 data.

    Returns
    -------
    dict[str, object]
        The data with `change` None on each pin, the unpinned place and
        the auto pin state's place, and every new setting at its default.
    """
    pins = data.get('pins')
    settings = data.get('settings')
    return {
        **data,
        'pins': (
            [with_change(pin=pin) for pin in pins]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': with_change(pin=data.get('unpinned')),
        'auto': with_before_change(auto=data.get('auto')),
        'settings': (
            {**default_settings(), **settings}
            if isinstance(settings, dict)
            else settings
        ),
    }


def route_names(*, value: object) -> object:
    """
    Keep a stored route filter only when it is a list of names.

    Parameters
    ----------
    value : object
        The stored filter.

    Returns
    -------
    object
        The filter, or an empty list when it is not a list of strings.
    """
    if isinstance(value, list) and all(
        isinstance(route, str) for route in value
    ):
        return value
    return []


def repaired_allow(*, allow: object) -> object:
    """
    Clamp a whole-minute allowance into 1-60.

    Parameters
    ----------
    allow : object
        The stored allowance.

    Returns
    -------
    object
        The clamped minutes; anything that is not an integer is returned
        as it is.
    """
    if not isinstance(allow, int) or isinstance(allow, bool):
        return allow
    return min(max(allow, ALLOW_MIN), ALLOW_MAX)


def repaired_change(*, pin: dict[str, object]) -> dict[str, object]:
    """
    Repair the parts of a stored pin's change that can be repaired.

    Parameters
    ----------
    pin : dict[str, object]
        The stored pin.

    Returns
    -------
    dict[str, object]
        The pin with a missing change set to None, a route filter that is
        not a list of names emptied and an integer allowance clamped into
        1-60. Anything else wrong is left for validation.
    """
    change = pin.get('change')
    if not isinstance(change, dict):
        return {**pin, 'change': change}
    fixed = {
        **change,
        'only': route_names(value=change.get('only')),
    }
    if 'allow' in change:
        fixed['allow'] = repaired_allow(allow=change['allow'])
    return {**pin, 'change': fixed}
