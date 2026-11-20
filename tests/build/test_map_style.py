"""Tests for the dense map style rules."""

import pytest

from paperroute_device.render.canvas import INK, PAPER, Face
from paperroute_transit.build.map_style import (
    AREA_GREYS,
    AREA_ORDER,
    CITY_PLACES,
    DARK,
    LIGHT,
    MAIN_ROAD_PX,
    OVER_ROADS,
    RIVER_STYLE,
    ROAD_LAYERS,
    ROAD_WIDTHS,
    STATION_FACE,
    STREET_FACE,
    STREET_REPEAT_PX,
    STREET_ZOOMS,
    SUBURB_FACE,
    UNDER_ROADS,
    Stroke,
    area_drawn,
    extra_strokes,
    named_roads,
    road_style,
    transit_style,
)
from paperroute_transit.core.map_places import PLACE_KINDS
from paperroute_transit.core.tiles import STATION_ZOOMS, SUBURB_ZOOMS, ZOOMS


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
        (1, DARK),
        (1, LIGHT),
        (2, LIGHT),
        (2, DARK),
        (3, DARK),
    ]


def test_minor_road_is_light_at_14() -> None:
    """Minor roads turn light grey at 14 m/px."""
    assert road_style(layer='road_minor', mpp=14) == (1, LIGHT)
    assert road_style(layer='road_minor', mpp=8) == (1, DARK)
    assert ROAD_WIDTHS['road_primary'] == (7, 6, 5, 3, 2, 2)
    assert road_style(layer='road_tertiary', mpp=14) == (1, DARK)
    assert road_style(layer='road_tertiary', mpp=8) == (2, INK)


@pytest.mark.parametrize(
    ('mpp', 'layer', 'grey'),
    [
        (3, 'road_tertiary', DARK),
        (5, 'road_tertiary', DARK),
        (8, 'road_tertiary', INK),
        (8, 'road_secondary', DARK),
        (14, 'road_secondary', INK),
        (14, 'road_primary', DARK),
        (3, 'road_minor', DARK),
        (14, 'road_minor', LIGHT),
        (25, 'road_tertiary', LIGHT),
        (25, 'road_secondary', DARK),
        (45, 'road_trunk', DARK),
        (45, 'road_minor', LIGHT),
    ],
)
def test_s1_greys(mpp: int, layer: str, grey: int) -> None:
    """Ink roads as wide as a main road turn dark grey."""
    assert road_style(layer=layer, mpp=mpp)[1] == grey


@pytest.mark.parametrize('mpp', ZOOMS)
def test_no_ink_road_is_main_road_wide(mpp: int) -> None:
    """Every ink road is thinner than a main road."""
    assert all(
        width < MAIN_ROAD_PX
        for width, grey in (
            road_style(layer=layer, mpp=mpp) for layer in ROAD_LAYERS
        )
        if grey == INK
    )


def test_25_widths() -> None:
    """At 25 m/px primary roads are two dark pixels, minor one light."""
    assert road_style(layer='road_primary', mpp=25) == (2, DARK)
    assert road_style(layer='road_minor', mpp=25) == (1, LIGHT)


def test_places_and_stations_at_25() -> None:
    """Suburbs and stations are labelled at 25 m/px, streets are not."""
    assert 25 in SUBURB_ZOOMS
    assert 25 in STATION_ZOOMS
    assert 25 not in STREET_ZOOMS
    assert named_roads(mpp=25) == frozenset()


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
    """Label zooms and place kinds are the chosen ones."""
    assert STREET_ZOOMS == {3, 5, 8, 14}
    assert SUBURB_ZOOMS == {8, 14, 25, 45}
    assert STATION_ZOOMS == {3, 5, 8, 14, 25, 45}
    assert CITY_PLACES == {'suburb', 'town'}
    assert PLACE_KINDS == {'suburb', 'town', 'locality', 'village'}
    assert STREET_REPEAT_PX == 384


def test_faces_and_areas_match_the_spec() -> None:
    """Faces, area greys and the river stroke are the chosen ones."""
    assert STREET_FACE == Face(weight=700, px=12)
    assert SUBURB_FACE == Face(weight=700, px=13)
    assert STATION_FACE == Face(weight=800, px=14)
    assert AREA_GREYS == {
        'park': LIGHT,
        'water': DARK,
        'sea': DARK,
        'green': LIGHT,
        'apron': PAPER,
        'platform': DARK,
    }
    assert AREA_ORDER.keys() == AREA_GREYS.keys()
    assert RIVER_STYLE == (2, DARK)


def test_busway_strokes() -> None:
    """Busways follow tertiary roads, dark from 14 m/px out."""
    assert ROAD_LAYERS.index('road_busway') == (
        ROAD_LAYERS.index('road_tertiary') + 1
    )
    assert [road_style(layer='road_busway', mpp=mpp) for mpp in ZOOMS] == [
        (4, DARK),
        (3, DARK),
        (2, INK),
        (2, DARK),
        (1, DARK),
        (1, DARK),
    ]


def test_busways_are_named_at_8_and_closer() -> None:
    """Busways are named like streets at 8 m/px, not at 14."""
    assert 'road_busway' in named_roads(mpp=8)
    assert named_roads(mpp=14) == {
        'road_secondary',
        'road_primary',
        'road_trunk',
        'road_motorway',
    }


def test_named_roads_by_zoom() -> None:
    """Street names cover every class at 8, main ones at 14, none at 45."""
    assert named_roads(mpp=3) == frozenset(ROAD_LAYERS)
    assert named_roads(mpp=8) == frozenset(ROAD_LAYERS)
    assert named_roads(mpp=14) == {
        'road_secondary',
        'road_primary',
        'road_trunk',
        'road_motorway',
    }
    assert named_roads(mpp=45) == frozenset()


def test_rail_has_paper_dashes_below_city() -> None:
    """Rail is an ink line with a dashed paper core below 45 m/px."""
    assert transit_style(layer='rail', mpp=8) == [
        (4, INK, None),
        (2, PAPER, (6, 6)),
    ]
    assert transit_style(layer='subway', mpp=45) == [(2, DARK, None)]


@pytest.mark.parametrize(
    ('mpp', 'strokes'),
    [
        (3, [(1, INK, None), (6, INK, (1, 7))]),
        (5, [(1, INK, None), (6, INK, (1, 7))]),
        (8, [(1, INK, None), (5, INK, (1, 6))]),
        (14, [(1, INK, None), (5, INK, (1, 6))]),
        (25, [(1, INK, None), (4, INK, (1, 5))]),
        (45, [(1, INK, None)]),
    ],
)
def test_light_rail_is_ticked(mpp: int, strokes: list[Stroke]) -> None:
    """Light rail is a 1 px ink line with ink ticks below 45 m/px."""
    assert transit_style(layer='light_rail', mpp=mpp) == strokes
    assert transit_style(layer='light_rail_tunnel', mpp=mpp) == [
        (2, DARK, (4, 4)),
    ]


def test_transit_style_other_layers() -> None:
    """Tunnels, ferries and light rail have their own strokes."""
    assert transit_style(layer='rail_tunnel', mpp=5) == [
        (2, DARK, (4, 4)),
    ]
    assert transit_style(layer='ferry', mpp=5) == [(1, INK, (4, 3))]
    assert transit_style(layer='rail', mpp=3)[0][0] == 5
    assert transit_style(layer='rail', mpp=5)[0][0] == 5
    assert transit_style(layer='rail', mpp=14)[0][0] == 3
    assert transit_style(layer='other', mpp=14) == []


@pytest.mark.parametrize(
    ('layer', 'mpp', 'strokes'),
    [
        ('path', 3, [(1, INK, (2, 2))]),
        ('path', 5, [(1, INK, (2, 2))]),
        ('path', 8, []),
        ('service', 3, [(1, LIGHT, None)]),
        ('service', 5, [(1, LIGHT, None)]),
        ('service', 8, []),
        ('runway', 8, []),
        ('runway', 14, [(3, PAPER, None)]),
        ('runway', 25, [(2, PAPER, None)]),
        ('runway', 45, [(1, PAPER, None)]),
        ('taxiway', 14, [(1, PAPER, None)]),
        ('taxiway', 25, []),
        ('road_minor', 3, []),
    ],
)
def test_extra_strokes(layer: str, mpp: int, strokes: list[Stroke]) -> None:
    """Each extra line layer has its strokes at its own zooms only."""
    assert extra_strokes(layer=layer, mpp=mpp) == strokes


def test_under_roads_order() -> None:
    """Service roads, paths, taxiways and runways are stroked in order."""
    assert UNDER_ROADS == ('service', 'path', 'taxiway', 'runway')


@pytest.mark.parametrize(
    ('layer', 'mpp', 'drawn'),
    [
        ('platform', 3, True),
        ('platform', 5, True),
        ('platform', 8, False),
        ('apron', 8, False),
        ('apron', 14, True),
        ('apron', 45, True),
        ('green', 3, True),
        ('green', 45, True),
        ('park', 45, True),
        ('residential', 3, False),
    ],
)
def test_area_drawn(layer: str, mpp: int, drawn: bool) -> None:
    """Each area layer is filled at its own zooms."""
    assert area_drawn(layer=layer, mpp=mpp) is drawn


def test_platform_strokes_and_order() -> None:
    """Open platforms are 2 px dark grey at 3 and 5 m/px, over roads."""
    assert extra_strokes(layer='platform', mpp=3) == [(2, DARK, None)]
    assert extra_strokes(layer='platform', mpp=5) == [(2, DARK, None)]
    assert extra_strokes(layer='platform', mpp=8) == []
    assert OVER_ROADS == ('platform',)
