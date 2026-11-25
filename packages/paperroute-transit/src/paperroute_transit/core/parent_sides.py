"""Splitting a parent station's runs between its two kerbs."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from paperroute_transit.core.model import Pin, StopPlace, pin_key
from paperroute_transit.core.reach import RunTail, StripKey
from paperroute_transit.core.sides import KerbPair, ParentKerb, ParentKerbs


def leaving(
    *, tails: Iterable[RunTail], onward: frozenset[str],
) -> tuple[RunTail, ...]:
    """
    Keep the tails whose first station after the From is in a set.

    Parameters
    ----------
    tails : Iterable[RunTail]
        The runs through the From.
    onward : frozenset[str]
        IDs of the stations a kerb's buses call at next.

    Returns
    -------
    tuple[RunTail, ...]
        The matching tails, in order. A tail with nothing after the From
        is never kept.
    """
    return tuple(
        tail for tail in tails if tail.after and tail.after[0] in onward
    )


def kerb_sides(
    *,
    key: StripKey,
    tails: tuple[RunTail, ...],
    across_tails: tuple[RunTail, ...],
    parents: ParentKerbs | None,
) -> tuple[tuple[RunTail, ...], tuple[RunTail, ...]]:
    """
    Split a parent station's tails between the key's kerb and the other.

    Parameters
    ----------
    key : StripKey
        The strip's From and the kerb across.
    tails : tuple[RunTail, ...]
        The tails read for the From's station.
    across_tails : tuple[RunTail, ...]
        The tails read for the kerb across.
    parents : ParentKerbs | None
        The From's parent station's kerbs, if it has them.

    Returns
    -------
    tuple[tuple[RunTail, ...], tuple[RunTail, ...]]
        The tails of the key's kerb and of the kerb across. When the key
        is one kerb of the parent, each keeps the tails leaving toward
        its own stations; otherwise both come back unchanged.
    """
    if parents is None:
        return tails, across_tails
    onward = {each.place.stop_id: each.onward for each in parents.kerbs}
    if key.station not in onward:
        return tails, across_tails
    mine = leaving(tails=tails, onward=onward[key.station])
    if key.across in onward and key.across != key.station:
        return mine, leaving(tails=tails, onward=onward[key.across])
    return mine, across_tails


def parent_pair(
    *, line: Sequence[StopPlace], index: int, parents: ParentKerbs,
) -> KerbPair | None:
    """
    Orient a parent station's kerbs on a line.

    Parameters
    ----------
    line : Sequence[StopPlace]
        Stations in running order.
    index : int
        Index of the parent station on the line.
    parents : ParentKerbs
        The parent station's kerbs.

    Returns
    -------
    KerbPair | None
        The pair whose here kerb is the one leaving for the next station
        (for the last station, the one not leaving for the station
        before), or None when not exactly one kerb matches.
    """
    last = index == len(line) - 1
    neighbour = line[index - 1 if last else index + 1].stop_id
    matching = [
        kerb for kerb in parents.kerbs if neighbour in kerb.onward
    ]
    if len(matching) != 1:
        return None
    toward = matching[0]
    rest = next(kerb for kerb in parents.kerbs if kerb is not toward)
    here, other = (rest, toward) if last else (toward, rest)
    return KerbPair(
        station=parents.station,
        other=other.place,
        busy=parents.busy,
        here=here.place,
    )


def line_pairs(
    *,
    line: Sequence[StopPlace],
    pairs: Mapping[str, KerbPair],
    parents: Mapping[str, ParentKerbs],
) -> tuple[KerbPair, ...]:
    """
    Pick the kerb pair to offer at each station of a line.

    Parameters
    ----------
    line : Sequence[StopPlace]
        Stations in running order.
    pairs : Mapping[str, KerbPair]
        Facing pairs by station ID.
    parents : Mapping[str, ParentKerbs]
        Parent stations' kerbs by station ID.

    Returns
    -------
    tuple[KerbPair, ...]
        In line order, each station once, at its first call: its parent
        pair if the line orients one, else its facing pair unless the
        other kerb is the station itself, else nothing.
    """
    chosen: dict[str, KerbPair | None] = {}
    for index, station in enumerate(line):
        key = station.stop_id
        if key in chosen:
            continue
        oriented = (
            parent_pair(line=line, index=index, parents=parents[key])
            if key in parents
            else None
        )
        if oriented is None and key in pairs:
            facing = pairs[key]
            oriented = None if facing.other.stop_id == key else facing
        chosen[key] = oriented
    return tuple(pair for pair in chosen.values() if pair is not None)


def busier(*, parents: ParentKerbs) -> tuple[ParentKerb, ParentKerb]:
    """
    Order a parent's kerbs by how many trips leave them.

    Parameters
    ----------
    parents : ParentKerbs
        The parent station's kerbs.

    Returns
    -------
    tuple[ParentKerb, ParentKerb]
        The kerb with more trips first; on a tie the lower stop ID.
    """
    first, second = sorted(
        parents.kerbs,
        key=lambda kerb: (-kerb.trips, kerb.place.stop_id),
    )
    return first, second


def parent_pin(*, pin: Pin, parents: Mapping[str, ParentKerbs]) -> Pin:
    """
    Turn a plain pin on a parent station into a both-sides pin.

    Parameters
    ----------
    pin : Pin
        Pin.
    parents : Mapping[str, ParentKerbs]
        Parent stations' kerbs by station ID.

    Returns
    -------
    Pin
        The pin with the busier kerb as its From and the other as its
        opposite; the pin itself when it has a Destination, ways, an
        opposite kerb or the missing mark, or its From is not a parent.
    """
    plain = (
        pin.destination is None
        and not pin.ways
        and pin.opposite is None
        and not pin.missing
    )
    if not plain or pin.origin.stop_id not in parents:
        return pin
    here, other = busier(parents=parents[pin.origin.stop_id])
    return replace(
        pin,
        origin=here.place,
        opposite=other.place,
        towards=('', ''),
        parent=pin.origin,
    )


def unconverted(*, pin: Pin, parents: Mapping[str, ParentKerbs]) -> Pin:
    """
    Turn a converted pin back into a plain pin on its parent.

    Parameters
    ----------
    pin : Pin
        Pin.
    parents : Mapping[str, ParentKerbs]
        Parent stations' kerbs by station ID.

    Returns
    -------
    Pin
        The pin on its parent with no opposite kerb, when it was
        converted from a parent that is no longer in `parents`; the pin
        itself otherwise.
    """
    parent = pin.parent
    if parent is None or parent.stop_id in parents:
        return pin
    return replace(
        pin, origin=parent, opposite=None, towards=(), parent=None,
    )


def parent_pins(
    *, pins: tuple[Pin, ...], parents: Mapping[str, ParentKerbs]
) -> tuple[Pin, ...]:
    """
    Turn each pin on a parent station into a both-sides pin.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        Pins.
    parents : Mapping[str, ParentKerbs]
        Parent stations' kerbs by station ID.

    Returns
    -------
    tuple[Pin, ...]
        The pins in order. A pin whose converted form would have another
        pin's key is kept as it was.
    """
    keys = [pin_key(pin=pin) for pin in pins]
    result: list[Pin] = []
    for index, pin in enumerate(pins):
        changed = parent_pin(
            pin=unconverted(pin=pin, parents=parents), parents=parents,
        )
        others = keys[:index] + keys[index + 1:]
        taken = changed is not pin and pin_key(pin=changed) in others
        result.append(pin if taken else changed)
    return tuple(result)
