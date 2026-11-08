"""Tests for tagging repeated route numbers with places."""

import re
from typing import Final

from paperroute_transit.build.route_tags import CatalogueRoute, tags_of
from paperroute_transit.core.city import PlaceNames
from paperroute_transit.core.geo import Point, Projection, to_metres

TOWN: Final[PlaceNames] = PlaceNames(
    centres=(('Town', 0.1, 51.5, 10),),
    suffix=re.compile(r'\s+Station$'),
    short={},
)
PROJECTION: Final[Projection] = Projection(
    origin_lon=0.1,
    origin_lat=51.5,
    grid_lon=-1.0,
    grid_lat=52.5,
)


def route(*, key: str, long_name: str, middle: Point) -> CatalogueRoute:
    """
    Build a merged bus route numbered 10.

    Parameters
    ----------
    key : str
        Route key.
    long_name : str
        Route long name.
    middle : Point
        Median station point in metres.

    Returns
    -------
    CatalogueRoute
        The route.
    """
    return CatalogueRoute(
        route_key=key,
        kind='bus',
        number='10',
        long_name=long_name,
        agency='A1',
        middle=middle,
    )


def test_other_places_tag_other_centres() -> None:
    """A route inside another city's centre is tagged with its label."""
    inside = route(
        key='inside',
        long_name='Alpha to Beta',
        middle=to_metres(lon=0.1, lat=51.5, projection=PROJECTION),
    )
    outside = route(
        key='outside',
        long_name='Gamma Station to Delta',
        middle=to_metres(lon=1.5, lat=51.5, projection=PROJECTION),
    )
    tags = tags_of(
        routes=[inside, outside],
        towns={},
        projection=PROJECTION,
        places=TOWN,
    )
    assert tags == {'inside': 'Town', 'outside': 'Gamma'}
