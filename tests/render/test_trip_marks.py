"""Facts about a connection's marks on the map base (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_device.render.frame import HEADER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    TripMarks,
    screen_point,
)
from paperroute_transit.core.tiles import TileVariant
from paperroute_transit.render.map_base import draw_map_base, window_of
from paperroute_transit.render.map_marks import draw_stop, mark_scale
from paperroute_transit.render.trip_marks import (
    draw_trip_ends,
    draw_trip_legs,
    draw_trip_lines,
    draw_trip_points,
)
from tests.render.test_map_base import CENTRE as STOP
from tests.render.test_map_base import base as base_of

MPP: Final[int] = 8
CENTRE: Final[Point] = (0.0, 0.0)
MIDDLE: Final[tuple[int, int]] = (MAP_WIDTH // 2, MAP_HEIGHT // 2)
TRIP: Final[TripMarks] = TripMarks(
    origin=(-1600.0, 800.0),
    second=(((-1000.0, 400.0), (1000.0, 400.0)),),
    walk=((-2000.0, -1200.0), (-1600.0, -1200.0)),
    change=(800.0, -400.0),
    end=(1600.0, -800.0),
)


def drawn(*, trip: TripMarks | None) -> np.ndarray:
    """
    Draw a blank base with or without trip marks.

    Parameters
    ----------
    trip : TripMarks | None
        The marks.

    Returns
    -------
    np.ndarray
        The base's grey levels.
    """
    return draw_map_base(
        base=MapBase(
            centre=CENTRE,
            mpp=MPP,
            route=(),
            trip=trip,
            projection=SYDNEY_PROJECTION,
        ),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )


def test_second_leg_dashed() -> None:
    """Leg 2's line alternates ink and paper along its length."""
    grey = drawn(trip=TRIP)
    row = grey[MIDDLE[1] - 50, 160:360] == INK
    changes = np.count_nonzero(row[1:] != row[:-1])
    assert row.any()
    assert changes >= 10


def test_change_square_and_end_disc() -> None:
    """The change is a square of ink and the To a disc."""
    grey = drawn(trip=TRIP)
    x, y = MIDDLE[0] + 100, MIDDLE[1] + 50
    assert (grey[y - 6:y + 7, x - 6:x + 7] == INK).all()
    x, y = MIDDLE[0] + 200, MIDDLE[1] + 100
    assert grey[y, x] == INK
    assert grey[y, x + 11] == INK
    assert grey[y - 10, x - 10] != INK


def test_walk_dotted() -> None:
    """The walk is a dotted line between its ends."""
    grey = drawn(trip=TRIP)
    row = grey[MIDDLE[1] + 150, 20:60] == INK
    assert row.any()
    assert not row.all()


def test_no_stop_ring_at_centre() -> None:
    """With trip marks the centre gets no stop ring."""
    plain = drawn(trip=None)
    marked = drawn(trip=TRIP)
    x, y = MIDDLE
    assert plain[y, x + 12] == INK
    assert marked[y, x + 12] == PAPER


def test_from_drawn_at_origin() -> None:
    """The From marker is drawn at the trip's origin."""
    canvas = Canvas()
    draw_trip_points(
        canvas,
        base=MapBase(
            centre=CENTRE,
            mpp=MPP,
            route=(),
            trip=TRIP,
            projection=SYDNEY_PROJECTION,
        ),
    )
    grey = canvas.grey()
    x, y = MIDDLE[0] - 200, MIDDLE[1] - 100 + 40
    assert grey[y, x] < 128
    assert grey[y, x + 12] < 128


def test_no_trip_draws_no_from() -> None:
    """Without trip marks, no From is drawn."""
    canvas = Canvas()
    draw_trip_points(
        canvas, base=MapBase(
            centre=CENTRE,
            mpp=MPP,
            route=(),
            projection=SYDNEY_PROJECTION,
        ),
    )
    assert canvas.grey().min() == PAPER


def test_lines_are_legs_then_ends() -> None:
    """The lines are the legs, drawn before names, then the ends."""
    base = MapBase(
        centre=CENTRE,
        mpp=MPP,
        route=(),
        trip=TRIP,
        projection=SYDNEY_PROJECTION,
    )
    whole, split = Canvas(width=MAP_WIDTH), Canvas(width=MAP_WIDTH)
    window = (0, 0)
    draw_trip_lines(whole, base=base, window=window)
    draw_trip_legs(split, base=base, window=window)
    draw_trip_ends(split, base=base, window=window)
    assert (whole.grey() == split.grey()).all()


def test_later_walks_dotted_with_change_square() -> None:
    """A later change draws its own dotted walk and square."""
    grey = drawn(trip=replace(
        TRIP, others=(((400.0, 1200.0), (1200.0, 1200.0)),),
    ))
    row = grey[MIDDLE[1] - 150, MIDDLE[0] + 40:MIDDLE[0] + 90] == INK
    assert row.any()
    assert not row.all()
    x, y = MIDDLE[0] + 100, MIDDLE[1] - 150
    assert (grey[y - 6:y + 7, x - 6:x + 7] == INK).all()


def test_a_leg_line_of_one_point_is_skipped() -> None:
    """A second ride's line with fewer than two points draws nothing."""
    canvas = Canvas(width=MAP_WIDTH)
    draw_trip_legs(
        canvas,
        base=MapBase(
            centre=CENTRE,
            mpp=MPP,
            route=(),
            trip=replace(TRIP, second=(((0.0, 0.0),), ()), walk=None),
            projection=SYDNEY_PROJECTION,
        ),
        window=(0, 0),
    )
    assert np.all(canvas.grey() == PAPER)




def ink_extent(*, values: NDArray[np.uint8]) -> int:
    """Pixels from the first to the last ink in a column."""
    ink = np.flatnonzero(values == INK)
    return int(ink[-1] - ink[0] + 1)


def mark_crop(
    *, lettered: bool, end: bool, mpp: int = MPP,
) -> NDArray[np.uint8]:
    """The 29 by 29 pixels round the From's or the end's mark."""
    far = (STOP[0] + 100.0 * mpp, STOP[1])
    marks = TripMarks(
        origin=STOP, second=(), walk=None, change=None, end=far,
        lettered=lettered,
    )
    plain = replace(base_of(mpp=mpp), trip=marks)
    canvas = Canvas(width=MAP_WIDTH, height=MAP_HEIGHT + HEADER)
    point = far if end else STOP
    x, y = screen_point(
        centre=STOP, mpp=mpp, point=point, projection=SYDNEY_PROJECTION,
    )
    if end:
        draw_trip_ends(canvas, base=plain, window=window_of(base=plain))
    else:
        draw_trip_points(canvas, base=plain)
        y += HEADER
    grey = canvas.grey()
    return grey[round(y) - 14 : round(y) + 15, round(x) - 14 : round(x) + 15]


def test_lettered_marks_read_a_and_b() -> None:
    """A pin with a Destination marks its ends with A and B discs."""
    a = mark_crop(lettered=True, end=False)
    b = mark_crop(lettered=True, end=True)
    assert ink_extent(values=a[:, 14]) in {24, 25}
    assert np.count_nonzero(a[8:21, 8:21] == PAPER) > 0
    assert not np.array_equal(a, b)


def test_no_destination_keeps_the_bullseye() -> None:
    """With no Destination the From is today's bullseye."""
    canvas = Canvas(width=MAP_WIDTH, height=MAP_HEIGHT + HEADER)
    x, y = screen_point(
        centre=STOP, mpp=MPP, point=STOP, projection=SYDNEY_PROJECTION,
    )
    draw_stop(canvas, at=(x, y + HEADER))
    row, col = round(y + HEADER), round(x)
    assert np.array_equal(
        mark_crop(lettered=False, end=False),
        canvas.grey()[row - 14 : row + 15, col - 14 : col + 15],
    )


@pytest.mark.parametrize(
    ('mpp', 'scale'),
    [(3, 1.0), (5, 1.0), (8, 1.0), (14, 1.0), (25, 0.85), (45, 0.75)],
)
def test_marks_shrink_at_the_two_widest_steps(
    mpp: int, scale: float,
) -> None:
    """Marks are full size to 14 m/px, 85% at 25 and 75% at 45."""
    crop = mark_crop(lettered=True, end=True, mpp=mpp)
    assert abs(ink_extent(values=crop[:, 14]) - 24 * scale) <= 1


@pytest.mark.parametrize(
    ('mpp', 'scale'),
    [(3, 1.0), (5, 1.0), (8, 1.0), (14, 1.0), (25, 0.85), (45, 0.75)],
)
def test_mark_scale_at_every_zoom(mpp: int, scale: float) -> None:
    """Marks are full size to 14 m/px, 0.85 at 25 and 0.75 at 45."""
    assert mark_scale(mpp=mpp) == scale
