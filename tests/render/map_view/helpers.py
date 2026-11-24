"""Shared helpers for the map view tests."""

import math

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import PAPER
from paperroute_device.render.frame import HEADER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.core.geo import Point, to_metres
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    MapView,
    Marker,
    screen_point,
)
from paperroute_transit.core.stop_view import Pill, Row, empty_text

MPP = 8


CENTRE: Point = to_metres(
    lon=151.0036,
    lat=-33.8170,
    projection=SYDNEY_PROJECTION,
)


ROW_TOPS = (110, 182, 254, 326, 398)


LONG_EMPTY = empty_text(
    destination='Sydney Olympic Park Wharf, Parramatta River', loaded=True
)


def row(
    *,
    route: str = '606',
    onto: str | None = None,
    pill: Pill | None = None,
    live: bool = True,
    countdown: str = '9',
) -> Row:
    """
    Make a sidebar row.

    Parameters
    ----------
    route : str
        Route label.
    onto : str | None
        Route the vehicle becomes before the Destination.
    pill : Pill | None
        Marker.
    live : bool
        True for a realtime row.
    countdown : str
        Countdown text.

    Returns
    -------
    Row
        A bus row leaving at 8:09.
    """
    return Row(
        route=route,
        vehicle='bus',
        where='',
        destination='Parramatta',
        countdown=countdown,
        leaves_at='8:09',
        arrives_at='8:45',
        ride_minutes=36,
        live=live,
        pill=pill,
        onto=onto,
    )


VIEW = MapView(
    origin='Hollis St Opp Constitution Hill Library',
    destination='Parramatta Station',
    clock='8:00',
    base=MapBase(
        centre=CENTRE,
        mpp=MPP,
        route=(),
        projection=SYDNEY_PROJECTION,
    ),
    zoom=2,
    markers=(),
    sidebar=(row(),) * 5,
    banner=None,
    empty=None,
)


def paper() -> NDArray[np.uint8]:
    """
    Make a blank map base.

    Returns
    -------
    NDArray[np.uint8]
        Map-sized paper.
    """
    return np.full((MAP_HEIGHT, MAP_WIDTH), PAPER, dtype=np.uint8)


def live(*, at: tuple[float, float], route: str = '606') -> Marker:
    """
    Make a live marker without a wedge.

    Parameters
    ----------
    at : tuple[float, float]
        Map pixels.
    route : str
        Route label.

    Returns
    -------
    Marker
        A pinned live marker.
    """
    return Marker(
        route=route,
        x=at[0],
        y=at[1],
        bearing=None,
        live=True,
        pinned=True,
    )


def stop_point() -> tuple[float, float]:
    """
    Find the map point under the From stop.

    Returns
    -------
    tuple[float, float]
        Column and row, before the header is added.
    """
    return screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=CENTRE,
        projection=SYDNEY_PROJECTION,
    )


def stop_pixel() -> tuple[int, int]:
    """
    Find the frame pixel under the From stop.

    Returns
    -------
    tuple[int, int]
        Column and row.
    """
    x, y = stop_point()
    return math.floor(x), math.floor(y + HEADER)
