"""Parse GTFS-realtime trip updates, converting each trip on first use."""

import logging
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from typing import Any, Final

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from paperroute.adapters.tfnsw import FeedError
from paperroute.core.geo import to_metres
from paperroute.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
    fallback_label,
)
from paperroute.core.realtime import RealtimeSnapshot, StopUpdate, TripRealtime

logger = logging.getLogger(__name__)
ENTITY_FIELD: Final[int] = 2
CANCELED: Final[int] = (
    gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.CANCELED
)
NO_DATA: Final[int] = (
    gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.NO_DATA
)


def varint(*, data: bytes, at: int) -> tuple[int, int]:
    """
    Read a protobuf varint.

    Parameters
    ----------
    data : bytes
        Buffer.
    at : int
        Start offset.

    Returns
    -------
    tuple[int, int]
        Value and the offset after it.

    Raises
    ------
    IndexError
        When the buffer ends mid-varint.
    """
    value, shift = 0, 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def skip(*, data: bytes, at: int, wire: int) -> tuple[bytes | None, int]:
    """
    Step over one field's payload.

    Parameters
    ----------
    data : bytes
        Buffer.
    at : int
        Offset after the field key.
    wire : int
        Wire type.

    Returns
    -------
    tuple[bytes | None, int]
        The payload for length-delimited fields, and the next offset.

    Raises
    ------
    IndexError
        On unknown wire types or a truncated buffer.
    """
    if wire == 0:
        return None, varint(data=data, at=at)[1]
    if wire in (1, 5):
        return None, at + (8 if wire == 1 else 4)
    if wire != 2:
        raise IndexError(f'wire type {wire}')
    length, at = varint(data=data, at=at)
    if at + length > len(data):
        raise IndexError('truncated')
    return data[at : at + length], at + length


def entity_blobs(*, data: bytes) -> Iterator[bytes]:
    """
    Yield each FeedEntity's bytes from a FeedMessage.

    Stops quietly at the first truncated or unreadable field.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.

    Yields
    ------
    bytes
        One serialised FeedEntity.
    """
    at = 0
    try:
        while at < len(data):
            key, at = varint(data=data, at=at)
            payload, at = skip(data=data, at=at, wire=key & 7)
            if key >> 3 == ENTITY_FIELD and payload is not None:
                yield payload
    except IndexError:
        logger.warning('stopped reading feed at byte %d', at)


def event_value(
    *, event: gtfs_realtime_pb2.TripUpdate.StopTimeEvent | None, name: str
) -> int | None:
    """
    Read 'delay' or 'time' from a StopTimeEvent when it is set.

    Parameters
    ----------
    event : gtfs_realtime_pb2.TripUpdate.StopTimeEvent | None
        Arrival or departure event, or None when the update has none.
    name : str
        Field name.

    Returns
    -------
    int | None
        Value, or None when unset.
    """
    if event is None or not event.HasField(name):
        return None
    return int(getattr(event, name))


def stop_update(
    *, update: gtfs_realtime_pb2.TripUpdate.StopTimeUpdate
) -> StopUpdate:
    """
    Convert one StopTimeUpdate.

    Parameters
    ----------
    update : gtfs_realtime_pb2.TripUpdate.StopTimeUpdate
        Protobuf message.

    Returns
    -------
    StopUpdate
        Plain data.
    """
    arrival = update.arrival if update.HasField('arrival') else None
    departure = update.departure if update.HasField('departure') else None
    has_sequence = update.HasField('stop_sequence')
    return StopUpdate(
        sequence=update.stop_sequence if has_sequence else None,
        stop_id=update.stop_id or None,
        arrival_delay=event_value(event=arrival, name='delay'),
        departure_delay=event_value(event=departure, name='delay'),
        arrival_time=event_value(event=arrival, name='time'),
        departure_time=event_value(event=departure, name='time'),
    )


def parse_start_date(*, text: str) -> date | None:
    """
    Read a trip's start date, or None when it is missing or malformed.

    Parameters
    ----------
    text : str
        Date as written in the feed, for example '20261016'.

    Returns
    -------
    date | None
        The date.
    """
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        logger.warning('ignored malformed start_date %r', text)
        return None


def to_trip(*, update: gtfs_realtime_pb2.TripUpdate) -> TripRealtime:
    """
    Convert one TripUpdate, dropping stop updates marked NO_DATA.

    Parameters
    ----------
    update : gtfs_realtime_pb2.TripUpdate
        Protobuf message.

    Returns
    -------
    TripRealtime
        Plain data.
    """
    return TripRealtime(
        trip_id=update.trip.trip_id,
        cancelled=update.trip.schedule_relationship == CANCELED,
        updates=tuple(
            stop_update(update=stop)
            for stop in update.stop_time_update
            if stop.schedule_relationship != NO_DATA
        ),
        start_date=parse_start_date(text=update.trip.start_date),
    )


class TripIndex(Mapping[str, TripRealtime]):
    """
    Trip updates by trip ID, converted to core types on first lookup.

    Parameters
    ----------
    updates : dict[str, Any]
        Raw TripUpdate messages by trip ID.
    """

    def __init__(self, *, updates: dict[str, Any]) -> None:
        self.updates = updates
        self.converted: dict[str, TripRealtime] = {}

    def __getitem__(self, key: str) -> TripRealtime:
        """
        Convert and cache one trip.

        Parameters
        ----------
        key : str
            Trip ID.

        Returns
        -------
        TripRealtime
            The trip's live data.
        """
        if key not in self.converted:
            self.converted[key] = to_trip(update=self.updates[key])
        return self.converted[key]

    def __contains__(self, key: object) -> bool:
        """
        Check for a trip without converting it.

        Parameters
        ----------
        key : object
            Trip ID.

        Returns
        -------
        bool
            Whether the feed mentions the trip.
        """
        return key in self.updates

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over trip IDs.

        Returns
        -------
        Iterator[str]
            Trip IDs in feed order.
        """
        return iter(self.updates)

    def __len__(self) -> int:
        """
        Count the trips.

        Returns
        -------
        int
            Number of trips.
        """
        return len(self.updates)


def decoded_entities(*, data: bytes) -> Iterator[Any]:
    """
    Decode a FeedMessage entity by entity, skipping any that are corrupt.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.

    Yields
    ------
    Any
        One gtfs_realtime_pb2.FeedEntity.
    """
    for blob in entity_blobs(data=data):
        try:
            yield gtfs_realtime_pb2.FeedEntity.FromString(blob)
        except DecodeError:
            logger.warning('skipped an undecodable entity')


def decode_feed(*, data: bytes) -> tuple[list[Any], int | None]:
    """
    Decode every entity: whole message first, entity by entity if that fails.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.

    Returns
    -------
    tuple[list[Any], int | None]
        The gtfs_realtime_pb2.FeedEntity messages and the header timestamp.
        The timestamp is None when reading entity by entity.

    Raises
    ------
    FeedError
        When the body is empty, yields no entity at all, or decodes whole
        but has no header.
    """
    if not data:
        raise FeedError('feed body is empty')
    try:
        message = gtfs_realtime_pb2.FeedMessage.FromString(data)
    except DecodeError:
        logger.warning('feed did not decode whole; reading entity by entity')
    else:
        if not message.HasField('header'):
            raise FeedError('feed body has no header')
        return list(message.entity), int(message.header.timestamp)
    entities = list(decoded_entities(data=data))
    if not entities:
        raise FeedError('feed body did not decode')
    return entities, None


def feed_entities(*, data: bytes) -> list[Any]:
    """
    Decode every entity of a feed.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.

    Returns
    -------
    list[Any]
        The gtfs_realtime_pb2.FeedEntity messages.
    """
    return decode_feed(data=data)[0]


def parse_trip_updates(
    *, data: bytes, fetched_at: datetime
) -> RealtimeSnapshot:
    """
    Index a trip-updates feed by trip ID; trips convert when looked up.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    RealtimeSnapshot
        Snapshot whose trips mapping is a TripIndex.

    Raises
    ------
    FeedError
        When the body is empty or yields no entity at all.
    """
    updates = {
        entity.trip_update.trip.trip_id: entity.trip_update
        for entity in feed_entities(data=data)
        if entity.HasField('trip_update') and entity.trip_update.trip.trip_id
    }
    return RealtimeSnapshot(
        fetched_at=fetched_at, trips=TripIndex(updates=updates)
    )


def vehicle_bearing(*, position: Any) -> float | None:
    """
    Read a vehicle's heading, or None when it is not a real one.

    A bearing of 0 with no speed is how some feeds write "unknown".

    Parameters
    ----------
    position : Any
        A gtfs_realtime_pb2.Position.

    Returns
    -------
    float | None
        Degrees clockwise from north, or None.
    """
    if not position.HasField('bearing'):
        return None
    if position.bearing == 0 and not position.HasField('speed'):
        return None
    return float(position.bearing)


def to_vehicle(*, vehicle: Any, header_time: int) -> VehiclePosition:
    """
    Convert one VehiclePosition message.

    Parameters
    ----------
    vehicle : Any
        A gtfs_realtime_pb2.VehiclePosition with a position.
    header_time : int
        Timestamp used when the vehicle has none.

    Returns
    -------
    VehiclePosition
        Plain data.
    """
    route_id = vehicle.trip.route_id
    return VehiclePosition(
        trip_id=vehicle.trip.trip_id,
        route_id=route_id,
        label=fallback_label(route_id=route_id),
        point=to_metres(
            lon=vehicle.position.longitude, lat=vehicle.position.latitude
        ),
        bearing=vehicle_bearing(position=vehicle.position),
        timestamp=(
            int(vehicle.timestamp)
            if vehicle.HasField('timestamp')
            else header_time
        ),
        start_date=parse_start_date(text=vehicle.trip.start_date),
    )


def parse_positions(
    *, data: bytes, fetched_at: datetime
) -> PositionsSnapshot:
    """
    Read every positioned vehicle in a vehicle-positions feed.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    PositionsSnapshot
        Vehicles with a position; the header time is the feed's, or the
        fetch time when the feed has none.

    Raises
    ------
    FeedError
        When the body is empty or yields no entity at all.
    """
    entities, stamp = decode_feed(data=data)
    header_time = stamp or int(fetched_at.timestamp())
    return PositionsSnapshot(
        fetched_at=fetched_at,
        header_time=header_time,
        vehicles=tuple(
            to_vehicle(vehicle=entity.vehicle, header_time=header_time)
            for entity in entities
            if entity.HasField('vehicle')
            and entity.vehicle.HasField('position')
        ),
    )
