"""Facts about a connection's marks on the map base (no golden images)."""

from typing import Final

import numpy as np

from paperroute.adapters.tiles_db import NullTiles
from paperroute.core.geo import Point
from paperroute.core.map_view import MAP_HEIGHT, MAP_WIDTH, MapBase, TripMarks
from paperroute.core.tiles import TileVariant
from paperroute.render.canvas import INK, PAPER, Canvas
from paperroute.render.map_base import draw_map_base
from paperroute.render.trip_marks import (
    draw_trip_ends,
    draw_trip_legs,
    draw_trip_lines,
    draw_trip_points,
)

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
        base=MapBase(centre=CENTRE, mpp=MPP, route=(), trip=trip),
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
    assert grey[y - 7, x - 7] != INK


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
    assert plain[y, x + 9] == INK
    assert marked[y, x + 9] == PAPER


def test_from_drawn_at_origin() -> None:
    """The From marker is drawn at the trip's origin."""
    canvas = Canvas()
    draw_trip_points(
        canvas,
        base=MapBase(centre=CENTRE, mpp=MPP, route=(), trip=TRIP),
    )
    grey = canvas.grey()
    x, y = MIDDLE[0] - 200, MIDDLE[1] - 100 + 40
    assert grey[y, x] < 128
    assert grey[y, x + 9] < 128


def test_no_trip_draws_no_from() -> None:
    """Without trip marks, no From is drawn."""
    canvas = Canvas()
    draw_trip_points(
        canvas, base=MapBase(centre=CENTRE, mpp=MPP, route=()),
    )
    assert canvas.grey().min() == PAPER


def test_lines_are_legs_then_ends() -> None:
    """The lines are the legs, drawn before names, then the ends."""
    base = MapBase(centre=CENTRE, mpp=MPP, route=(), trip=TRIP)
    whole, split = Canvas(width=MAP_WIDTH), Canvas(width=MAP_WIDTH)
    window = (0, 0)
    draw_trip_lines(whole, base=base, window=window)
    draw_trip_legs(split, base=base, window=window)
    draw_trip_ends(split, base=base, window=window)
    assert (whole.grey() == split.grey()).all()
