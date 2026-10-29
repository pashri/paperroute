"""Place names knocked out of the map, so the route can lie over them."""

from typing import Final

from paperroute.build.map_style import (
    DARK,
    STATION_FACE,
    SUBURB_FACE,
    SUBURB_ZOOMS,
)
from paperroute.core.map_view import MAP_HEIGHT, MapBase
from paperroute.core.tiles import LABEL_REACH, LabelKind, StreetLabel
from paperroute.ports import TileRepository
from paperroute.render.canvas import INK, PAPER, Canvas
from paperroute.render.map_marks import Window

PLACE_HALO: Final[int] = 3
KNOCKOUT_SQUARE: Final[tuple[int, int]] = (7, 5)  # paper, then ink


def label_query(
    *,
    window: Window,
    width: int,
) -> tuple[float, float, float, float]:
    """
    Give the box to fetch labels for, grown by the label reach.

    Parameters
    ----------
    window : Window
        The map's top-left corner in global pixels.
    width : int
        Map width in pixels.

    Returns
    -------
    tuple[float, float, float, float]
        Left, top, right and bottom in global pixels.
    """
    left, top = window
    return (
        left - LABEL_REACH,
        top - LABEL_REACH,
        left + width + LABEL_REACH,
        top + MAP_HEIGHT + LABEL_REACH,
    )


def draw_place_knockouts(
    canvas: Canvas, *, base: MapBase, window: Window, tiles: TileRepository,
) -> None:
    """
    Redraw suburb and station names on paper halos over the tiles.

    Suburb names are drawn only at the zooms that show them.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key.
    window : Window
        The map's top-left corner in global pixels.
    tiles : TileRepository
        Where the labels come from.
    """
    box = label_query(window=window, width=base.width)
    suburbs = (
        tiles.street_labels(zoom=base.mpp, box=box, kind=LabelKind.SUBURB)
        if base.mpp in SUBURB_ZOOMS
        else []
    )
    for label in suburbs:
        canvas.text(
            at=(label.x - window[0], label.y - window[1]),
            text=label.name,
            face=SUBURB_FACE,
            grey=DARK,
            anchor='mm',
            halo=PLACE_HALO,
            angle=label.angle,
        )
    for label in tiles.street_labels(
        zoom=base.mpp, box=box, kind=LabelKind.STATION,
    ):
        draw_station(canvas, label=label, window=window)


def draw_station(
    canvas: Canvas, *, label: StreetLabel, window: Window,
) -> None:
    """
    Draw a station's ink square on paper, then its name on a halo.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    label : StreetLabel
        The station's label; its start is the square's centre.
    window : Window
        The map's top-left corner in global pixels.
    """
    x, y = label.start[0] - window[0], label.start[1] - window[1]
    for half, grey in zip(KNOCKOUT_SQUARE, (PAPER, INK), strict=True):
        canvas.rect(box=(x - half, y - half, x + half, y + half), grey=grey)
    draw_station_name(canvas, label=label, window=window, halo=PLACE_HALO)


def draw_station_name(
    canvas: Canvas, *, label: StreetLabel, window: Window, halo: int,
) -> None:
    """
    Draw a station's name in ink on a paper halo.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    label : StreetLabel
        The station's label, in global pixels.
    window : Window
        The map's top-left corner in global pixels.
    halo : int
        Halo width in pixels.
    """
    canvas.text(
        at=(label.x - window[0], label.y - window[1]),
        text=label.name,
        face=STATION_FACE,
        grey=INK,
        anchor='mm',
        halo=halo,
    )
