"""The times and carry-on note of a train Strip's rows."""

from datetime import date

from paperroute_device.core.text import clock_text
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.reach import Reach, RowKind
from paperroute_transit.core.strip_follow import change_at, origin_of
from paperroute_transit.core.strip_model import CarryNote
from paperroute_transit.core.strip_times import (
    TripInputs,
    earlier_times,
    trip_times,
)


def is_train(*, reach: Reach) -> bool:
    """
    Say whether a reach is laid out along followed trains.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    bool
        True for a train Strip's reach with a followed train.
    """
    return reach.source is not None and reach.trip is not None


def trip_item(
    *,
    trips: TripInputs,
    trip: str | None,
    day: date | None,
    seq: int | None = None,
) -> LiveDeparture | None:
    """
    Find the strip's departure of a trip on a service day.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    trip : str | None
        The trip ID, or None.
    day : date | None
        The service date it runs on.
    seq : int | None
        The stop sequence it is boarded at; None matches any.

    Returns
    -------
    LiveDeparture | None
        The first departure of that trip, day and sequence; None when it
        is not among them.
    """
    return next(
        (
            item
            for item in trips.departures
            if trip is not None
            and item.departure.trip_id == trip
            and item.departure.service_day == day
            and (seq is None or item.departure.origin_seq == seq)
        ),
        None,
    )


def train_times(*, trips: TripInputs, reach: Reach) -> dict[int, str]:
    """
    Time the rows of a train Strip.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `train_reach`.

    Returns
    -------
    dict[int, str]
        Clock time by row. The From has the followed train's departure.
        Calls below it have the followed train's times, its earlier calls
        theirs, whether made or not, and calls above it the train read
        upward theirs. Passed stations, exit rows and rows its train
        does not call at have none.
    """
    origin = origin_of(reach=reach)
    main = trip_item(
        trips=trips, trip=reach.trip, day=reach.day, seq=reach.seq,
    )
    above = trip_item(
        trips=trips,
        trip=reach.above_trip,
        day=reach.above_day,
        seq=reach.above_seq,
    )
    lead = main or above
    if origin is None or lead is None:
        return {}
    here = reach.rows[origin].place.stop_id
    moments = [
        {} if item is None else {
            **earlier_times(item=item, trips=trips, origin=here),
            **trip_times(item=item, trips=trips, origin=here, above=False),
        }
        for item in (main, above)
    ]
    found = {
        index: moments[row.above and row.kind is RowKind.MAIN].get(
            row.place.stop_id,
        )
        for index, row in enumerate(reach.rows)
        if row.kind in (RowKind.MAIN, RowKind.EARLIER)
    }
    return {
        **{
            index: clock_text(moment=moment, zone=trips.zone)
            for index, moment in found.items()
            if moment is not None
        },
        origin: clock_text(moment=lead.departs, zone=trips.zone),
    }


def train_note(
    *, trips: TripInputs, reach: Reach,
) -> tuple[int, CarryNote] | None:
    """
    Place the carry-on note of the followed train.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `train_reach`.

    Returns
    -------
    tuple[int, CarryNote] | None
        The row where the followed train runs on as another route, with
        that change; None when it does not, or not on these rows.
    """
    item = trip_item(
        trips=trips, trip=reach.trip, day=reach.day, seq=reach.seq,
    )
    then = None if item is None else item.departure.facts.then
    if item is None or then is None:
        return None
    change = change_at(trips=trips, item=item)
    found = next(
        (
            index
            for index, row in enumerate(reach.rows)
            if row.kind is RowKind.MAIN
            and not row.above
            and row.place.stop_id == change
        ),
        None,
    )
    if found is None:
        return None
    return found, CarryNote(
        route=item.departure.route, then=f'{then.route} {then.headsign}',
    )
