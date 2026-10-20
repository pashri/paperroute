"""The note on the station where a strip run carries on as another route."""

from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.reach import Reach, RowKind
from paperroute.core.stop_view import not_departed
from paperroute.core.strip_model import CarryNote
from paperroute.core.strip_times import TripInputs, live_strip

NOTE_KINDS: Final[frozenset[RowKind]] = frozenset({
    RowKind.MAIN, RowKind.BRANCH,
})


def change_row(
    *, item: LiveDeparture, trips: TripInputs, reach: Reach,
) -> int | None:
    """
    Find the row where a departure's run carries on as another route.

    Parameters
    ----------
    item : LiveDeparture
        A strip departure.
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach.

    Returns
    -------
    int | None
        The main or branch row of its pattern's last station; None when
        it is cancelled, doesn't carry on, has no loaded pattern or ends
        on no such row.
    """
    departure = item.departure
    found = trips.patterns.get(departure.trip_id)
    if item.cancelled or departure.facts.then is None:
        return None
    if found is None or not found.stops:
        return None
    last = found.stops[-1].station
    station = reach.station_of.get(last, last)
    return next(
        (
            index
            for index, row in enumerate(reach.rows)
            if row.place.stop_id == station and row.kind in NOTE_KINDS
        ),
        None,
    )


def carry_note(
    *, trips: TripInputs, reach: Reach,
) -> tuple[int, CarryNote] | None:
    """
    Place the note for the next strip run that carries on as another route.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach.

    Returns
    -------
    tuple[int, CarryNote] | None
        The change row and note of the first departure, in live order,
        not yet gone and not cancelled, whose loaded run ends on a main
        or branch row and carries on; None when there is none.
    """
    coming = not_departed(departures=live_strip(trips=trips), now=trips.now)
    for item in coming:
        row = change_row(item=item, trips=trips, reach=reach)
        then = item.departure.facts.then
        if row is not None and then is not None:
            return row, CarryNote(
                route=item.departure.route,
                then=f'{then.route} {then.headsign}',
            )
    return None
