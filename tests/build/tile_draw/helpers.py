"""Shared helpers for the tile draw tests."""

import math
from typing import Final

import numpy as np

from paperroute_device.render.canvas import text_width
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.labels import TEXT_MARGIN
from paperroute_transit.build.map_style import STREET_FACE
from paperroute_transit.build.tile_draw import Window
from paperroute_transit.build.tile_store import PlacedLabel
from paperroute_transit.core.geo import MetreBox, to_metres
from paperroute_transit.core.tiles import (
    TILE,
    LabelKind,
    from_pixels,
    tile_of,
    to_pixels,
)
from tests.build.tile_inputs import pixel_vertices

FRAME_HALF: Final[float] = 800.0  # metres


SEAM_MPP: Final[int] = 3


SEAM_ANGLE: Final[float] = 20.0  # degrees


def around(*, lon: float, lat: float, half: float) -> MetreBox:
    """
    Build a metre box centred on a longitude and latitude.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    half : float
        Half the box's side in metres.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    x, y = to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION)
    return (x - half, y - half, x + half, y + half)


def tile_pixel(
    *,
    lon: float,
    lat: float,
    mpp: int,
) -> tuple[Window, tuple[int, int]]:
    """
    Find the tile window holding a point and the point's pixel in it.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[Window, tuple[int, int]]
        The tile's window, and the column and row of the pixel holding
        the point.
    """
    pixel = to_pixels(
        point=to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION),
        mpp=mpp,
        projection=SYDNEY_PROJECTION,
    )
    col, row = tile_of(pixel=pixel)
    window = Window(
        mpp=mpp,
        left=col * TILE,
        top=row * TILE,
        width=TILE,
        height=TILE,
        projection=SYDNEY_PROJECTION,
    )
    local = (
        math.floor(pixel[0]) - window.left,
        math.floor(pixel[1]) - window.top,
    )
    return window, local


def add_pixel_line(
    *,
    store: FeatureStore,
    layer: str,
    pixels: list[tuple[float, float]],
    mpp: int,
) -> None:
    """
    Add a line drawn in a zoom's global pixels.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    layer : str
        Line layer.
    pixels : list[tuple[float, float]]
        Vertices in global pixels.
    mpp : int
        Zoom the pixels are in.
    """
    store.add_line(
        layer=layer,
        name='',
        points=pixel_vertices(pixels=pixels, mpp=mpp),
    )


def origin_window(*, mpp: int) -> Window:
    """
    Give the tile at the grid origin.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    Window
        The tile at column 0, row 0.
    """
    return Window(
        mpp=mpp,
        left=0,
        top=0,
        width=TILE,
        height=TILE,
        projection=SYDNEY_PROJECTION,
    )


def park_under_tile(*, store: FeatureStore) -> tuple[Window, float, float]:
    """
    Fill the area around the SEAM_MPP tile window with park.

    Parameters
    ----------
    store : FeatureStore
        Store to add the park to.

    Returns
    -------
    tuple[Window, float, float]
        The tile window and its left and top in pixels.
    """
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=store,
        layer='park',
        box=(x - 10, y - 10, x + 300, y + 300),
        mpp=SEAM_MPP,
    )
    return window, x, y


def tile_window(*, mpp: int) -> Window:
    """
    Give a tile window away from the grid origin.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    Window
        The tile at column 40, row 30.
    """
    return Window(
        mpp=mpp,
        left=40 * TILE,
        top=30 * TILE,
        width=TILE,
        height=TILE,
        projection=SYDNEY_PROJECTION,
    )


def add_pixel_area(
    *,
    store: FeatureStore,
    layer: str,
    box: tuple[float, float, float, float],
    mpp: int,
) -> None:
    """
    Add a rectangular area drawn in a zoom's global pixels.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    layer : str
        Area layer.
    box : tuple[float, float, float, float]
        Left, top, right and bottom in global pixels.
    mpp : int
        Zoom the pixels are in.
    """
    left, top, right, bottom = box
    corners = [
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom),
        (left, top),
    ]
    store.add_area(
        layer=layer,
        rings=[np.array([from_pixels(
            pixel=p,
            mpp=mpp,
            projection=SYDNEY_PROJECTION,
        ) for p in corners])],
    )


def street_label(
    *,
    name: str,
    centre: tuple[float, float],
    angle: float,
) -> PlacedLabel:
    """
    Build a street label along a straight stretch.

    Parameters
    ----------
    name : str
        The street name.
    centre : tuple[float, float]
        Centre in global pixels.
    angle : float
        Rotation in degrees.

    Returns
    -------
    PlacedLabel
        The label, as wide as its text plus the margin.
    """
    width = text_width(text=name, face=STREET_FACE) + TEXT_MARGIN
    turn = math.radians(angle)
    reach = (math.cos(turn) * width / 2, math.sin(turn) * width / 2)
    return PlacedLabel(
        name=name,
        x=centre[0],
        y=centre[1],
        angle=angle,
        width=width,
        start=(centre[0] - reach[0], centre[1] - reach[1]),
        end=(centre[0] + reach[0], centre[1] + reach[1]),
        zoom=SEAM_MPP,
        kind=LabelKind.STREET,
    )


def point_label(
    *,
    name: str,
    kind: LabelKind,
    point: tuple[float, float],
    rank: int,
) -> PlacedLabel:
    """
    Build a level station or suburb label.

    Parameters
    ----------
    name : str
        The name.
    kind : LabelKind
        Station or suburb.
    point : tuple[float, float]
        The feature's point in global pixels.
    rank : int
        Place rank.

    Returns
    -------
    PlacedLabel
        The label, 15 px above a station's point and on a suburb's.
    """
    rise = 15 if kind == LabelKind.STATION else 0
    return PlacedLabel(
        name=name,
        x=float(round(point[0])),
        y=float(round(point[1] - rise)),
        angle=0.0,
        width=60.0,
        start=point,
        end=point,
        zoom=SEAM_MPP,
        kind=kind,
        rank=rank,
    )
