"""Split each rail route into the named lines its trains run on."""

import sqlite3
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class RailLine:
    """
    A named line of one operator.

    Attributes
    ----------
    badge : str
        Operator badge, the route's short name.
    name : str
        Name of the line.
    marks : frozenset[str]
        Ids of the stations that put a trip on this line.
    """

    badge: str
    name: str
    marks: frozenset[str]


def _line(*, badge: str, name: str, marks: tuple[str, ...]) -> RailLine:
    """
    Make a line from the CRS codes of its marks.

    Parameters
    ----------
    badge : str
        Operator badge.
    name : str
        Name of the line.
    marks : tuple[str, ...]
        CRS codes of its marks.

    Returns
    -------
    RailLine
        The line.
    """
    return RailLine(
        badge=badge,
        name=name,
        marks=frozenset(f'rail:{crs}' for crs in marks),
    )


RAIL_LINES: Final[tuple[RailLine, ...]] = (
    _line(badge='GN', name='Kings Lynn & Kings Cross', marks=('KLN', 'KGX')),
    _line(badge='TL', name='Brighton via St Pancras', marks=('BTN', 'STP')),
    _line(badge='GA', name='Liverpool Street', marks=('LST',)),
    _line(badge='GA', name='Norwich', marks=('NRW',)),
    _line(badge='GA', name='Ipswich', marks=('IPS',)),
    _line(badge='GA', name='Stansted Airport', marks=('SSD', 'NRW')),
    _line(badge='XC', name='Birmingham & Stansted', marks=('BHM', 'SSD')),
    _line(badge='EM', name='Norwich & Liverpool', marks=('NRW', 'LIV')),
    _line(badge='GA', name='Ipswich & Peterborough', marks=('IPS', 'PBO')),
)


def line_for(
    *,
    badge: str,
    stations: Collection[str],
    lines: Sequence[RailLine],
) -> RailLine | None:
    """
    Pick the line of an operator that a trip's stations mark most.

    Parameters
    ----------
    badge : str
        The trip's route badge.
    stations : Collection[str]
        Ids of the stations the trip calls at.
    lines : Sequence[RailLine]
        The declared lines, in chooser order.

    Returns
    -------
    RailLine | None
        The operator's line with the most marks among the stations. A tie
        goes to the line listed first, so a trip reaching no mark takes
        the operator's first line. None when the operator has no line.
    """
    own = [line for line in lines if line.badge == badge]
    if not own:
        return None
    return max(own, key=lambda line: len(line.marks & set(stations)))


def _trip_stations(
    *,
    conn: sqlite3.Connection,
    source: str,
) -> dict[str, tuple[str, str, set[str]]]:
    """
    Read each trip's route, badge and stations.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    source : str
        Source whose trips are read.

    Returns
    -------
    dict[str, tuple[str, str, set[str]]]
        Route id, badge and station ids by trip id.
    """
    rows = conn.execute(
        'SELECT t.trip_id, t.route_id, r.short_name,'
        " COALESCE(NULLIF(s.parent_station, ''), s.stop_id)"
        ' FROM trips t JOIN routes r ON r.route_id = t.route_id'
        ' JOIN stop_times st ON st.trip_id = t.trip_id'
        ' JOIN stops s ON s.stop_id = st.stop_id'
        ' WHERE t.source = ?',
        (source,),
    )
    found: dict[str, tuple[str, str, set[str]]] = {}
    for trip_id, route_id, badge, station in rows:
        found.setdefault(trip_id, (route_id, badge, set()))[2].add(station)
    return found


def _add_route(
    *,
    conn: sqlite3.Connection,
    route_id: str,
    line: RailLine,
) -> str:
    """
    Copy a route as the route of one line.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    route_id : str
        Route to copy.
    line : RailLine
        The line the copy carries as its long name.

    Returns
    -------
    str
        Id of the copy.
    """
    new_id = f'{route_id}:{line.name}'
    conn.execute(
        'INSERT OR IGNORE INTO routes (route_id, short_name, long_name,'
        ' type, color, text_color, agency_id, source)'
        ' SELECT ?, short_name, ?, type, color, text_color, agency_id,'
        ' source FROM routes WHERE route_id = ?',
        (new_id, line.name, route_id),
    )
    return new_id


def assign_rail_lines(
    *,
    conn: sqlite3.Connection,
    lines: Sequence[RailLine],
    source: str,
) -> int:
    """
    Move each trip of a source onto the route of its line.

    Runs in the caller's transaction. A route left with no trips is
    deleted.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    lines : Sequence[RailLine]
        The declared lines, in chooser order.
    source : str
        Source whose trips are moved.

    Returns
    -------
    int
        Trips given a line.
    """
    moves: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for trip_id, (route_id, badge, stations) in _trip_stations(
        conn=conn, source=source,
    ).items():
        line = line_for(badge=badge, stations=stations, lines=lines)
        if line is not None:
            new_id = _add_route(conn=conn, route_id=route_id, line=line)
            moves[new_id].append((new_id, trip_id))
    conn.executemany(
        'UPDATE trips SET route_id = ? WHERE trip_id = ?',
        [row for rows in moves.values() for row in rows],
    )
    conn.execute(
        'DELETE FROM routes WHERE source = ? AND route_id NOT IN'
        ' (SELECT route_id FROM trips)',
        (source,),
    )
    return sum(len(rows) for rows in moves.values())


def rail_line_sort(*, lines: Sequence[RailLine]) -> dict[str, int]:
    """
    Rank the lines in chooser order.

    Parameters
    ----------
    lines : Sequence[RailLine]
        The declared lines, in chooser order.

    Returns
    -------
    dict[str, int]
        Position from 1, by badge and name joined with a colon.
    """
    return {
        f'{line.badge}:{line.name}': rank
        for rank, line in enumerate(lines, start=1)
    }


def rail_line_groups(*, lines: Sequence[RailLine]) -> dict[str, int]:
    """
    Number the operators by their first line.

    Parameters
    ----------
    lines : Sequence[RailLine]
        The declared lines, in chooser order.

    Returns
    -------
    dict[str, int]
        Index of each badge's first appearance.
    """
    badges = dict.fromkeys(line.badge for line in lines)
    return {badge: index for index, badge in enumerate(badges)}
