"""Transit's Device screen lines, and its confirm and reset copy."""

from dataclasses import dataclass
from typing import Final

from paperroute_device.core.device_model import ConfirmAction
from paperroute_device.core.panel_views import UNKNOWN, ConfirmCopy, InfoRow
from paperroute_device.core.text import DOT, clock_text, day_text, local_time
from paperroute_transit.core.quota import calls_today
from paperroute_transit.core.state_model import State

TRANSIT_CONFIRM_COPY: Final[ConfirmCopy] = {
    ConfirmAction.RESTART: (
        'paperroute starts again. Your pins and settings stay.'
    ),
    ConfirmAction.SHUT_DOWN: (
        "paperroute switches off. The screen says when it's safe"
        ' to unplug.'
    ),
    ConfirmAction.RESET_SETTINGS: (
        'Clears pins, settings, Wi-Fi and the key.'
        ' The timetable and map stay.'
    ),
    ConfirmAction.ERASE: (
        'Clears pins, settings, Wi-Fi, the key and the timetable.'
        ' The map stays.'
    ),
    ConfirmAction.JOIN_WIFI: (
        'paperroute leaves its Wi-Fi while you set up the new one.'
    ),
}


@dataclass(frozen=True)
class TilesFacts:
    """
    What the app's probe found about the map tiles.

    Attributes
    ----------
    built : str | None
        When the tiles were built, or None with no tiles.
    """

    built: str | None


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
    built = local_time(stamp=state.saving.timetable, zone=state.city.zone)
    if built is None:
        return DOT.join(parts) or UNKNOWN
    clock = clock_text(moment=built, zone=state.city.zone)
    parts += [day_text(moment=built), f'built {clock}']
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


def tiles_value(*, state: State) -> str:
    """
    Describe the map tiles.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str
        The tiles' build day, or a dash when unknown.
    """
    facts = state.panel.facts
    app = None if facts is None else facts.app
    stamp = app.built if isinstance(app, TilesFacts) else None
    tiles = local_time(stamp=stamp, zone=state.city.zone)
    return UNKNOWN if tiles is None else f'built {day_text(moment=tiles)}'


def transit_rows(*, state: State) -> tuple[InfoRow, ...]:
    """
    Gather transit's lines on the Device screen.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[InfoRow, ...]
        Timetable, map tiles and live data.
    """
    return (
        InfoRow(label='Timetable', value=timetable_value(state=state)),
        InfoRow(label='Map tiles', value=tiles_value(state=state)),
        InfoRow(label='Live data', value=live_value(state=state)),
    )
