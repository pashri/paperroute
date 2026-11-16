"""Read the coach sightings in a SIRI-VM vehicle monitoring body."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from xml.etree import ElementTree

from paperroute_transit.adapters.feed_errors import FeedError

SIRI: Final[str] = '{http://www.siri.org.uk/siri}'


@dataclass(frozen=True)
class CoachSighting:
    """One vehicle activity; the recorder counts sightings by operator."""

    operator: str  # OperatorRef
    vehicle: str  # VehicleRef, the id GTFS-RT gives as vehicle.id
    line: str  # PublishedLineName, else LineRef
    recorded: int  # RecordedAtTime, seconds since the epoch


def recorded_at(*, text: str) -> int | None:
    """
    Read a RecordedAtTime.

    Parameters
    ----------
    text : str
        An ISO 8601 time with an offset.

    Returns
    -------
    int | None
        Seconds since the epoch; None when it has no offset or does not
        parse.
    """
    try:
        stamp = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return int(stamp.timestamp())


def child_text(*, element: ElementTree.Element, tag: str) -> str:
    """
    Read the trimmed text of a SIRI child element.

    Parameters
    ----------
    element : ElementTree.Element
        The parent element.
    tag : str
        Child tag without its namespace.

    Returns
    -------
    str
        The text; '' when the child is missing or empty.
    """
    return (element.findtext(f'{SIRI}{tag}') or '').strip()


def to_sighting(*, activity: ElementTree.Element) -> CoachSighting | None:
    """
    Convert one VehicleActivity.

    Parameters
    ----------
    activity : ElementTree.Element
        A VehicleActivity element.

    Returns
    -------
    CoachSighting | None
        The sighting; None when the vehicle, line or time is missing.
    """
    journey = activity.find(f'{SIRI}MonitoredVehicleJourney')
    if journey is None:
        return None

    recorded = recorded_at(
        text=activity.findtext(f'{SIRI}RecordedAtTime') or '',
    )
    line = (
        child_text(element=journey, tag='PublishedLineName')
        or child_text(element=journey, tag='LineRef')
    )
    vehicle = child_text(element=journey, tag='VehicleRef')
    if recorded is None or not line or not vehicle:
        return None
    return CoachSighting(
        operator=child_text(element=journey, tag='OperatorRef'),
        vehicle=vehicle,
        line=line,
        recorded=recorded,
    )


def coach_sightings(*, data: bytes) -> tuple[CoachSighting, ...]:
    """
    Read every complete vehicle activity in a SIRI-VM body.

    Parameters
    ----------
    data : bytes
        The XML document.

    Returns
    -------
    tuple[CoachSighting, ...]
        The sightings, in body order; incomplete activities are left out.

    Raises
    ------
    FeedError
        When the body is not XML, or declares an encoding Python lacks.
    """
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, LookupError) as error:
        raise FeedError('SIRI-VM body is not XML') from error
    sightings = (
        to_sighting(activity=activity)
        for activity in root.iter(f'{SIRI}VehicleActivity')
    )
    return tuple(found for found in sightings if found is not None)
