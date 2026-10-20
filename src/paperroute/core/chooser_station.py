"""Picking a station, then a destination on the same line, in the lists."""

from dataclasses import replace

from paperroute.core.chooser import (
    ChooserRequest,
    ChooserState,
    ColumnRequest,
    DepartureLoad,
    Level,
    Stage,
    backed,
    chosen,
    chosen_pin,
    focusable,
    path_to,
    pressed,
    previewed,
)
from paperroute.core.model import Pin
from paperroute.core.reach import Reach
from paperroute.core.strip_model import ALL_TAG


def focus_level(*, chooser: ChooserState) -> Level | None:
    """
    Find what the focused column lists.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    Level | None
        The focus's level in the LISTS stage, else None.
    """
    if chooser.stage is not Stage.LISTS or not chooser.columns:
        return None
    return chooser.columns[chooser.focus].level


def has_list_from(*, chooser: ChooserState) -> bool:
    """
    Say whether a station press has chosen the chooser's From.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    bool
        True in the LISTS stage with a From, from the press on until the
        cursor leaves the station or the TO column is left.
    """
    return chooser.stage is Stage.LISTS and chooser.origin is not None


def pressed_station(
    *, chooser: ChooserState,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Take the cursor's station as the From and ask for its TO column.

    Parameters
    ----------
    chooser : ChooserState
        Chooser focused on a STATION column.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser with the From and its request; unchanged on a row
        with no place, or while that column is already asked for.
    """
    choice = chosen(chooser=chooser, index=chooser.focus)
    path = path_to(chooser=chooser, index=chooser.focus)
    if choice.place is None or not focusable(choice=choice):
        return chooser, None
    if chooser.waiting == path:
        return chooser, None
    picked = replace(
        chooser, origin=choice.place, waiting=path, failed=None,
        departures=(), load=DepartureLoad(),
    )
    return picked, ColumnRequest(path=path)


def pressed_destination(
    *, chooser: ChooserState,
) -> tuple[ChooserState, Pin | None]:
    """
    Make the place the TO column's cursor picks.

    Parameters
    ----------
    chooser : ChooserState
        Chooser focused on a TO column.

    Returns
    -------
    tuple[ChooserState, Pin | None]
        The chooser and the place: no Destination on the From's row, else
        the row's station. No place on a row that can't be picked.
    """
    choice = chosen(chooser=chooser, index=chooser.focus)
    if chooser.origin is None or not focusable(choice=choice):
        return chooser, None
    destination = None if choice.tag == ALL_TAG else choice.place
    return chooser, chosen_pin(chooser=chooser, destination=destination)


def backed_to(*, chooser: ChooserState) -> ChooserState:
    """
    Go back from the TO column to the STATION column.

    Parameters
    ----------
    chooser : ChooserState
        Chooser focused on a TO column.

    Returns
    -------
    ChooserState
        The chooser focused on the STATION column, its cursor on the From,
        with no From, departures or load.
    """
    focus = chooser.focus - 1
    keys = [choice.key for choice in chooser.columns[focus].items]
    key = '' if chooser.origin is None else chooser.origin.stop_id
    cursor = keys.index(key) if key in keys else chooser.cursors[focus]
    cursors = (*chooser.cursors[:focus], cursor)
    back, _ = previewed(
        chooser=replace(chooser, focus=focus, cursors=cursors),
    )
    return back


def knob_pressed(
    *, chooser: ChooserState, reach: Reach | None,
) -> tuple[ChooserState, ChooserRequest | Pin | None]:
    """
    Handle a bottom-knob press, STATION and TO columns included.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    reach : Reach | None
        The TO stage's strip, or None.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | Pin | None]
        The chooser and its request; a Pin means commit that place.
    """
    match focus_level(chooser=chooser):
        case Level.STATION:
            return pressed_station(chooser=chooser)
        case Level.TO:
            return pressed_destination(chooser=chooser)
    return pressed(chooser=chooser, reach=reach)


def knob_backed(*, chooser: ChooserState) -> ChooserState | None:
    """
    Handle a top-knob press, the TO column included: go back one step.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    ChooserState | None
        The chooser one step back, or None when it closes.
    """
    if focus_level(chooser=chooser) is Level.TO:
        return backed_to(chooser=chooser)
    return backed(chooser=chooser)
