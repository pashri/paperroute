"""Turn timetable trip rows into trip patterns."""

from datetime import date
from typing import Final

from paperroute.core.model import mode_for
from paperroute.core.patterns import PatternStop, TripPattern

JOINED_TRIPS: Final[str] = """
SELECT j.trip_id, j.next_trip FROM trip_joins j
JOIN trips n ON n.trip_id = j.next_trip
WHERE j.trip_id IN ({marks})"""


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
