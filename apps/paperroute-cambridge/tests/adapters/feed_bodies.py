"""Builders for BODS positions bodies and SIRI-VM bodies."""

from collections.abc import Sequence
from datetime import UTC, datetime
from xml.etree import ElementTree

from google.transit import gtfs_realtime_pb2

from paperroute_cambridge.adapters.bods_positions import parse_positions
from paperroute_cambridge.area import CAMBRIDGE_PROJECTION

NOW = datetime(1970, 1, 1, 0, 20, tzinfo=UTC)
AT_990 = '1970-01-01T00:16:30+00:00'
SIRI = 'http://www.siri.org.uk/siri'


def positions_body(
    *,
    vehicles: Sequence[tuple[str, str, str, str, int]],
    header: int = 1_000,
) -> bytes:
    """
    Build a GTFS-RT vehicle-positions body.

    Parameters
    ----------
    vehicles : Sequence[tuple[str, str, str, str, int]]
        Vehicle id, trip id, route id, label and timestamp of each.
    header : int
        Header timestamp.

    Returns
    -------
    bytes
        The serialised message.
    """
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = '2.0'
    message.header.timestamp = header
    for number, (vehicle_id, trip_id, route_id, label, stamp) in enumerate(
        vehicles,
    ):
        entity = message.entity.add()
        entity.id = str(number)
        entity.vehicle.vehicle.id = vehicle_id
        entity.vehicle.vehicle.label = label
        entity.vehicle.trip.trip_id = trip_id
        entity.vehicle.trip.route_id = route_id
        entity.vehicle.timestamp = stamp
        entity.vehicle.position.longitude = 0.1218
        entity.vehicle.position.latitude = 52.2053
    return bytes(message.SerializeToString())


def siri_body(*, activities: Sequence[tuple[str, str, str, str, str]]) -> bytes:
    """
    Build a SIRI-VM body.

    Parameters
    ----------
    activities : Sequence[tuple[str, str, str, str, str]]
        Operator, vehicle ref, line ref, published name and
        RecordedAtTime text of each.

    Returns
    -------
    bytes
        The XML document.
    """
    ElementTree.register_namespace('', SIRI)
    root = ElementTree.Element(f'{{{SIRI}}}Siri')
    delivery = ElementTree.SubElement(
        ElementTree.SubElement(root, f'{{{SIRI}}}ServiceDelivery'),
        f'{{{SIRI}}}VehicleMonitoringDelivery',
    )
    for operator, vehicle, line, name, recorded in activities:
        activity = ElementTree.SubElement(
            delivery, f'{{{SIRI}}}VehicleActivity',
        )
        ElementTree.SubElement(
            activity, f'{{{SIRI}}}RecordedAtTime',
        ).text = recorded
        journey = ElementTree.SubElement(
            activity, f'{{{SIRI}}}MonitoredVehicleJourney',
        )
        for tag, text in (
            ('LineRef', line),
            ('PublishedLineName', name),
            ('OperatorRef', operator),
            ('VehicleRef', vehicle),
        ):
            ElementTree.SubElement(journey, f'{{{SIRI}}}{tag}').text = text
    return ElementTree.tostring(root, xml_declaration=True)


def labels(body: bytes) -> list[str]:
    """
    Read a body and list each vehicle's label.

    Parameters
    ----------
    body : bytes
        A positions body.

    Returns
    -------
    list[str]
        The labels, in body order.
    """
    snapshot = parse_positions(
        data=body, fetched_at=NOW, projection=CAMBRIDGE_PROJECTION,
    )
    return [vehicle.label for vehicle in snapshot.vehicles]
