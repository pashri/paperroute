"""Read and cut the parts of a SIRI-VM body the recorder needs."""

from typing import Final
from xml.etree import ElementTree

from paperroute_cambridge.adapters.siri_vm import SIRI, to_sighting
from paperroute_transit.adapters.feed_errors import FeedError

JOURNEY_TAGS: Final[tuple[str, ...]] = (
    'LineRef',
    'PublishedLineName',
    'OperatorRef',
    'VehicleRef',
)


def siri_root(*, body: bytes) -> ElementTree.Element:
    """
    Parse a SIRI-VM body.

    Parameters
    ----------
    body : bytes
        The XML document.

    Returns
    -------
    ElementTree.Element
        The root element.

    Raises
    ------
    FeedError
        When the body is not XML.
    """
    try:
        return ElementTree.fromstring(body)
    except (ElementTree.ParseError, LookupError) as error:
        raise FeedError('SIRI-VM body is not XML') from error


def activity_places(*, body: bytes) -> list[tuple[float, float]]:
    """
    List the VehicleLocation of each activity that has one.

    Parameters
    ----------
    body : bytes
        The SIRI-VM document.

    Returns
    -------
    list[tuple[float, float]]
        Longitude and latitude of each located activity.
    """
    places = []
    for activity in siri_root(body=body).iter(f'{SIRI}VehicleActivity'):
        place = activity.find(f'.//{SIRI}VehicleLocation')
        if place is None:
            continue
        try:
            places.append(
                (
                    float(place.findtext(f'{SIRI}Longitude') or ''),
                    float(place.findtext(f'{SIRI}Latitude') or ''),
                ),
            )
        except ValueError:
            continue
    return places


def activity_count(*, body: bytes) -> int:
    """
    Count the VehicleActivity elements in a SIRI-VM body.

    Parameters
    ----------
    body : bytes
        The SIRI-VM document.

    Returns
    -------
    int
        The count.
    """
    return sum(1 for _ in siri_root(body=body).iter(f'{SIRI}VehicleActivity'))


def cut_siri(*, body: bytes) -> bytes:
    """
    Keep only the elements the sightings reader uses.

    Parameters
    ----------
    body : bytes
        The SIRI-VM document.

    Returns
    -------
    bytes
        A document with each complete activity's RecordedAtTime and the
        journey's line, operator and vehicle elements.
    """
    ElementTree.register_namespace('', SIRI[1:-1])
    root = ElementTree.Element(f'{SIRI}Siri')
    delivery = ElementTree.SubElement(
        ElementTree.SubElement(root, f'{SIRI}ServiceDelivery'),
        f'{SIRI}VehicleMonitoringDelivery',
    )
    for activity in siri_root(body=body).iter(f'{SIRI}VehicleActivity'):
        journey = activity.find(f'{SIRI}MonitoredVehicleJourney')
        if to_sighting(activity=activity) is None or journey is None:
            continue
        kept = ElementTree.SubElement(delivery, f'{SIRI}VehicleActivity')
        stamp = ElementTree.SubElement(kept, f'{SIRI}RecordedAtTime')
        stamp.text = activity.findtext(f'{SIRI}RecordedAtTime')
        kept_journey = ElementTree.SubElement(
            kept, f'{SIRI}MonitoredVehicleJourney',
        )
        for tag in JOURNEY_TAGS:
            child = journey.find(f'{SIRI}{tag}')
            if child is not None:
                kept_journey.append(child)
    return ElementTree.tostring(root, xml_declaration=True)
