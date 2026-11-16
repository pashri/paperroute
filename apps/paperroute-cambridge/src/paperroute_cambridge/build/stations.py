"""Group stops that share a name into one station."""

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StationSpec:
    """
    A station made from named stops.

    Attributes
    ----------
    station_id : str
        Id of the parent stop to add.
    name : str
        Name of the parent stop.
    member_names : tuple[str, ...]
        Exact names of the stops that belong to it.
    id_prefix : str
        Prefix the member stop ids must start with.
    """

    station_id: str
    name: str
    member_names: tuple[str, ...]
    id_prefix: str


def _members(
    *,
    conn: sqlite3.Connection,
    spec: StationSpec,
) -> list[tuple[str, float, float]]:
    """
    Find the stops that belong to a station.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    spec : StationSpec
        The station.

    Returns
    -------
    list[tuple[str, float, float]]
        Stop id, longitude and latitude of each member.
    """
    marks = ', '.join('?' * len(spec.member_names))
    rows = conn.execute(
        'SELECT stop_id, lon, lat FROM stops '
        f'WHERE name IN ({marks}) AND substr(stop_id, 1, ?) = ? '
        'AND lon IS NOT NULL AND lat IS NOT NULL',
        (*spec.member_names, len(spec.id_prefix), spec.id_prefix),
    )
    return list(rows)


def _add_station(
    *,
    conn: sqlite3.Connection,
    spec: StationSpec,
    members: Sequence[tuple[str, float, float]],
) -> None:
    """
    Add the parent stop at the members' mean and point them at it.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    spec : StationSpec
        The station.
    members : Sequence[tuple[str, float, float]]
        Stop id, longitude and latitude of each member.
    """
    lon = sum(member[1] for member in members) / len(members)
    lat = sum(member[2] for member in members) / len(members)
    conn.execute(
        "INSERT INTO stops VALUES (?, ?, NULL, ?, ?, '')",
        (spec.station_id, spec.name, lat, lon),
    )
    conn.executemany(
        'UPDATE stops SET parent_station = ? WHERE stop_id = ?',
        [(spec.station_id, member[0]) for member in members],
    )


def group_stations(
    *,
    conn: sqlite3.Connection,
    stations: Sequence[StationSpec],
) -> int:
    """
    Give each station's stops one parent stop.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    stations : Sequence[StationSpec]
        The stations to make.

    Returns
    -------
    int
        Stops given a parent. A station with no member is logged and
        skipped.
    """
    grouped = 0
    for spec in stations:
        members = _members(conn=conn, spec=spec)
        if not members:
            logger.warning('no stop found for station %s', spec.name)
            continue
        _add_station(conn=conn, spec=spec, members=members)
        grouped += len(members)
    conn.commit()
    return grouped
