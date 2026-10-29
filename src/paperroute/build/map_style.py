"""Which map features and labels appear at each zoom, and how they look."""

from typing import Final

from paperroute.core.tiles import CITY, ZOOMS
from paperroute.render.canvas import INK, PAPER, Face

DARK: Final[int] = 85
LIGHT: Final[int] = 170
SUPERSAMPLE: Final[int] = 4  # sub-pixels per map pixel along each side

ROAD_LAYERS: Final[tuple[str, ...]] = (
    'road_minor',
    'road_tertiary',
    'road_secondary',
    'road_primary',
    'road_trunk',
    'road_motorway',
)
ROAD_WIDTHS: Final[dict[str, tuple[int, ...]]] = {
    'road_minor': (3, 2, 1, 1, 1, 0),
    'road_tertiary': (4, 3, 2, 1, 1, 1),
    'road_secondary': (5, 4, 3, 2, 1, 1),
    'road_primary': (7, 6, 5, 3, 2, 2),
    'road_trunk': (8, 7, 5, 4, 3, 2),
    'road_motorway': (9, 8, 6, 4, 3, 3),
}
MAIN_ROAD_PX: Final[int] = 3  # ink roads this wide or wider turn dark
TOWN: Final[int] = 25  # m/px, the level between streets and the city
TRANSIT_LAYERS: Final[tuple[str, ...]] = (
    'rail_tunnel',
    'subway_tunnel',
    'light_rail_tunnel',
    'ferry',
    'light_rail',
    'subway',
    'rail',
)
AREA_GREYS: Final[dict[str, int]] = {
    'park': LIGHT,
    'water': DARK,
    'sea': DARK,
}
RIVER_STYLE: Final[tuple[int, int]] = (2, DARK)

STREET_ZOOMS: Final[frozenset[int]] = frozenset({3, 5, 8, 14})
CITY_PLACES: Final[frozenset[str]] = frozenset({'suburb', 'town'})
PLACE_KINDS: Final[frozenset[str]] = frozenset(
    {'suburb', 'town', 'locality', 'village'}
)
STREET_REPEAT_PX: Final[int] = 384

STREET_FACE: Final[Face] = Face(weight=700, px=12)
SUBURB_FACE: Final[Face] = Face(weight=700, px=13)
STATION_FACE: Final[Face] = Face(weight=800, px=14)

Stroke = tuple[float, int, tuple[float, float] | None]


def road_style(*, layer: str, mpp: int) -> tuple[int, int]:
    """
    Give the width and grey of a road class at a zoom.

    Parameters
    ----------
    layer : str
        Road layer name.
    mpp : int
        Metres per pixel, one of the zoom steps.

    Returns
    -------
    tuple[int, int]
        Stroke width (0 hides the road) and grey level.

    Raises
    ------
    ValueError
        If ``mpp`` is not one of the zoom steps.
    """
    if mpp not in ZOOMS:
        raise ValueError(f'unsupported zoom: {mpp} m/px')
    width = ROAD_WIDTHS[layer][ZOOMS.index(mpp)]
    return width, road_grey(layer=layer, mpp=mpp)


def road_grey(*, layer: str, mpp: int) -> int:
    """
    Give the grey of a road class at a zoom.

    Parameters
    ----------
    layer : str
        Road layer name.
    mpp : int
        Metres per pixel, one of the zoom steps.

    Returns
    -------
    int
        Grey level: at the city zoom, dark for trunk roads and
        motorways and light for the rest; at 25 m/px, light for minor
        and tertiary roads and dark for the rest. Below that, minor
        roads are light from 14 m/px and tertiary roads dark from 14
        m/px; other roads are ink, or dark when at least
        ``MAIN_ROAD_PX`` wide.
    """
    if mpp == CITY:
        return DARK if layer in ('road_trunk', 'road_motorway') else LIGHT
    if mpp == TOWN:
        return LIGHT if layer in ('road_minor', 'road_tertiary') else DARK
    if layer == 'road_minor':
        return LIGHT if mpp >= 14 else DARK
    if layer == 'road_tertiary' and mpp >= 14:
        return DARK
    width = ROAD_WIDTHS[layer][ZOOMS.index(mpp)]
    return DARK if width >= MAIN_ROAD_PX else INK


def transit_style(*, layer: str, mpp: int) -> list[Stroke]:
    """
    Give the strokes for a transit line, in drawing order.

    Parameters
    ----------
    layer : str
        Transit layer name.
    mpp : int
        Metres per pixel.

    Returns
    -------
    list[tuple[float, int, tuple[float, float] | None]]
        Width, grey and dash pattern of each stroke. Unknown layers have
        none.
    """
    if layer.endswith('_tunnel'):
        return [(2, DARK, (4, 4))]
    if layer == 'ferry':
        return [(1, INK, (4, 3))]
    if layer in ('rail', 'subway'):
        if mpp == CITY:
            return [(2, DARK, None)]
        width = 5 if mpp <= 5 else 4 if mpp == 8 else 3
        return [(width, INK, None), (width - 2, PAPER, (6, 6))]
    if layer == 'light_rail':
        return [(2 if mpp < CITY else 1, INK, None)]
    return []


def named_roads(*, mpp: int) -> frozenset[str]:
    """
    Give the road layers whose streets are named at a zoom.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    frozenset[str]
        Every road layer at 8 and under, secondary and up at 14, none at
        city zoom.
    """
    if mpp <= 8:
        return frozenset(ROAD_LAYERS)
    if mpp == 14:
        return frozenset(ROAD_LAYERS[2:])
    return frozenset()
