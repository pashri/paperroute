"""A strip's ring when the From is on a loop."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_detours import (
    Detour,
    DetourKind,
    DetourPart,
    detours_of,
)
from paperroute_transit.core.strip_model import StripRow


def loop_round(
    *,
    pattern: TripPattern,
    station: str,
    station_of: Mapping[str, str],
) -> tuple[Detour, int] | None:
    """
    Find the loop a trip runs with a station on its ring.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    station : str
        The From's strip station.
    station_of : Mapping[str, str]
        Strip station by stop ID.

    Returns
    -------
    tuple[Detour, int] | None
        The loop and the From's call index, or None when the From is on
        no loop's ring.
    """
    calls = [
        station_of.get(stop.station, stop.station) for stop in pattern.stops
    ]
    return next(
        (
            (detour, index)
            for detour in detours_of(pattern=pattern)
            if detour.kind is DetourKind.LOOP
            for index in range(detour.first + 1, detour.last)
            if calls[index] == station
        ),
        None,
    )


def ring_order(
    *, rows: Sequence[StripRow], detour: Detour, group: int,
) -> list[StripRow]:
    """
    Reorder a loop's rows as the ring draws them.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The loop's rows in calling order, both stands included.
    detour : Detour
        The loop.
    group : int
        Its detour group.

    Returns
    -------
    list[StripRow]
        The leaving stand, the ring's calls last first, then the stand
        returned to, each in the y slots the rows held.
    """
    size = detour.last - detour.first - 1
    ring = [
        replace(
            rows[slot + 1],
            detour=DetourPart(
                kind=DetourKind.LOOP, group=group, slot=slot, size=size,
            ),
        )
        for slot in reversed(range(size))
    ]
    order = [rows[0], *ring, rows[-1]]
    return [
        replace(row, y=rows[place].y) for place, row in enumerate(order)
    ]


def from_loop(
    *,
    rows: Sequence[StripRow],
    pattern: TripPattern,
    station_of: Mapping[str, str],
) -> tuple[StripRow, ...]:
    """
    Lay a loop round the From out as a ring group across it.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Visible rows in calling order, the From among them.
    pattern : TripPattern
        The trip the rows follow.
    station_of : Mapping[str, str]
        Strip station by stop ID.

    Returns
    -------
    tuple[StripRow, ...]
        The rows, with a loop whose ring holds the From laid out like a
        loop below it (see `loop_rows`); unchanged when the From is on
        no loop's ring or the loop's rows are not all shown in order.
    """
    origin = next(
        (i for i, row in enumerate(rows) if row.kind is RowKind.ORIGIN), None,
    )
    found = None if origin is None else loop_round(
        pattern=pattern, station=rows[origin].station, station_of=station_of,
    )
    if origin is None or found is None:
        return tuple(rows)
    detour, at = found
    start = origin - (at - detour.first)
    span = rows[max(start, 0):start + detour.last - detour.first + 1]
    calls = [
        station_of.get(stop.station, stop.station)
        for stop in pattern.stops[detour.first:detour.last + 1]
    ]
    if start < 0 or [row.station for row in span] != calls:
        return tuple(rows)
    group = 1 + max(
        (row.detour.group for row in rows if row.detour is not None),
        default=-1,
    )
    return (
        *rows[:start],
        *ring_order(rows=span, detour=detour, group=group),
        *rows[start + len(span):],
    )
