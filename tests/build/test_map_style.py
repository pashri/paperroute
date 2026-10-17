"""Tests for the dense map style rules."""

import pytest

from paperroute.build.map_style import (
    DARK,
    INK,
    LIGHT,
    PAPER,
    ROAD_LAYERS,
    ROAD_WIDTHS,
    named_roads,
    road_style,
    transit_style,
)

ZOOMS = (3, 5, 8, 14, 45)


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


def test_minor_road_is_light_at_14() -> None:
    """Minor roads turn light grey at 14 m/px."""
    assert road_style(layer='road_minor', mpp=14) == (1, LIGHT)
    assert road_style(layer='road_minor', mpp=8) == (1, DARK)
    assert ROAD_WIDTHS['road_primary'] == (7, 6, 5, 3, 2)


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
    assert transit_style(layer='rail', mpp=14)[0][0] == 3
    assert transit_style(layer='other', mpp=14) == []
