"""Shared test values and helpers for running the app on fakes."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import cast

from paperroute_device_testkit.device_fakes import FixedClock
from paperroute_device_testkit.paths import FIXTURES
from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import pin_of
from paperroute_testkit.fakes import (
    STOP_POINT,
    EmptyPlacedTimetable,
    FakeNetwork,
)
from paperroute_transit.adapters.feed_files import FileFeedsClient
from paperroute_transit.app_ports import Ports
from paperroute_transit.app_work import perform
from paperroute_transit.core.map_view import (
    STRIP_MAP_WIDTH,
    MapBase,
    MapView,
    Marker,
)
from paperroute_transit.core.messages import Effect, Event
from paperroute_transit.core.strip_model import StripMapView, StripPane
from paperroute_transit.ports import TimetableRepository

REAL = FIXTURES / 'real'
AT = datetime(2026, 10, 16, 6, 25, tzinfo=city_under_test().zone)
PIN = pin_of('215020', '200060')
MAP_PIN = pin_of('2145615', '215020')
MAP_MPP = 8


def map_view(*, markers: tuple[Marker, ...] = ()) -> MapView:
    """
    Make a Map view at the 606 stop's area.

    Parameters
    ----------
    markers : tuple[Marker, ...]
        Vehicles drawn.

    Returns
    -------
    MapView
        The view.
    """
    return MapView(
        origin='Here',
        destination='There',
        clock='6:25',
        base=MapBase(
            centre=STOP_POINT,
            mpp=MAP_MPP,
            route=(),
            projection=city_under_test().projection,
        ),
        zoom=2,
        markers=markers,
        sidebar=(),
        banner=None,
        empty=None,
    )


def strip_map_view(*, markers: tuple[Marker, ...] = ()) -> StripMapView:
    """
    Make a Strip + map view at the 606 stop's area.

    Parameters
    ----------
    markers : tuple[Marker, ...]
        Vehicles drawn on the map.

    Returns
    -------
    StripMapView
        The view, with an empty pane.
    """
    view = map_view(markers=markers)
    return StripMapView(
        map=replace(view, base=replace(view.base, width=STRIP_MAP_WIDTH)),
        pane=StripPane(rows=(), markers=()),
    )


def ports_of(
    *,
    timetable: TimetableRepository | None = None,
    network: FakeNetwork | None = None,
) -> Ports:
    """
    Make transit's ports on fakes, replacing the ones given.

    Parameters
    ----------
    timetable : TimetableRepository | None
        Timetable; a placed one with no departures by default.
    network : FakeNetwork | None
        Network; one that knows nothing by default.

    Returns
    -------
    Ports
        The ports.
    """
    return Ports(
        timetable=timetable or EmptyPlacedTimetable(),
        feeds=FileFeedsClient(directory=REAL),
        clock=FixedClock(at=AT),
        network=network or FakeNetwork(),
    )


async def posted_by(
    *, ports: Ports, effects: Sequence[Effect],
) -> list[Event]:
    """
    Carry out transit's effects and give every event they posted.

    Parameters
    ----------
    ports : Ports
        Adapters.
    effects : Sequence[Effect]
        Effects, in order.

    Returns
    -------
    list[Event]
        The events posted, in order.
    """
    posted: list[object] = []
    for effect in effects:
        await perform(ports=ports, effect=effect, post=posted.append)
    return cast(list[Event], posted)
