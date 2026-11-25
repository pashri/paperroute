"""The kerb across the road from a station, and pins that cover both."""

from dataclasses import dataclass, replace
from typing import Final

from paperroute_device.core.device_model import AppRow
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.saved import StopSides

BOTH_SUFFIX: Final[str] = ', both sides'
SIDES_ROWS: Final[dict[StopSides, AppRow]] = {
    StopSides.TIME_ORDER: AppRow(
        label='Both sides: time order', stays=True,
    ),
    StopSides.EACH_WAY: AppRow(
        label='Both sides: next bus each way', stays=True,
    ),
}


@dataclass(frozen=True)
class KerbPair:
    """
    A station and the kerb across the road from it.

    Attributes
    ----------
    station : str
        ID of the station looked up.
    other : StopPlace
        The kerb across the road, with its point.
    busy : bool
        Whether both kerbs are busy enough to list apart.
    here : StopPlace | None
        The kerb on this side when the station holds both kerbs; None
        when the station is itself the kerb.
    """

    station: str
    other: StopPlace
    busy: bool
    here: StopPlace | None = None


@dataclass(frozen=True)
class ParentKerb:
    """
    One bus kerb of a parent station.

    Attributes
    ----------
    place : StopPlace
        The kerb, with its name and point.
    onward : frozenset[str]
        IDs of the stations its buses call at next.
    trips : int
        Trips leaving it.
    """

    place: StopPlace
    onward: frozenset[str]
    trips: int


@dataclass(frozen=True)
class ParentKerbs:
    """
    A parent station's two bus kerbs, whose buses leave different ways.

    Attributes
    ----------
    station : str
        ID of the parent station.
    kerbs : tuple[ParentKerb, ParentKerb]
        The two kerbs, by stop ID.
    busy : bool
        Whether both kerbs are busy enough to list apart.
    """

    station: str
    kerbs: tuple[ParentKerb, ParentKerb]
    busy: bool


@dataclass(frozen=True)
class Kerbs:
    """
    The paired kerbs of the line being picked from.

    Attributes
    ----------
    pairs : tuple[KerbPair, ...]
        The line's stations that have a kerb across the road.
    menu : int | None
        The box's cursor row while it is open over the cursor's station;
        None while it is closed.
    """

    pairs: tuple[KerbPair, ...] = ()
    menu: int | None = None


def sides_row(*, sides: StopSides) -> AppRow:
    """
    Give the menu row that names the view shown.

    Parameters
    ----------
    sides : StopSides
        The view the setting holds.

    Returns
    -------
    AppRow
        The row, which stays on the menu when pressed.
    """
    return SIDES_ROWS[sides]


def flipped(*, sides: StopSides) -> StopSides:
    """
    Give the other view.

    Parameters
    ----------
    sides : StopSides
        The view the setting holds.

    Returns
    -------
    StopSides
        The other of the two views.
    """
    if sides is StopSides.TIME_ORDER:
        return StopSides.EACH_WAY
    return StopSides.TIME_ORDER


def both_sides(*, pin: Pin) -> bool:
    """
    Say whether a pin covers both kerbs of a road.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    bool
        True when the pin has an opposite kerb other than its From and
        no Destination.
    """
    return (
        pin.opposite is not None
        and pin.opposite.stop_id != pin.origin.stop_id
        and pin.destination is None
    )


def pin_places(*, pin: Pin) -> tuple[StopPlace, ...]:
    """
    List the places a pin reads from the timetable.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    tuple[StopPlace, ...]
        The From, then the opposite kerb when the pin is both-sides,
        then the Destination when there is one.
    """
    places = (
        pin.origin,
        pin.opposite if both_sides(pin=pin) else None,
        pin.destination,
    )
    return tuple(place for place in places if place is not None)


def pin_title(*, pin: Pin) -> str:
    """
    Name a pin's place for a heading.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    str
        The name of the parent the pin was converted from, else the
        From name, with the both-sides suffix when the pin is
        both-sides.
    """
    suffix = BOTH_SUFFIX if both_sides(pin=pin) else ''
    named = pin.origin if pin.parent is None else pin.parent
    return f'{named.name}{suffix}'


def kept_sides(*, old: Pin | None, new: Pin) -> Pin:
    """
    Carry an edited pin's opposite kerb over from the pin it replaces.

    Parameters
    ----------
    old : Pin | None
        The pin before the edit, or None for a new pin.
    new : Pin
        The pin after the edit.

    Returns
    -------
    Pin
        The new pin with the old one's opposite kerb and towards names
        when the old pin was both-sides, the new one has no Destination
        and the same From; otherwise the new pin unchanged.
    """
    if (
        old is None
        or not both_sides(pin=old)
        or new.destination is not None
        or new.origin.stop_id != old.origin.stop_id
    ):
        return new
    return replace(new, opposite=old.opposite, towards=old.towards)
