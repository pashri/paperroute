"""Tests that every zoom-keyed style table follows the zoom list."""

from paperroute.build.map_style import ROAD_WIDTHS, STREET_ZOOMS
from paperroute.core.tiles import STATION_ZOOMS, SUBURB_ZOOMS, ZOOMS
from paperroute.render.map_marks import ROUTE_STYLE


def test_style_tables_cover_the_zooms() -> None:
    """Tables keyed by zoom cover exactly the zooms; label sets fit in."""
    assert set(ROUTE_STYLE) == set(ZOOMS)
    assert {len(widths) for widths in ROAD_WIDTHS.values()} == {len(ZOOMS)}
    for zooms in (STREET_ZOOMS, SUBURB_ZOOMS, STATION_ZOOMS):
        assert zooms <= set(ZOOMS)
