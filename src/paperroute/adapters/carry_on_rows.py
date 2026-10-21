"""
Departures that reach the Destination by staying on as another trip.

Leaving at the station where two trips meet means leaving the earlier
one, so a joined trip's first call never counts as reaching the
Destination.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Final

from paperroute.adapters.departure_rows import STILL_AT_FROM, to_departure
from paperroute.core.model import CarryOn, Departure

MAX_HOPS: Final[int] = 6
STOPS_OF: Final[str] = """(SELECT stop_id FROM stops
                    WHERE stop_id = :{end} OR parent_station = :{end})"""
AT_DEST: Final[str] = STOPS_OF.format(end='dest')
AFTER_JOIN: Final[str] = """
  AND {call}.seq > (SELECT MIN(f.seq) FROM stop_times f
                    WHERE f.trip_id = {call}.trip_id)"""
REACHES: Final[str] = """
EXISTS (SELECT 1 FROM stop_times x
        WHERE x.trip_id = j.next_trip AND x.dropoff <> 1
          AND x.stop_id IN """ + AT_DEST + AFTER_JOIN.format(call='x') + ')'
CARRY_ONS: Final[str] = """
WITH RECURSIVE boarded(trip_id, seq) AS (
  SELECT o.trip_id, o.seq FROM stop_times o
  JOIN trips t ON t.trip_id = o.trip_id
  JOIN services sv ON sv.service_id = t.service_id AND sv.date = :day
  WHERE o.stop_id IN """ + STOPS_OF.format(end='origin') + """
    AND o.dep BETWEEN :lo AND :hi AND o.pickup <> 1
    AND NOT EXISTS (SELECT 1 FROM trip_skip k
                    WHERE k.trip_id = t.trip_id AND k.date = :day)
    {service}
    AND NOT EXISTS (SELECT 1 FROM stop_times x
                    WHERE x.trip_id = o.trip_id AND x.seq > o.seq
                      AND x.dropoff <> 1 AND x.stop_id IN """ + AT_DEST + """)
""" + STILL_AT_FROM + """),
chain(trip_id, seq, onto, hops, reached) AS (
  SELECT b.trip_id, b.seq, b.trip_id, 0, 0 FROM boarded b
  UNION ALL
  SELECT c.trip_id, c.seq, j.next_trip, c.hops + 1, """ + REACHES + """
  FROM chain c
  JOIN trip_joins j ON j.trip_id = c.onto
  JOIN trips n ON n.trip_id = j.next_trip
  JOIN services nv ON nv.service_id = n.service_id AND nv.date = :day
  WHERE c.hops < """ + str(MAX_HOPS) + """ AND NOT c.reached
    AND n.service = 1
    AND NOT EXISTS (SELECT 1 FROM trip_skip k
                    WHERE k.trip_id = n.trip_id AND k.date = :day)
)
SELECT t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  t.headsign, os.platform,
  o.seq, o.stop_id, l.seq, l.stop_id, o.dep, MIN(d.arr),
  (SELECT group_concat(a.alias_id, ' ') FROM trip_aliases a
     WHERE a.trip_id = t.trip_id),
  {shape}, nr.short_name, n.headsign, n.trip_id
FROM chain c
JOIN stop_times d ON d.trip_id = c.onto AND d.dropoff <> 1
  AND d.stop_id IN """ + AT_DEST + AFTER_JOIN.format(call='d') + """
JOIN stop_times o ON o.trip_id = c.trip_id AND o.seq = c.seq
JOIN trips t ON t.trip_id = c.trip_id
JOIN routes r ON r.route_id = t.route_id
JOIN stops os ON os.stop_id = o.stop_id
JOIN stop_times l ON l.trip_id = t.trip_id
  AND l.seq = (SELECT MAX(m.seq) FROM stop_times m
               WHERE m.trip_id = t.trip_id)
JOIN trips n ON n.trip_id = c.onto
JOIN routes nr ON nr.route_id = n.route_id
WHERE c.reached AND d.arr > o.dep
GROUP BY c.trip_id, c.seq
"""


def carry_on_departure(*, row: tuple[object, ...], day: date) -> Departure:
    """
    Turn a CARRY_ONS row into a Departure.

    The departure is the boarded trip's, running to its last call, so
    realtime for the boarded trip moves its arrival. Only ``arrives`` is
    the Destination arrival on the trip the vehicle becomes.

    Parameters
    ----------
    row : tuple[object, ...]
        CARRY_ONS row.
    day : date
        Service date the row's times count from.

    Returns
    -------
    Departure
        The departure, with ``facts.onto`` naming the trip it becomes.
    """
    onto, onto_to, onto_trip = row[14:17]
    departure = to_departure(row=(*row[:14], None, None, None), day=day)
    return replace(
        departure,
        facts=replace(
            departure.facts,
            onto=CarryOn(
                route=str(onto), headsign=str(onto_to),
                trip_id=str(onto_trip),
            ),
        ),
    )


def first_to_arrive(*, departures: Sequence[Departure]) -> list[Departure]:
    """
    Drop carry-ons that a direct trip leaving no earlier beats.

    Parameters
    ----------
    departures : Sequence[Departure]
        Direct and carry-on departures for one From and Destination.

    Returns
    -------
    list[Departure]
        The departures, less each carry-on for which a direct one leaves
        at the same time or later and arrives no later.
    """
    direct = [d for d in departures if d.facts.onto is None]
    return [
        departure
        for departure in departures
        if departure.facts.onto is None
        or not any(
            other.departs >= departure.departs
            and other.arrives <= departure.arrives
            for other in direct
        )
    ]
