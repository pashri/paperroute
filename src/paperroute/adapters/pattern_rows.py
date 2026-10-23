"""Turn timetable trip rows into trip patterns."""

from datetime import date
from typing import Final

from paperroute.core.model import mode_for
from paperroute.core.patterns import PatternStop, TripPattern

JOINED_TRIPS: Final[str] = """
SELECT j.trip_id, j.next_trip FROM trip_joins j
JOIN trips n ON n.trip_id = j.next_trip
WHERE j.trip_id IN ({marks})"""
LABELS_BY_TRIP: Final[str] = """
SELECT t.trip_id, r.short_name FROM trips t
JOIN routes r ON r.route_id = t.route_id
WHERE t.trip_id IN ({marks})
"""
LABELS_BY_ALIAS: Final[str] = """
SELECT a.alias_id, r.short_name FROM trip_aliases a
JOIN trips t ON t.trip_id = a.trip_id
JOIN routes r ON r.route_id = t.route_id
WHERE a.alias_id IN ({marks})
ORDER BY a.alias_id, t.trip_id
"""
RUNNING_DAYS: Final[str] = """
SELECT t.trip_id, sv.date FROM trips t
JOIN services sv ON sv.service_id = t.service_id
WHERE sv.date IN (?, ?) AND t.trip_id IN ({marks})
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = sv.date)
ORDER BY t.trip_id, sv.date
"""
PATTERN_STOPS: Final[str] = """
SELECT st.trip_id, st.arr, st.dep, s.lon, s.lat,
  COALESCE(NULLIF(s.parent_station, ''), s.stop_id),
  COALESCE(p.name, s.name), st.pickup = 1 AND st.dropoff = 1
FROM stop_times st
JOIN stops s ON s.stop_id = st.stop_id
LEFT JOIN stops p ON p.stop_id = NULLIF(s.parent_station, '')
WHERE st.trip_id IN ({marks})
ORDER BY st.trip_id, st.seq
"""


def pattern_of(
    *,
    requested: str,
    rows: list[tuple[object, ...]],
    days: dict[str, list[date]],
    calls: tuple[dict[str, list[PatternStop]], dict[str, str]],
) -> TripPattern:
    """
    Build the pattern of a requested ID from its candidate trips.

    Parameters
    ----------
    requested : str
        The requested trip ID or alias.
    rows : list[tuple[object, ...]]
        Candidate trip rows, preferred first.
    days : dict[str, list[date]]
        Running dates by trip ID.
    calls : tuple[dict[str, list[PatternStop]], dict[str, str]]
        Stops by trip ID, and the trip each runs on through as.

    Returns
    -------
    TripPattern
        The pattern; its dates are the union over all candidates, and the
        rest comes from the first candidate that runs on any of them.
    """
    running = sorted({d for row in rows for d in days.get(str(row[1]), ())})
    row = next((r for r in rows if days.get(str(r[1]))), rows[0])
    return TripPattern(
        trip_id=requested,
        route=str(row[2]),
        mode=mode_for(
            route_type=int(str(row[3])),
            agency_id=str(row[4]) if row[4] else None,
        ),
        shape_id=str(row[5]) if row[5] else None,
        stops=tuple(calls[0].get(str(row[1]), ())),
        days=tuple(running),
        next_trip=calls[1].get(str(row[1])),
    )
