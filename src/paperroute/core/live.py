"""Apply live trip updates to scheduled departures."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.model import Departure, shifted
from paperroute.core.realtime import RealtimeSnapshot, StopUpdate, TripRealtime

STALE_AFTER: Final[timedelta] = timedelta(minutes=2)


@dataclass(frozen=True)
class LiveDeparture:
    """A departure with live times applied when available."""

    departure: Departure
    departs: datetime
    arrives: datetime
    live: bool
    cancelled: bool


def trip_for(
    *, departure: Departure, snapshot: RealtimeSnapshot
) -> TripRealtime | None:
    """
    Find the realtime record for a departure, by its ID or an alias.

    Parameters
    ----------
    departure : Departure
        Departure.
    snapshot : RealtimeSnapshot
        Snapshot.

    Returns
    -------
    TripRealtime | None
        The trip, if the feed mentions it.
    """
    for trip_id in (departure.trip_id, *departure.aliases):
        if trip_id in snapshot.trips:
            return snapshot.trips[trip_id]
    return None


def shift(
    *, update: StopUpdate, scheduled: datetime, arrival: bool
) -> int | None:
    """
    Work out how many seconds an update moves a scheduled time.

    Parameters
    ----------
    update : StopUpdate
        Update at the stop.
    scheduled : datetime
        Scheduled time there.
    arrival : bool
        Use the arrival event first (else departure first).

    Returns
    -------
    int | None
        Delay in seconds, or None when the update has no timing.
    """
    first, second = (
        (update.arrival_time, update.arrival_delay),
        (update.departure_time, update.departure_delay),
    )
    for absolute, delay in (first, second) if arrival else (second, first):
        if absolute is not None:
            return absolute - int(scheduled.timestamp())
        if delay is not None:
            return delay
    return None


def delay_at(
    *,
    updates: Sequence[StopUpdate],
    sequence: int,
    stop_id: str,
    scheduled: datetime,
    arrival: bool,
) -> int | None:
    """
    Find the delay at one stop: its own update, else the last earlier stop's.

    An earlier stop's absolute time says nothing about this stop, so only
    its delay carries forward.

    Parameters
    ----------
    updates : Sequence[StopUpdate]
        Trip's stop updates.
    sequence : int
        Stop sequence of interest.
    stop_id : str
        Stop ID of interest.
    scheduled : datetime
        Scheduled time there.
    arrival : bool
        Arrival rather than departure.

    Returns
    -------
    int | None
        Delay in seconds, or None when nothing applies.
    """
    exact = [
        u
        for u in updates
        if u.sequence == sequence
        or (u.sequence is None and u.stop_id == stop_id)
    ]
    if exact:
        return shift(update=exact[0], scheduled=scheduled, arrival=arrival)
    earlier = [
        u for u in updates if u.sequence is not None and u.sequence < sequence
    ]
    if not earlier:
        return None
    last = max(earlier, key=lambda u: u.sequence or 0)
    if last.departure_delay is not None:
        return last.departure_delay
    return last.arrival_delay


def stale(*, snapshot: RealtimeSnapshot, now: datetime) -> bool:
    """
    Say whether a snapshot is too old to use.

    Parameters
    ----------
    snapshot : RealtimeSnapshot
        Snapshot.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when it was fetched more than STALE_AFTER of real time ago.
    """
    age = now.timestamp() - snapshot.fetched_at.timestamp()
    return age > STALE_AFTER.total_seconds()


def resolve(
    *,
    departure: Departure,
    snapshot: RealtimeSnapshot | None,
    now: datetime,
) -> LiveDeparture:
    """
    Apply live data to a departure when the snapshot is fresh.

    A note for another day's run of the same trip ID (a different start
    date) doesn't apply. A note found under an alias numbers its stops for the
    other copy of the trip, so it is matched by stop ID only.

    Parameters
    ----------
    departure : Departure
        Scheduled departure.
    snapshot : RealtimeSnapshot | None
        Latest snapshot for its feed.
    now : datetime
        Current time.

    Returns
    -------
    LiveDeparture
        Departure with live times, or scheduled times when there's nothing.
    """
    scheduled = LiveDeparture(
        departure=departure,
        departs=departure.departs,
        arrives=departure.arrives,
        live=False,
        cancelled=False,
    )
    if snapshot is None or stale(snapshot=snapshot, now=now):
        return scheduled
    trip = trip_for(departure=departure, snapshot=snapshot)
    if trip is None or trip.start_date not in (None, departure.service_day):
        return scheduled
    updates = trip.updates
    if trip.trip_id != departure.trip_id:
        updates = tuple(replace(update, sequence=None) for update in updates)
    leave = delay_at(
        updates=updates,
        sequence=departure.origin_seq,
        stop_id=departure.origin_stop,
        scheduled=departure.departs,
        arrival=False,
    )
    reach = delay_at(
        updates=updates,
        sequence=departure.destination_seq,
        stop_id=departure.destination_stop,
        scheduled=departure.arrives,
        arrival=True,
    )
    leave_s = leave or 0
    reach_s = reach if reach is not None else leave_s
    return LiveDeparture(
        departure=departure,
        departs=shifted(moment=departure.departs, seconds=leave_s),
        arrives=shifted(moment=departure.arrives, seconds=reach_s),
        live=leave is not None or reach is not None,
        cancelled=trip.cancelled,
    )
