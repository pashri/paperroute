"""Shared helpers for the stop view tests."""

from dataclasses import replace
from datetime import timedelta

from paperroute_device.core.device_state import Banner
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyFeed
from paperroute_testkit.core.factories import START, departure
from paperroute_transit.core.alert_model import AlertBanner
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import TripFacts
from paperroute_transit.core.stop_view import (
    Row,
    StopInputs,
    StopView,
    build_stop_view,
)


def live(
    *,
    minutes: int,
    ride: int = 30,
    cancelled: bool = False,
    is_live: bool = True,
    late: int = 0,
    mode: SydneyFeed = SydneyFeed.BUS,
    facts: TripFacts = TripFacts(),
) -> LiveDeparture:
    """
    A resolved departure leaving some minutes after START.

    Parameters
    ----------
    minutes : int
        Minutes after START.
    ride : int
        Ride minutes.
    cancelled : bool
        Cancelled flag.
    is_live : bool
        Live flag.
    late : int
        Minutes the live times run behind the timetable.
    mode : SydneyFeed
        Mode.
    facts : TripFacts
        What is known about the trip.

    Returns
    -------
    LiveDeparture
        Departure.
    """
    dep = replace(
        departure(
            departs=START + timedelta(minutes=minutes),
            ride=ride,
            mode=mode,
        ),
        facts=facts,
    )
    delay = timedelta(minutes=late)
    return LiveDeparture(
        departure=dep,
        departs=dep.departs + delay,
        arrives=dep.arrives + delay,
        live=is_live,
        cancelled=cancelled,
    )


def stop_inputs(
    *,
    departures: tuple[LiveDeparture, ...] = (),
    scroll: int = 0,
    age: timedelta | None = timedelta(seconds=5),
    has_feed: bool = True,
    destination: str | None = 'Parramatta Station',
    large: bool = False,
    alert: AlertBanner | None = None,
    notice: str | None = None,
) -> StopInputs:
    """
    Build view inputs with fixed names and time.

    Parameters
    ----------
    departures : tuple[LiveDeparture, ...]
        Departures.
    scroll : int
        Scroll offset.
    age : timedelta | None
        Age of the live data, or None when there is none.
    has_feed : bool
        Whether the pin has a live feed.
    destination : str | None
        Destination name, or None for none.
    large : bool
        Whether rows use larger text.
    alert : AlertBanner | None
        The pin's alert line, if any.
    notice : str | None
        A device warning, if any; an empty one counts as none.

    Returns
    -------
    StopInputs
        The inputs; live data goes stale after two minutes.
    """
    return StopInputs(
        origin='Hollis St',
        destination=destination,
        departures=departures,
        now=START,
        scroll=scroll,
        live_since=None if age is None else START - age,
        has_feed=has_feed,
        stale_after=timedelta(minutes=2),
        large=large,
        alert=alert,
        notice=Banner(text=notice) if notice else None,
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )


def view(
    *,
    departures: tuple[LiveDeparture, ...] = (),
    scroll: int = 0,
    age: timedelta | None = timedelta(seconds=5),
    has_feed: bool = True,
    destination: str | None = 'Parramatta Station',
    large: bool = False,
    alert: AlertBanner | None = None,
    notice: str | None = None,
) -> StopView:
    """
    Build a view with fixed names and time.

    Parameters
    ----------
    departures : tuple[LiveDeparture, ...]
        Departures.
    scroll : int
        Scroll offset.
    age : timedelta | None
        Age of the live data, or None when there is none.
    has_feed : bool
        Whether the pin has a live feed.
    destination : str | None
        Destination name, or None for none.
    large : bool
        Whether rows use larger text.
    alert : AlertBanner | None
        The pin's alert line, if any.
    notice : str | None
        A device warning, if any; an empty one counts as none.

    Returns
    -------
    StopView
        View.
    """
    return build_stop_view(
        inputs=stop_inputs(
            departures=departures,
            scroll=scroll,
            age=age,
            has_feed=has_feed,
            destination=destination,
            large=large,
            alert=alert,
            notice=notice,
        ),
    )


def hero(*, result: StopView) -> Row:
    """
    The hero row, which must exist.

    Parameters
    ----------
    result : StopView
        View.

    Returns
    -------
    Row
        Hero row.
    """
    assert result.hero is not None
    return result.hero


def on(
    *,
    item: LiveDeparture,
    route: str = '606',
    headsign: str = 'Parramatta Station',
) -> LiveDeparture:
    """
    Give a departure another route and headsign.

    Parameters
    ----------
    item : LiveDeparture
        Departure.
    route : str
        Route short name.
    headsign : str
        Headsign.

    Returns
    -------
    LiveDeparture
        The departure with its route and headsign replaced.
    """
    return replace(
        item,
        departure=replace(item.departure, route=route, headsign=headsign),
    )


def many() -> tuple[LiveDeparture, ...]:
    """
    Fifteen departures, four minutes apart.

    Returns
    -------
    tuple[LiveDeparture, ...]
        Departures.
    """
    return tuple(live(minutes=minute) for minute in range(0, 60, 4))
