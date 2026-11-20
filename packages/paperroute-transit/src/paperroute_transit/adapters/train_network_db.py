"""Read a train kind's hops and weekday runs from the timetable."""

import json
from itertools import pairwise
from typing import Final

from paperroute_transit.adapters.network_lines import Fetch, Row
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun

HOP_TABLES: Final[tuple[str, ...]] = ('rail_hops', 'weekday_runs')
CALLS_AT: Final[str] = """
EXISTS (
  SELECT 1 FROM run_stops s
  WHERE s.run_id = r.run_id AND s.station_id = :station
)
"""
WEEKDAY: Final[str] = (
    'SELECT r.route, r.stations, w.trains FROM runs r '
    'JOIN weekday_runs w ON w.run_id = r.run_id '
    'WHERE r.kind = :kind AND ' + CALLS_AT + ' ORDER BY r.run_id'
)
STATIONS: Final[str] = (
    'SELECT r.stations FROM runs r WHERE r.kind = :kind AND ' + CALLS_AT
)
CALLS: Final[str] = (
    'SELECT c.stops FROM run_calls c JOIN runs r ON r.run_id = c.run_id '
    'WHERE r.kind = :kind AND ' + CALLS_AT
)
HOPS: Final[str] = """
SELECT a, b, passed, inside FROM rail_hops
WHERE a IN (SELECT value FROM json_each(:ids))
  AND b IN (SELECT value FROM json_each(:ids))
"""

def pairs_of(*, rows: list[Row]) -> set[tuple[str, str]]:
    """
    Take the consecutive station pairs of some JSON station lists.

    Parameters
    ----------
    rows : list[Row]
        Rows whose first column is a JSON array of station IDs.

    Returns
    -------
    set[tuple[str, str]]
        Every pair of neighbours.
    """
    return {
        (str(a), str(b))
        for row in rows
        for a, b in pairwise(json.loads(str(row[0])))
    }


def load_train_network(
    *, fetch: Fetch, kind: str, station: str, with_calls: bool,
) -> TrainNetwork:
    """
    Load the hops and weekday runs of a kind's runs through a station.

    Only the runs of the kind that call at the station are read. The
    hops are those between neighbouring calls and neighbouring run
    stations of those runs.

    Parameters
    ----------
    fetch : Fetch
        Runs a query; called with ``sql`` and ``params``.
    kind : str
        Mode id of the strip.
    station : str
        From station ID.
    with_calls : bool
        Whether the file has ``run_calls``.

    Returns
    -------
    TrainNetwork
        The hops and the runs that have weekday trains.
    """
    params: dict[str, object] = {'kind': kind, 'station': station}
    weekday = fetch(sql=WEEKDAY, params=params)
    runs = fetch(sql=STATIONS, params=params)
    calls = fetch(sql=CALLS, params=params) if with_calls else []
    wanted = pairs_of(rows=runs) | pairs_of(rows=calls)
    ids = {station for pair in wanted for station in pair}
    found = fetch(sql=HOPS, params={'ids': json.dumps(sorted(ids))})
    return TrainNetwork(
        hops={
            (str(a), str(b)): Hop(
                passed=tuple(json.loads(str(passed))), inside=bool(inside),
            )
            for a, b, passed, inside in found
            if (a, b) in wanted
        },
        runs=tuple(
            WeekdayRun(
                route=str(route),
                stations=tuple(json.loads(str(stations))),
                trains=int(str(trains)),
            )
            for route, stations, trains in weekday
        ),
    )
