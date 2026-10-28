"""Turn timetable departure rows into Departures."""

from datetime import date
from typing import Final

from paperroute.core.model import (
    CarryOn,
    Departure,
    TripFacts,
    kind_for,
    mode_for,
    service_time,
)

STILL_AT_FROM: Final[str] = """
  AND NOT EXISTS (SELECT 1 FROM stop_times n
                  WHERE n.trip_id = o.trip_id AND n.pickup <> 1
                    AND n.seq = (SELECT MIN(q.seq) FROM stop_times q
                                 WHERE q.trip_id = o.trip_id
                                   AND q.seq > o.seq)
                    AND n.stop_id IN (SELECT stop_id FROM stops
                                      WHERE stop_id = :origin
                                        OR parent_station = :origin))
"""
STATION_OF: Final[str] = "COALESCE(NULLIF(s.parent_station, ''), s.stop_id)"


def stop_count(*, trip: str, after: str, upto: str | None = None) -> str:
    """
    Make SQL counting the stations a trip stops at within a seq range.

    Calls that neither pick up nor set down are passed through and not
    counted. Neighbouring calls at the same station count once, and a
    call at the station of the call the range starts after not at all.

    Parameters
    ----------
    trip : str
        SQL for the trip ID.
    after : str
        SQL for the seq the range starts after.
    upto : str | None
        SQL for the last seq in the range, or None for the trip's end.

    Returns
    -------
    str
        A bracketed scalar subquery.
    """
    end = '' if upto is None else f' AND c.seq <= {upto}'
    return f"""(SELECT COUNT(*) FROM (
  SELECT {STATION_OF} AS here,
    LAG({STATION_OF}, 1, (
      SELECT {STATION_OF} FROM stop_times f
      JOIN stops s ON s.stop_id = f.stop_id
      WHERE f.trip_id = {trip} AND f.seq = {after}
    )) OVER (ORDER BY c.seq) AS before
  FROM stop_times c JOIN stops s ON s.stop_id = c.stop_id
  WHERE c.trip_id = {trip} AND c.seq > {after}{end}
    AND NOT (c.pickup = 1 AND c.dropoff = 1))
 WHERE before IS NULL OR here <> before)"""


def to_departure(*, row: tuple[object, ...], day: date) -> Departure:
    """
    Turn a query row into a Departure.

    Parameters
    ----------
    row : tuple[object, ...]
        DEPARTURES or WHOLE_STOP row.
    day : date
        Service date the row's times count from.

    Returns
    -------
    Departure
        The departure.
    """
    (trip, route, route_type, agency, headsign, platform, oseq, ostop,
     dseq, dstop, dep, arr, aliases) = row[:13]
    return Departure(
        trip_id=str(trip),
        aliases=tuple(sorted(str(aliases).split())) if aliases else (),
        route=str(route),
        mode=mode_for(
            route_type=int(str(route_type)),
            agency_id=str(agency) if agency else None,
        ),
        headsign=str(headsign),
        platform=str(platform),
        origin_seq=int(str(oseq)),
        origin_stop=str(ostop),
        destination_seq=int(str(dseq)),
        destination_stop=str(dstop),
        departs=service_time(day=day, seconds=int(str(dep))),
        arrives=service_time(day=day, seconds=int(str(arr))),
        service_day=day,
        shape_id=str(row[13]) if row[13] else None,
        facts=facts_of(row=row),
        stops=(
            int(str(row[17]))
            if len(row) > 17 and row[17] is not None
            else None
        ),
    )


def facts_of(*, row: tuple[object, ...]) -> TripFacts:
    """
    Read a departure row's route kind and carry-on.

    Parameters
    ----------
    row : tuple[object, ...]
        DEPARTURES or WHOLE_STOP row.

    Returns
    -------
    TripFacts
        Kind and ID of the route and the service the vehicle runs next,
        None when the row has none.
    """
    route, route_type, agency = row[1:4]
    then, then_to, then_trip = row[14:17]
    return TripFacts(
        route_id=str(row[18]) if len(row) > 18 else '',
        kind=kind_for(
            route_type=int(str(route_type)),
            agency_id=str(agency) if agency else None,
            short_name=str(route),
        ),
        then=(
            None
            if then is None
            else CarryOn(
                route=str(then),
                headsign=str(then_to),
                trip_id=str(then_trip) if then_trip else '',
            )
        ),
    )


DEPARTURES: Final[str] = """
SELECT t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  t.headsign, os.platform,
  o.seq, o.stop_id, MIN(d.seq), d.stop_id, o.dep, d.arr,
  (SELECT group_concat(a.alias_id, ' ') FROM trip_aliases a
     WHERE a.trip_id = t.trip_id),
  {shape}, {then},
  """ + stop_count(trip='o.trip_id', after='o.seq', upto='d.seq') + """,
  t.route_id
FROM stop_times o
JOIN trips t ON t.trip_id = o.trip_id
JOIN services sv ON sv.service_id = t.service_id AND sv.date = :day
JOIN routes r ON r.route_id = t.route_id
JOIN stops os ON os.stop_id = o.stop_id
JOIN stop_times d ON d.trip_id = o.trip_id AND d.seq > o.seq
{next_join}
WHERE o.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :origin OR parent_station = :origin)
  AND d.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :dest OR parent_station = :dest)
  AND o.dep BETWEEN :lo AND :hi AND o.pickup <> 1 AND d.dropoff <> 1
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = :day)
  {service}
""" + STILL_AT_FROM + """
GROUP BY o.trip_id, o.seq
"""
WHOLE_STOP: Final[str] = """
SELECT t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  t.headsign, os.platform,
  o.seq, o.stop_id, d.seq, d.stop_id, o.dep, d.arr,
  (SELECT group_concat(a.alias_id, ' ') FROM trip_aliases a
     WHERE a.trip_id = t.trip_id),
  {shape}, {then}, NULL, t.route_id
FROM stop_times o
JOIN trips t ON t.trip_id = o.trip_id
JOIN services sv ON sv.service_id = t.service_id AND sv.date = :day
JOIN routes r ON r.route_id = t.route_id
JOIN stops os ON os.stop_id = o.stop_id
JOIN stop_times d ON d.trip_id = o.trip_id
  AND d.seq = (SELECT MAX(l.seq) FROM stop_times l
               WHERE l.trip_id = o.trip_id)
{next_join}
WHERE o.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :origin OR parent_station = :origin)
  AND d.seq > o.seq
  AND o.dep BETWEEN :lo AND :hi AND o.pickup <> 1
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = :day)
  {service}
""" + STILL_AT_FROM
