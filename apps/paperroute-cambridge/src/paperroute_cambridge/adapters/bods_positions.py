"""Read BODS vehicle positions, naming coaches by their SIRI-VM line."""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from paperroute_cambridge.adapters.siri_vm import CoachSighting
from paperroute_transit.adapters.feed_errors import FeedError
from paperroute_transit.adapters.gtfs_rt import read_positions
from paperroute_transit.core.geo import Projection
from paperroute_transit.core.positions import PositionsSnapshot


def named(*, data: bytes, sightings: Sequence[CoachSighting]) -> bytes:
    """
    Write each coach's line into its vehicle's label.

    Every label is cleared first. An entity is then named only when it has
    no trip, is the only trip-less entity with its vehicle id, and exactly
    one sighting has that vehicle ref and the entity's timestamp.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.
    sightings : Sequence[CoachSighting]
        The coach sightings of the same poll.

    Returns
    -------
    bytes
        The serialised message with the labels set.

    Raises
    ------
    FeedError
        When the body does not decode.
    """
    message = gtfs_realtime_pb2.FeedMessage()
    try:
        message.ParseFromString(data)
    except DecodeError as error:
        raise FeedError('positions body did not decode') from error
    vehicles = [
        entity.vehicle.vehicle
        for entity in message.entity
        if entity.HasField('vehicle')
    ]
    tripless = Counter(
        entity.vehicle.vehicle.id
        for entity in message.entity
        if entity.HasField('vehicle') and not entity.vehicle.trip.trip_id
    )
    seen = Counter((found.vehicle, found.recorded) for found in sightings)
    lines = {
        (found.vehicle, found.recorded): found.line for found in sightings
    }
    for vehicle in vehicles:
        vehicle.label = ''
    for entity in message.entity:
        if not entity.HasField('vehicle') or entity.vehicle.trip.trip_id:
            continue
        key = (entity.vehicle.vehicle.id, int(entity.vehicle.timestamp))
        if tripless[key[0]] == 1 and seen[key] == 1:
            entity.vehicle.vehicle.label = lines[key]
    return bytes(message.SerializeToString())


def parse_positions(
    *, data: bytes, fetched_at: datetime, projection: Projection,
) -> PositionsSnapshot:
    """
    Read every positioned vehicle, labelled as the feed names it.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.
    fetched_at : datetime
        When it was downloaded.
    projection : Projection
        The projection to place the vehicles in.

    Returns
    -------
    PositionsSnapshot
        Vehicles with a position, each labelled with its
        vehicle.vehicle.label, which is '' when unnamed; the header time
        is the feed's, or the fetch time when the feed has none.

    Raises
    ------
    FeedError
        When the body is empty or yields no entity at all.
    """
    return read_positions(
        data=data,
        fetched_at=fetched_at,
        projection=projection,
        label=lambda vehicle: vehicle.vehicle.label,
    )
