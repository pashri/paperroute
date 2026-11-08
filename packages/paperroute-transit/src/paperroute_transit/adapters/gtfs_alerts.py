"""Parse GTFS-realtime service alerts."""

import logging
from datetime import UTC, datetime
from typing import Any

from paperroute_transit.adapters.gtfs_rt import feed_entities
from paperroute_transit.core.alert_model import (
    Alert,
    AlertsSnapshot,
    Informed,
    Period,
)

logger = logging.getLogger(__name__)
HTML_SUFFIX = '/html'


def english(*, text: Any) -> str:
    """
    Pick the English text of a TranslatedString.

    Parameters
    ----------
    text : Any
        A gtfs_realtime_pb2.TranslatedString.

    Returns
    -------
    str
        The translation whose language is ``en``, else the first whose
        language does not end in ``/html``, else an empty string.
    """
    for item in text.translation:
        if item.language == 'en':
            return str(item.text)
    for item in text.translation:
        if not item.language.endswith(HTML_SUFFIX):
            return str(item.text)
    return ''


def moment(*, period: Any, name: str) -> datetime | None:
    """
    Read 'start' or 'end' of a TimeRange, reading zero as unset.

    Parameters
    ----------
    period : Any
        A gtfs_realtime_pb2.TimeRange.
    name : str
        Field name.

    Returns
    -------
    datetime | None
        An aware UTC time, or None when unset or zero.
    """
    if not period.HasField(name) or not getattr(period, name):
        return None
    return datetime.fromtimestamp(getattr(period, name), tz=UTC)


def to_period(*, period: Any) -> Period:
    """
    Convert one TimeRange, reading unset and zero times as open.

    Parameters
    ----------
    period : Any
        A gtfs_realtime_pb2.TimeRange.

    Returns
    -------
    Period
        Plain data.
    """
    return Period(
        start=moment(period=period, name='start'),
        end=moment(period=period, name='end'),
    )


def to_informed(*, selector: Any) -> Informed:
    """
    Convert one EntitySelector, reading each ID by presence.

    Parameters
    ----------
    selector : Any
        A gtfs_realtime_pb2.EntitySelector.

    Returns
    -------
    Informed
        Plain data with no resolved route or stops yet.
    """
    return Informed(
        route_id=selector.route_id or None,
        stop_id=selector.stop_id or None,
        trip_id=selector.trip.trip_id or None,
    )


def to_alert(*, entity: Any) -> Alert:
    """
    Convert one FeedEntity that carries an alert.

    Parameters
    ----------
    entity : Any
        A gtfs_realtime_pb2.FeedEntity.

    Returns
    -------
    Alert
        Plain data.

    Raises
    ------
    ValueError
        When a time is out of range.
    OverflowError
        When a time is out of range.
    OSError
        When a time is out of range.
    """
    alert = entity.alert
    return Alert(
        alert_id=entity.id,
        header=english(text=alert.header_text),
        description=english(text=alert.description_text),
        periods=tuple(to_period(period=item) for item in alert.active_period),
        informed=tuple(
            to_informed(selector=item) for item in alert.informed_entity
        ),
    )


def parse_alerts(
    *, data: bytes, feed: str, fetched_at: datetime
) -> AlertsSnapshot:
    """
    Read every alert in an alerts feed.

    An entity that cannot be read is skipped, with one warning per feed.

    Parameters
    ----------
    data : bytes
        Serialised FeedMessage.
    feed : str
        Feed name, kept on the snapshot.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    AlertsSnapshot
        The alerts in feed order.

    Raises
    ------
    FeedError
        When the body is empty or yields no entity at all.
    """
    alerts, skipped = readable_alerts(data=data)
    if skipped:
        logger.warning('skipped %d unreadable alerts in %s', skipped, feed)
    return AlertsSnapshot(
        feed=feed, fetched_at=fetched_at, alerts=tuple(alerts)
    )


def readable_alerts(*, data: bytes) -> tuple[list[Alert], int]:
    """
    Read every alert entity of a feed body that can be read.

    Parameters
    ----------
    data : bytes
        Feed body.

    Returns
    -------
    tuple[list[Alert], int]
        The alerts in feed order, and how many were skipped as
        unreadable.

    Raises
    ------
    FeedError
        When the body is empty or yields no entity at all.
    """
    alerts: list[Alert] = []
    skipped = 0
    for entity in feed_entities(data=data):
        if not entity.HasField('alert'):
            continue
        try:
            alerts.append(to_alert(entity=entity))
        except (ValueError, OverflowError, OSError):
            skipped += 1
    return alerts, skipped
