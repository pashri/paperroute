"""Map boxes that baked names and the corner boxes cover."""

from dataclasses import dataclass
from typing import Final

from paperroute.build.map_style import STATION_FACE, SUBURB_FACE, SUBURB_ZOOMS
from paperroute.core.map_view import MAP_HEIGHT, MapBase
from paperroute.core.tiles import (
    POINT_HALF_HEIGHT,
    STREET_HEIGHT,
    LabelKind,
    StreetLabel,
    text_box,
)
from paperroute.ports import TileRepository
from paperroute.render.canvas import Box, Face
from paperroute.render.map_base import HALO, window_of
from paperroute.render.map_corners import credit_box, scale_box
from paperroute.render.map_labels import (
    KNOCKOUT_SQUARE,
    PLACE_HALO,
    label_query,
)
from paperroute.render.map_marks import Window

EDGE: Final[float] = 1.0  # the corner boxes' dark edge


@dataclass(frozen=True)
class KeepOut:
    """Boxes on a map, in map pixels, that numbers keep clear of.

    Attributes
    ----------
    places : tuple[Box, ...]
        Suburb and station names, station squares and the corner boxes;
        nothing may cover them.
    streets : tuple[Box, ...]
        Street names; a number may cover one when it has nowhere else.
    """

    places: tuple[Box, ...]
    streets: tuple[Box, ...]


NO_KEEP_OUT: Final[KeepOut] = KeepOut(places=(), streets=())


def grown(*, box: Box, by: float) -> Box:
    """
    Grow a box on every side.

    Parameters
    ----------
    box : Box
        Left, top, right, bottom.
    by : float
        Pixels added each side.

    Returns
    -------
    Box
        The grown box.
    """
    return box[0] - by, box[1] - by, box[2] + by, box[3] + by


def on_map(*, box: Box, width: int) -> bool:
    """
    Check whether a box reaches onto the map.

    Parameters
    ----------
    box : Box
        Left, top, right, bottom in map pixels.
    width : int
        Map width in pixels.

    Returns
    -------
    bool
        True when it shares area with the map.
    """
    return (
        box[0] < width and box[2] > 0 and box[1] < MAP_HEIGHT and box[3] > 0
    )


def name_box(
    *, label: StreetLabel, window: Window, height: float, halo: float,
) -> Box:
    """
    Bound a label's text on the map, grown by its halo.

    Parameters
    ----------
    label : StreetLabel
        The label, in global pixels.
    window : Window
        The map's top-left corner in global pixels.
    height : float
        Text height in pixels.
    halo : float
        Pixels added each side.

    Returns
    -------
    Box
        Left, top, right, bottom in map pixels.
    """
    return grown(
        box=text_box(
            centre=(label.x - window[0], label.y - window[1]),
            width=label.width,
            height=height,
            angle=label.angle,
        ),
        by=halo,
    )


def point_height(*, face: Face) -> float:
    """
    Give a place name's box height.

    Parameters
    ----------
    face : Face
        The name's face.

    Returns
    -------
    float
        Pixels, scaled with the face as the tiles build places them.
    """
    return 2 * POINT_HALF_HEIGHT * face.px


def station_boxes(*, label: StreetLabel, window: Window) -> list[Box]:
    """
    Give a station's name box and its square.

    Parameters
    ----------
    label : StreetLabel
        The station's label; its start is the square's centre.
    window : Window
        The map's top-left corner in global pixels.

    Returns
    -------
    list[Box]
        The name on its halo, then the paper square.
    """
    x, y = label.start[0] - window[0], label.start[1] - window[1]
    half = KNOCKOUT_SQUARE[0]
    name = name_box(
        label=label,
        window=window,
        height=point_height(face=STATION_FACE),
        halo=PLACE_HALO,
    )
    return [name, (x - half, y - half, x + half, y + half)]


def corner_boxes(*, base: MapBase, tiles: TileRepository) -> list[Box]:
    """
    Give the corner boxes the base draws, with their dark edges.

    Parameters
    ----------
    base : MapBase
        The base map key.
    tiles : TileRepository
        Says whether tiles are installed, which adds the credit.

    Returns
    -------
    list[Box]
        The scale box, then the credit box when tiles are installed.
    """
    boxes = [scale_box(mpp=base.mpp)]
    if tiles.version() is not None:
        boxes.append(credit_box(width=base.width))
    return [grown(box=box, by=EDGE) for box in boxes]


def place_boxes(
    *, base: MapBase, tiles: TileRepository, window: Window,
) -> list[Box]:
    """
    Give the suburb and station boxes near the map.

    Parameters
    ----------
    base : MapBase
        The base map key.
    tiles : TileRepository
        Where the labels come from.
    window : Window
        The map's top-left corner in global pixels.

    Returns
    -------
    list[Box]
        Suburb names at the zooms that show them, then each station's
        name and square.
    """
    query = label_query(window=window, width=base.width)
    suburbs = (
        tiles.street_labels(zoom=base.mpp, box=query, kind=LabelKind.SUBURB)
        if base.mpp in SUBURB_ZOOMS
        else []
    )
    stations = tiles.street_labels(
        zoom=base.mpp, box=query, kind=LabelKind.STATION,
    )
    height = point_height(face=SUBURB_FACE)
    return [
        *(
            name_box(
                label=label, window=window, height=height, halo=PLACE_HALO,
            )
            for label in suburbs
        ),
        *(
            box
            for label in stations
            for box in station_boxes(label=label, window=window)
        ),
    ]


def street_boxes(
    *, base: MapBase, tiles: TileRepository, window: Window,
) -> list[Box]:
    """
    Give the street name boxes near the map.

    Parameters
    ----------
    base : MapBase
        The base map key.
    tiles : TileRepository
        Where the labels come from.
    window : Window
        The map's top-left corner in global pixels.

    Returns
    -------
    list[Box]
        Each name's turned text bounds, grown by its halo.
    """
    return [
        name_box(label=label, window=window, height=STREET_HEIGHT, halo=HALO)
        for label in tiles.street_labels(
            zoom=base.mpp,
            box=label_query(window=window, width=base.width),
            kind=LabelKind.STREET,
        )
    ]


def keep_out(*, base: MapBase, tiles: TileRepository) -> KeepOut:
    """
    Gather the boxes a map's numbers keep clear of.

    Parameters
    ----------
    base : MapBase
        The base map key.
    tiles : TileRepository
        Where the labels and the tile version come from.

    Returns
    -------
    KeepOut
        Map-pixel boxes that reach onto the map; only the corner boxes
        when the map has no centre.

    Raises
    ------
    sqlite3.Error
        When the tiles file can't be read.
    """
    corners = tuple(corner_boxes(base=base, tiles=tiles))
    if base.centre is None:
        return KeepOut(places=corners, streets=())
    window = window_of(base=base)
    places = place_boxes(base=base, tiles=tiles, window=window)
    streets = street_boxes(base=base, tiles=tiles, window=window)
    return KeepOut(
        places=(
            *(box for box in places if on_map(box=box, width=base.width)),
            *corners,
        ),
        streets=tuple(
            box for box in streets if on_map(box=box, width=base.width)
        ),
    )
