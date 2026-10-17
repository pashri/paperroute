"""Tests for the dense map style rules."""

import pytest

from paperroute.build.map_style import (
    AREA_GREYS,
    CITY_PLACES,
    DARK,
    LIGHT,
    PLACE_KINDS,
    RESERVE_PX,
    RESERVE_ZOOMS,
    RIVER_STYLE,
    ROAD_LAYERS,
    ROAD_WIDTHS,
    STATION_FACE,
    STATION_ZOOMS,
    STREET_FACE,
    STREET_REPEAT_PX,
    STREET_ZOOMS,
    SUBURB_FACE,
    SUBURB_ZOOMS,
    named_roads,
    road_style,
    transit_style,
)
from paperroute.core.tiles import ZOOMS
from paperroute.render.canvas import INK, PAPER, Face


def test_minor_roads_hide_at_city_zoom() -> None:
    """Minor roads have no width at 45 m/px."""
    assert road_style(layer='road_minor', mpp=45)[0] == 0


@pytest.mark.parametrize('mpp', ZOOMS)
def test_main_roads_are_bolder_than_minor(mpp: int) -> None:
    """A motorway is never thinner than a minor road, and is wider."""
    minor = road_style(layer='road_minor', mpp=mpp)[0]
    motorway = road_style(layer='road_motorway', mpp=mpp)[0]
    assert motorway > minor


def test_city_zoom_is_grey() -> None:
    """No road is drawn in ink at 45 m/px."""
    greys = {road_style(layer=layer, mpp=45)[1] for layer in ROAD_LAYERS}
    assert INK not in greys
    assert greys == {DARK, LIGHT}
    assert [road_style(layer=layer, mpp=45) for layer in ROAD_LAYERS] == [
        (0, LIGHT),
        (1, LIGHT),
        (1, LIGHT),
        (2, LIGHT),
        (2, DARK),
        (3, DARK),
    ]


def test_minor_road_is_light_at_14() -> None:
    """Minor roads turn light grey at 14 m/px."""
    assert road_style(layer='road_minor', mpp=14) == (1, LIGHT)
    assert road_style(layer='road_minor', mpp=8) == (1, DARK)
    assert ROAD_WIDTHS['road_primary'] == (7, 6, 5, 3, 2)
    assert road_style(layer='road_tertiary', mpp=14) == (1, DARK)
    assert road_style(layer='road_tertiary', mpp=8) == (2, INK)


@pytest.mark.parametrize('mpp', ZOOMS)
def test_road_widths_do_not_decrease_with_class(mpp: int) -> None:
    """Each road class is at least as wide as the one below it."""
    widths = [road_style(layer=layer, mpp=mpp)[0] for layer in ROAD_LAYERS]
    assert widths == sorted(widths)


def test_road_style_rejects_unknown_zoom() -> None:
    """A zoom that is not a step raises a ValueError naming it."""
    with pytest.raises(ValueError, match='unsupported zoom: 7'):
        road_style(layer='road_minor', mpp=7)


def test_label_zones_match_the_spec() -> None:
    """Label zooms, place kinds and reserve rules are the chosen ones."""
    assert STREET_ZOOMS == {3, 5, 8, 14}
    assert SUBURB_ZOOMS == {8, 14, 45}
    assert STATION_ZOOMS == {3, 5, 8, 14, 45}
    assert CITY_PLACES == {'suburb', 'town'}
    assert PLACE_KINDS == {'suburb', 'town', 'locality', 'village'}
    assert RESERVE_ZOOMS == {3, 5, 8}
    assert RESERVE_PX == 24
    assert STREET_REPEAT_PX == 384


def test_faces_and_areas_match_the_spec() -> None:
    """Faces, area greys and the river stroke are the chosen ones."""
    assert STREET_FACE == Face(weight=700, px=12)
    assert SUBURB_FACE == Face(weight=700, px=13)
    assert STATION_FACE == Face(weight=800, px=14)
    assert AREA_GREYS == {'park': LIGHT, 'water': DARK, 'sea': DARK}
    assert RIVER_STYLE == (2, DARK)


def test_named_roads_by_zoom() -> None:
    """Street names cover every class at 8, main ones at 14, none at 45."""
    assert named_roads(mpp=3) == frozenset(ROAD_LAYERS)
    assert named_roads(mpp=8) == frozenset(ROAD_LAYERS)
    assert named_roads(mpp=14) == frozenset(ROAD_LAYERS[2:])
    assert named_roads(mpp=45) == frozenset()


def test_rail_has_paper_dashes_below_city() -> None:
    """Rail is an ink line with a dashed paper core below 45 m/px."""
    assert transit_style(layer='rail', mpp=8) == [
        (4, INK, None),
        (2, PAPER, (6, 6)),
    ]
    assert transit_style(layer='subway', mpp=45) == [(2, DARK, None)]


def test_transit_style_other_layers() -> None:
    """Tunnels, ferries and light rail have their own strokes."""
    assert transit_style(layer='rail_tunnel', mpp=5) == [
        (2, DARK, (4, 4)),
    ]
    assert transit_style(layer='ferry', mpp=5) == [(1, INK, (4, 3))]
    assert transit_style(layer='light_rail', mpp=45) == [(1, INK, None)]
    assert transit_style(layer='light_rail', mpp=14) == [(2, INK, None)]
    assert transit_style(layer='rail', mpp=3)[0][0] == 5
    assert transit_style(layer='rail', mpp=5)[0][0] == 5
    assert transit_style(layer='rail', mpp=14)[0][0] == 3
    assert transit_style(layer='other', mpp=14) == []
