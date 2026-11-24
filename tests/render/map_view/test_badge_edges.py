"""Tests for map view: badge edges."""

import math
from dataclasses import replace

from paperroute_device.render.canvas import INK, PAPER
from paperroute_device.render.frame import HEADER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    Marker,
    TripMarks,
    screen_point,
)
from paperroute_transit.core.tiles import ZOOMS
from paperroute_transit.render.chrome import BANNER
from paperroute_transit.render.map_view import (
    draw_map_view,
    edge_boxes,
    end_boxes,
    stop_boxes,
)
from paperroute_transit.render.markers import (
    END_HALF,
    badge_half,
    grown_box,
    nearest_first,
    overlaps,
    place,
)
from tests.render.map_view.helpers import (
    CENTRE,
    MPP,
    VIEW,
    live,
    paper,
    stop_pixel,
)


def test_trip_origin_gets_the_stop() -> None:
    """With trip marks, the From is drawn at the trip's origin."""
    origin = (CENTRE[0] - 800.0, CENTRE[1])
    trip = TripMarks(
        origin=origin, second=(), walk=None, change=None, end=None,
    )
    view = replace(VIEW, base=replace(VIEW.base, trip=trip))
    frame = draw_map_view(view=view, base=paper())
    x, y = stop_pixel()
    assert frame[y, x + 9] == PAPER
    assert frame[y, x - 100 + 12] == INK


def test_badges_keep_clear_of_the_to() -> None:
    """A badge on the To slides off its 15 px box."""
    end = (CENTRE[0] + 800.0, CENTRE[1])
    trip = TripMarks(
        origin=CENTRE, second=(), walk=None, change=None, end=end,
    )
    map_base = replace(VIEW.base, trip=trip)
    x, y = screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=end,
        projection=SYDNEY_PROJECTION,
    )
    boxes = end_boxes(base=map_base)
    assert (x - END_HALF, y - END_HALF, x + END_HALF, y + END_HALF) in boxes
    (placed,) = place(markers=[live(at=(x, y + 6))], reserved=boxes)
    assert placed.centre != (x, y + 6)


def test_plain_from_drawn_by_overlay_at_origin() -> None:
    """With a fitted centre away from the From, the stop is at the From."""
    origin = (CENTRE[0] - 800.0, CENTRE[1])
    end = (CENTRE[0] + 800.0, CENTRE[1])
    trip = TripMarks(
        origin=origin, second=(), walk=None, change=None, end=end,
    )
    view = replace(VIEW, base=replace(VIEW.base, trip=trip))
    frame = draw_map_view(view=view, base=paper())
    x, y = stop_pixel()
    assert frame[y, x - 100] == INK
    assert frame[y, x - 100 + 12] == INK
    assert frame[y, x] == PAPER


def test_stop_boxes_are_one_px_wider_than_the_ring() -> None:
    """At 8 m/px each solid ring, 8.5 px, gets a box 9.5 px each side."""
    stop = (CENTRE[0] + 80.0, CENTRE[1])
    map_base = replace(VIEW.base, stops=(stop,))
    x, y = screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=stop,
        projection=SYDNEY_PROJECTION,
    )
    assert stop_boxes(base=map_base) == [(x - 9.5, y - 9.5, x + 9.5, y + 9.5)]


def test_no_centre_has_no_boxes() -> None:
    """Without a centre nothing is drawn, so nothing is kept clear."""
    trip = TripMarks(
        origin=CENTRE, second=(), walk=None, change=CENTRE, end=CENTRE,
    )
    map_base = replace(VIEW.base, centre=None, trip=trip, stops=(CENTRE,))
    assert end_boxes(base=map_base) == []
    assert stop_boxes(base=map_base) == []


def test_badge_on_a_from_under_the_banner_slides_down() -> None:
    """A bus at a From near the banner slides down, not under it."""
    top = BANNER + 24.0
    origin = (CENTRE[0], CENTRE[1] + (MAP_HEIGHT / 2 - top) * MPP)
    trip = TripMarks(
        origin=origin, second=(), walk=None, change=None, end=None,
    )
    view = replace(
        VIEW,
        base=replace(VIEW.base, trip=trip),
        markers=(live(at=(MAP_WIDTH / 2, top)),),
        banner='Showing timetable only',
    )
    frame = draw_map_view(view=view, base=paper())
    edge = math.floor(MAP_WIDTH / 2 - badge_half(route='606')[0]) + 2
    assert frame[math.floor(HEADER + top + 36), edge] == INK


def test_badge_near_the_right_edge_slides_inward() -> None:
    """A badge with only a sideways spot free never slides off the map."""
    x, y = MAP_WIDTH - 24.0, MAP_HEIGHT / 2
    blocked = [(x - 5, y - 60, x + 5, y - 5), (x - 5, y + 5, x + 5, y + 60)]
    (placed,) = place(
        markers=[live(at=(x, y))],
        reserved=[
            (x - 5, y - 5, x + 5, y + 5),
            *blocked,
            *edge_boxes(width=MAP_WIDTH, banner=False),
        ],
    )
    assert placed.centre == (x - 40, y)


def test_number_near_the_right_edge_goes_left() -> None:
    """A route number with no room before the map's edge goes left."""
    x, y = MAP_WIDTH - 15.0, MAP_HEIGHT / 2 + 60.0
    other = Marker(
        route='401', x=x, y=y, bearing=None, live=True, pinned=False,
    )
    view = replace(
        VIEW,
        base=replace(VIEW.base, mpp=14),
        zoom=ZOOMS.index(14),
        markers=(other,),
    )
    frame = draw_map_view(view=view, base=paper())
    top = math.floor(HEADER + y)
    left = frame[top - 8 : top + 4, math.floor(x) - 40 : math.floor(x) - 8]
    assert (left == INK).any()


def test_badge_at_a_crowded_from_by_the_edge_still_shows() -> None:
    """A bus at a From in the top left corner, with a badge below, shows."""
    stop = (38.0, BANNER + 35.0)
    waiting = live(at=stop, route='T1')
    below = live(at=(42.0, stop[1] + 31.0), route='T5')
    reserved = [
        (stop[0] - 18, stop[1] - 18, stop[0] + 18, stop[1] + 18),
        *edge_boxes(width=MAP_WIDTH, banner=True),
    ]
    placed, *_ = place(
        markers=nearest_first(markers=[below, waiting], near=stop),
        reserved=reserved,
    )
    assert placed.marker == waiting
    assert placed.centre != stop
    box = grown_box(marker=waiting, centre=placed.centre)
    assert not any(overlaps(first=box, second=other) for other in reserved)
