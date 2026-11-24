"""The SQL that lists a route's trips on a service day."""

from typing import Final

ROUTE_TRIPS: Final[str] = """
SELECT t.trip_id, sv.date, MIN(st.dep), MAX(st.arr), t.direction_id,
  r.type, COALESCE(t.agency_id, r.agency_id)
FROM trips t
JOIN routes r ON r.route_id = t.route_id
JOIN services sv ON sv.service_id = t.service_id
JOIN stop_times st ON st.trip_id = t.trip_id
WHERE r.short_name = ? AND sv.date IN (?, ?)
  {service}
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = sv.date)
GROUP BY t.trip_id, sv.date
ORDER BY sv.date, MIN(st.dep), t.trip_id
"""
