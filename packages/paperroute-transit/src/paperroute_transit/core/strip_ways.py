"""The ring or spur path a strip vehicle's hop runs along."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.strip_badges import Badge, Spot
from paperroute_transit.core.strip_detours import DetourKind, DetourPart
from paperroute_transit.core.strip_model import StripRow
from paperroute_transit.core.strip_rings import (
    JOIN,
    LEAVE,
    Point,
    Ring,
    compass,
    groups_of,
    ring_of,
    ring_stop,
    spur_path,
    spur_places,
)

ON_STOP: Final[float] = 0.5  # px a badge may sit from a stop and cover it


@dataclass(frozen=True)
class Ways:
    """
    A strip's visible rows and the rings and spurs drawn on them.

    Attributes
    ----------
    rows : tuple[StripRow, ...]
        Visible rows.
    spots : tuple[Spot, ...]
        Each row's track spot.
    rings : Mapping[int, Ring]
        Each drawn loop's ring by detour group.
    spurs : Mapping[int, tuple[Point, ...]]
        Each drawn spur's branch by detour group, junction first.
    """

    rows: tuple[StripRow, ...]
    spots: tuple[Spot, ...]
    rings: Mapping[int, Ring]
    spurs: Mapping[int, tuple[Point, ...]]


@dataclass(frozen=True)
class RingHop:
    """
    A hop along a ring.

    Attributes
    ----------
    ring : Ring
        The ring.
    start : float
        Parameter angle in degrees where the hop starts.
    end : float
        Parameter angle in degrees where it ends.
    """

    ring: Ring
    start: float
    end: float


@dataclass(frozen=True)
class SpurHop:
    """
    A hop along a spur's branch.

    Attributes
    ----------
    start : Point
        Where the hop starts.
    end : Point
        Where it ends; the same point between a far end's two calls.
    bearing : float
        Compass bearing in degrees along the hop.
    """

    start: Point
    end: Point
    bearing: float


type Way = RingHop | SpurHop


def ways_of(
    *,
    rows: Sequence[StripRow],
    spots: Sequence[Spot],
    track_x: float,
) -> Ways:
    """
    Find the rings and spurs drawn on a strip's visible rows.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Visible rows.
    spots : Sequence[Spot]
        Each row's track spot.
    track_x : float
        The trunk's x.

    Returns
    -------
    Ways
        The rows, their spots and each whole ring and spur.
    """
    rings = {
        group: ring_of(rows=rows, group=group, track_x=track_x)
        for group in groups_of(rows=rows, kind=DetourKind.LOOP)
    }
    return Ways(
        rows=tuple(rows),
        spots=tuple(spots),
        rings={
            group: ring for group, ring in rings.items() if ring is not None
        },
        spurs={
            group: tuple(spur_path(rows=rows, group=group, track_x=track_x))
            for group in groups_of(rows=rows, kind=DetourKind.SPUR)
            if spur_places(rows=rows, group=group) is not None
        },
    )


def call_rows(
    *,
    pattern: TripPattern,
    station_of: Mapping[str, str],
    ways: Ways,
) -> list[int | None]:
    """
    Find the row each of a trip's calls is drawn on.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    station_of : Mapping[str, str]
        Strip station by stop ID.
    ways : Ways
        The strip's rows.

    Returns
    -------
    list[int | None]
        Per call, the index of its row, or None with none. A station
        called at more than once takes its rows in order, one a visit,
        staying on the last; a spur row's back call counts as its row.
    """
    seen: dict[str, int] = {}
    found: list[int | None] = []
    for stop in pattern.stops:
        station = station_of.get(stop.station, stop.station)
        rows = [
            index
            for index, row in enumerate(ways.rows)
            if station in (
                row.station, '' if row.detour is None else row.detour.back,
            )
        ]
        visit = seen.get(station, 0)
        seen[station] = visit + 1
        found.append(rows[min(visit, len(rows) - 1)] if rows else None)
    return found


def hop_ends(
    *,
    pattern: TripPattern,
    index: int,
    station_of: Mapping[str, str],
    ways: Ways,
) -> tuple[Spot | None, ...]:
    """
    Find the spots of a hop's two stops, each visit on its own row.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    index : int
        The index of the hop's first stop.
    station_of : Mapping[str, str]
        Strip station by stop ID.
    ways : Ways
        The strip's rows.

    Returns
    -------
    tuple[Spot | None, ...]
        Each stop's spot (see `call_rows`), None when not a visible row;
        one or none past the trip's last stop.
    """
    found = call_rows(pattern=pattern, station_of=station_of, ways=ways)
    return tuple(
        None if row is None else ways.spots[row]
        for row in found[index:index + 2]
    )


def ring_angle(*, ring: Ring, row: StripRow) -> float:
    """
    Give the parameter angle of a stop on a ring's right side.

    Parameters
    ----------
    ring : Ring
        The ring.
    row : StripRow
        A row on it.

    Returns
    -------
    float
        Degrees, between -90 at the top and 90 at the bottom.
    """
    share = max(-1.0, min(1.0, (row.y - ring.cy) / ring.r))
    return math.degrees(math.asin(share))


def ring_hop(*, ways: Ways, start: StripRow, end: StripRow) -> Way | None:
    """
    Find the ring a hop between two rows runs along.

    Parameters
    ----------
    ways : Ways
        The strip's rings.
    start : StripRow
        The hop's first row.
    end : StripRow
        Its second row.

    Returns
    -------
    Way | None
        The ring from the stop or the trunk at `LEAVE` to the stop or
        the trunk at `JOIN`, when either row is on a drawn ring; else
        None.
    """
    group = next(
        (
            row.detour.group
            for row in (start, end)
            if row.detour is not None
            and row.detour.kind is DetourKind.LOOP
            and row.detour.group in ways.rings
        ),
        None,
    )
    if group is None:
        return None
    ring = ways.rings[group]
    return RingHop(
        ring=ring,
        start=hop_angle(ring=ring, group=group, row=start, off=LEAVE),
        end=hop_angle(ring=ring, group=group, row=end, off=JOIN - 360),
    )


def hop_angle(*, ring: Ring, group: int, row: StripRow, off: float
              ) -> float:
    """
    Give where a hop's end row sits on a ring.

    Parameters
    ----------
    ring : Ring
        The ring.
    group : int
        Its detour group.
    row : StripRow
        The end row.
    off : float
        Parameter angle in degrees for a row off the ring, where the
        trunk meets it.

    Returns
    -------
    float
        The row's stop angle on the ring, or `off`.
    """
    on = row.detour is not None and row.detour.group == group
    return ring_angle(ring=ring, row=row) if on else off


def on_spur(*, ways: Ways, parts: tuple[DetourPart, DetourPart]) -> bool:
    """
    Tell whether two rows' detour parts are on one drawn spur.

    Parameters
    ----------
    ways : Ways
        The strip's spurs.
    parts : tuple[DetourPart, DetourPart]
        The two rows' parts.

    Returns
    -------
    bool
        True when both are on the same spur and it is drawn.
    """
    one, two = parts
    return (
        one.kind is two.kind is DetourKind.SPUR
        and one.group == two.group
        and one.group in ways.spurs
    )


def spur_hop(*, ways: Ways, start: StripRow, end: StripRow) -> Way | None:
    """
    Find the spur branch a hop between two rows runs along.

    Parameters
    ----------
    ways : Ways
        The strip's spurs.
    start : StripRow
        The hop's first row.
    end : StripRow
        Its second row.

    Returns
    -------
    Way | None
        The stretch of branch between the two rows' levels, heading out
        when the level rises and back otherwise, when both rows are on
        one drawn spur; else None.
    """
    one, two = start.detour, end.detour
    if one is None or two is None or not on_spur(ways=ways, parts=(one, two)):
        return None
    path = ways.spurs[one.group]
    near, far = path[one.slot], path[two.slot]
    if one.slot == two.slot:
        far = path[max(0, one.slot - 1)]
        return SpurHop(
            start=near, end=near,
            bearing=compass(dx=far[0] - near[0], dy=far[1] - near[1]),
        )
    return SpurHop(
        start=near, end=far,
        bearing=compass(dx=far[0] - near[0], dy=far[1] - near[1]),
    )


def hop_way(
    *,
    pattern: TripPattern,
    index: int,
    station_of: Mapping[str, str],
    ways: Ways,
) -> Way | None:
    """
    Find the ring or spur a trip's hop runs along.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    index : int
        The index of the hop's first stop.
    station_of : Mapping[str, str]
        Strip station by stop ID.
    ways : Ways
        The strip's rows, rings and spurs.

    Returns
    -------
    Way | None
        The hop's ring or spur stretch, or None on the trunk or with an
        end not on a visible row.
    """
    found = call_rows(pattern=pattern, station_of=station_of, ways=ways)
    ends = found[index:index + 2]
    if len(ends) != 2 or ends[0] is None or ends[1] is None:
        return None
    start, end = ways.rows[ends[0]], ways.rows[ends[1]]
    return (
        ring_hop(ways=ways, start=start, end=end)
        or spur_hop(ways=ways, start=start, end=end)
    )


def way_point(*, way: Way, fraction: float) -> tuple[Point, float]:
    """
    Give the point part way along a hop's ring or spur stretch.

    Parameters
    ----------
    way : Way
        The ring or spur stretch.
    fraction : float
        Share of the hop run.

    Returns
    -------
    tuple[Point, float]
        The point and the compass bearing of travel there.
    """
    if isinstance(way, RingHop):
        angle = way.start + (way.end - way.start) * fraction
        return way.ring.at(angle=angle), way.ring.bearing(angle=angle)
    (x0, y0), (x1, y1) = way.start, way.end
    return (x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction), way.bearing


def way_badge(
    *, look: Marker, way: Way, fraction: float, known: bool,
) -> Badge:
    """
    Make a badge part way along a hop's ring or spur stretch.

    Parameters
    ----------
    look : Marker
        The badge's route, age and liveness.
    way : Way
        The ring or spur stretch.
    fraction : float
        Share of the hop run.
    known : bool
        False when the vehicle has no next stop.

    Returns
    -------
    Badge
        A badge fixed on its path (see `pushed`), its wedge along the
        direction of travel when known.
    """
    (x, y), bearing = way_point(way=way, fraction=fraction)
    return Badge(
        marker=replace(look, x=x, y=y, bearing=bearing if known else None),
        low=y,
        high=y,
        spur=True,
    )


def covered_from(
    *,
    rows: Sequence[StripRow],
    markers: Sequence[Marker],
    ways: Ways,
) -> tuple[StripRow, ...]:
    """
    Hide the From's disc on a ring where a bus badge sits on it.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Visible rows, as in `ways`.
    markers : Sequence[Marker]
        The strip's badges.
    ways : Ways
        The strip's rings.

    Returns
    -------
    tuple[StripRow, ...]
        The rows, the From marked covered when it is on a drawn ring and
        a badge is within `ON_STOP` of its stop; it keeps its bold name
        and time.
    """
    return tuple(
        (
            replace(row, detour=replace(row.detour, covered=True))
            if row.detour is not None
            and under_badge(row=row, markers=markers, ways=ways)
            else row
        )
        for row in rows
    )


def under_badge(
    *, row: StripRow, markers: Sequence[Marker], ways: Ways,
) -> bool:
    """
    Tell whether a From on a ring has a badge on its stop.

    Parameters
    ----------
    row : StripRow
        A row.
    markers : Sequence[Marker]
        The strip's badges.
    ways : Ways
        The strip's rings.

    Returns
    -------
    bool
        True for the From on a drawn ring with a badge within `ON_STOP`
        of its stop.
    """
    if not row.bold or row.detour is None:
        return False
    ring = ways.rings.get(row.detour.group)
    if row.detour.kind is not DetourKind.LOOP or ring is None:
        return False
    x, y = ring_stop(ring=ring, row=row)
    return any(
        math.hypot(each.x - x, each.y - y) <= ON_STOP for each in markers
    )
