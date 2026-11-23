"""Splitting a parent station's runs between its two kerbs."""

from collections.abc import Iterable

from paperroute_transit.core.reach import RunTail, StripKey
from paperroute_transit.core.sides import ParentKerbs


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
