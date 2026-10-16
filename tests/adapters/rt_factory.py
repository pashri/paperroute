"""Build real GTFS-realtime protobuf bytes for tests."""

from dataclasses import dataclass
from typing import Final

from google.transit import gtfs_realtime_pb2

STOP_REL: Final = (
    gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship
)
NO_DATA: Final = STOP_REL.NO_DATA
CANCELED: Final = (
    gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.CANCELED
)


@dataclass(frozen=True)
class RtStop:
    """One stop time update to encode."""

    seq: int
    stop_id: str | None = None
    arr_delay: int | None = None
    dep_delay: int | None = None
    arr_time: int | None = None
    dep_time: int | None = None
    no_data: bool = False


@dataclass(frozen=True)
class RtTrip:
    """One trip update to encode."""

    trip_id: str
    cancelled: bool = False
    stops: tuple[RtStop, ...] = ()
    start_date: str | None = None


def add_stop(*, update: gtfs_realtime_pb2.TripUpdate, stop: RtStop) -> None:
    """
    Append one stop time update to a TripUpdate message.

    Parameters
    ----------
    update : gtfs_realtime_pb2.TripUpdate
        Message being built.
    stop : RtStop
        Values to set; None fields stay unset.
    """
    message = update.stop_time_update.add(stop_sequence=stop.seq)
    if stop.stop_id is not None:
        message.stop_id = stop.stop_id
    if stop.no_data:
        message.schedule_relationship = NO_DATA
    for event, delay, at in (
        ('arrival', stop.arr_delay, stop.arr_time),
        ('departure', stop.dep_delay, stop.dep_time),
    ):
        if delay is not None:
            getattr(message, event).delay = delay
        if at is not None:
            getattr(message, event).time = at


def feed(*, trips: list[RtTrip], timestamp: int = 1792093000) -> bytes:
    """
    Serialise a FeedMessage of trip updates.

    Parameters
    ----------
    trips : list[RtTrip]
        Trips to encode.
    timestamp : int
        Header timestamp.

    Returns
    -------
    bytes
        Serialised message.
    """
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = '2.0'
    message.header.timestamp = timestamp
    for trip in trips:
        update = message.entity.add(id=trip.trip_id).trip_update
        update.trip.trip_id = trip.trip_id
        if trip.start_date is not None:
            update.trip.start_date = trip.start_date
        if trip.cancelled:
            update.trip.schedule_relationship = CANCELED
        for stop in trip.stops:
            add_stop(update=update, stop=stop)
    return bytes(message.SerializeToString())
