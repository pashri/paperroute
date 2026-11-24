"""Facts about the drawn route lines and stop rings (no golden images)."""

import math
import re
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.map_style import DARK
from paperroute_transit.core.geo import Point, make_shape
from paperroute_transit.core.map_line import MapLine
from paperroute_transit.core.map_view import MAP_WIDTH, screen_point
from paperroute_transit.core.tiles import TILE, TileVariant, to_pixels
from paperroute_transit.render.map_base import draw_map_base
from paperroute_transit.render.map_lines import line_parts
from tests.render.map_base.helpers import (
    CENTRE,
    LABEL_SHIFT,
    MPP,
    base,
    build,
    centre_row,
)
from tests.render.map_base.helpers import pattern as pattern_of
from tests.render.map_base.helpers import stop_tile, street

FULL: Final[tuple[float, float]] = (0.0, 100_000.0)


def straight(
    *, solid: tuple[float, float] | None, shape_id: str = 'east',
) -> MapLine:
    """An east-west line 100 km long through CENTRE, solid over a span."""
    shape = make_shape(
        shape_id=shape_id,
        points=((CENTRE[0] - 50_000.0, CENTRE[1]),
                (CENTRE[0] + 50_000.0, CENTRE[1])),
    )
    return MapLine(shape_id=shape_id, solid=solid, shape=shape)


def drawn(
    *, tmp_path: Path, mpp: int, lines: tuple[MapLine, ...] = (),
    stops: tuple[Point, ...] = (), dashed_stops: tuple[Point, ...] = (),
    tiles: dict[tuple[int, int], NDArray[np.uint8]] | None = None,
) -> NDArray[np.uint8]:
    """The grey base map of those lines and rings."""
    with closing(build(out=tmp_path / 'tiles.db', tiles=tiles)) as repo:
        return draw_map_base(
            base=replace(
                base(mpp=mpp), lines=lines, stops=stops,
                dashed_stops=dashed_stops,
            ),
            tiles=repo,
            variant=TileVariant.GREY,
        )


def pixel(*, point: Point, mpp: int) -> tuple[int, int]:
    """A point's map pixel, column and row (`screen_point`, floored)."""
    x, y = screen_point(
        centre=CENTRE, mpp=mpp, point=point, projection=SYDNEY_PROJECTION,
    )
    return math.floor(x), math.floor(y)


def ink_run(*, values: NDArray[np.uint8], at: int) -> int:
    """The length of the ink run through index `at`; 0 off ink."""
    if values[at] != INK:
        return 0
    low = high = at
    while low > 0 and values[low - 1] == INK:
        low -= 1
    while high < len(values) - 1 and values[high + 1] == INK:
        high += 1
    return high - low + 1


def ink_extent(*, values: NDArray[np.uint8], at: int) -> int:
    """Rows from the first to the last ink within 15 of `at`."""
    ink = np.flatnonzero(values[at - 15 : at + 16] == INK)
    return int(ink[-1] - ink[0] + 1)


@pytest.mark.parametrize(
    ('mpp', 'width'),
    [(3, 13), (5, 13), (8, 10), (14, 7), (25, 4), (45, 3)],
)
def test_solid_line_is_t3_wide(tmp_path: Path, mpp: int, width: int) -> None:
    """A solid line is T3's width in ink."""
    grey = drawn(tmp_path=tmp_path, mpp=mpp, lines=(straight(solid=FULL),))
    assert ink_run(values=grey[:, 120], at=centre_row(mpp=mpp)) == width


def test_solid_line_is_cased_in_paper(tmp_path: Path) -> None:
    """Over a dark tile, 2 px of paper edge each side of the ink."""
    dark = {stop_tile(): np.full((TILE, TILE), DARK, dtype=np.uint8)}
    grey = drawn(
        tmp_path=tmp_path, mpp=MPP, lines=(straight(solid=FULL),),
        tiles=dark,
    )
    column, row = grey[:, MAP_WIDTH // 2 + 24], centre_row()
    top = row - int(np.argmin(column[row::-1] == INK)) + 1
    assert list(column[top - 2 : top]) == [PAPER, PAPER]
    assert column[top - 3] == DARK


@pytest.mark.parametrize(
    ('mpp', 'widths', 'dash'),
    [(3, {6, 7}, 14), (8, {5}, 14), (14, {3, 4}, 14), (25, {2}, 10),
     (45, {1, 2}, 10)],
)
def test_dashes_are_half_width(
    *,
    tmp_path: Path, mpp: int, widths: set[int], dash: int,
) -> None:
    """Dashes are half the solid width, at L6's dash length."""
    grey = drawn(tmp_path=tmp_path, mpp=mpp, lines=(straight(solid=None),))
    row = centre_row(mpp=mpp)
    runs = re.findall(r'#+|\.+', pattern_of(values=grey[row, 20:240]))
    assert {len(run) for run in runs[1:-1] if run[0] == '#'} == {dash}
    start = len(runs[0]) + (len(runs[1]) if runs[0][0] == '#' else 0)
    first_dash = 20 + start + 1
    assert ink_run(values=grey[:, first_dash], at=row) in widths


def test_line_parts_cut_at_the_span() -> None:
    """Before the span and after it are dashed; the span is solid."""
    line = straight(solid=(40_000.0, 60_000.0))
    solid, dashed = line_parts(base=replace(base(mpp=45), lines=(line,)))
    assert len(solid) == 1 and len(dashed) == 2
    x0, x1 = solid[0][0][0], solid[0][-1][0]
    assert (x0, x1) == pytest.approx(
        (pixel(point=(CENTRE[0] - 10_000.0, CENTRE[1]), mpp=45)[0],
         pixel(point=(CENTRE[0] + 10_000.0, CENTRE[1]), mpp=45)[0]),
        abs=1,
    )


def test_ride_is_solid_and_the_rest_dashed(tmp_path: Path) -> None:
    """West of the From the row has gaps; east of it, none."""
    line = straight(solid=(50_000.0, 100_000.0))
    grey = drawn(tmp_path=tmp_path, mpp=MPP, lines=(line,))
    row = centre_row()
    assert np.count_nonzero(grey[row, 20:240] != INK) > 0
    assert np.all(grey[row, 280:500] == INK)


def test_dashed_lines_on_one_road_read_as_one(tmp_path: Path) -> None:
    """Two dashed lines on one road draw the same pixels as one."""
    one = straight(solid=None)
    two = straight(solid=None, shape_id='other')
    assert np.array_equal(
        drawn(tmp_path=tmp_path, mpp=8, lines=(one,)),
        drawn(tmp_path=tmp_path, mpp=8, lines=(two, one)),
    )


def test_solid_wins_over_dashed(tmp_path: Path) -> None:
    """A dashed line drawn after a solid one on its road leaves no gap."""
    solid = straight(solid=FULL)
    dashed = straight(solid=None, shape_id='other')
    grey = drawn(tmp_path=tmp_path, mpp=8, lines=(solid, dashed))
    row = centre_row()
    assert np.all(grey[row, 20:240] == INK)
    assert np.all(grey[row, 280:500] == INK)


def beside(
    *, shape_id: str, start: float = 0.0, rise: float = 0.0,
) -> MapLine:
    """A dashed east-west line `rise` metres north of CENTRE's row."""
    shape = make_shape(
        shape_id=shape_id,
        points=((CENTRE[0] - 50_000.0 + start, CENTRE[1] + rise),
                (CENTRE[0] + 50_000.0, CENTRE[1] + rise)),
    )
    return MapLine(shape_id=shape_id, solid=None, shape=shape)


def test_dash_phase_ignores_where_a_shared_road_starts(
    tmp_path: Path,
) -> None:
    """A dashed line starting 7 px along a road adds no second dash set."""
    one = beside(shape_id='one')
    later = beside(shape_id='later', start=56.0)
    assert np.array_equal(
        drawn(tmp_path=tmp_path, mpp=8, lines=(one,)),
        drawn(tmp_path=tmp_path, mpp=8, lines=(one, later)),
    )


def test_solid_hides_a_dashed_line_beside_it(tmp_path: Path) -> None:
    """A dashed line 7 px from a solid one leaves the solid's pixels."""
    solid = straight(solid=FULL)
    dashed = beside(shape_id='dashed', rise=56.0)
    assert np.array_equal(
        drawn(tmp_path=tmp_path, mpp=8, lines=(solid,)),
        drawn(tmp_path=tmp_path, mpp=8, lines=(solid, dashed)),
    )


@pytest.mark.parametrize(
    ('mpp', 'solid', 'radius'),
    [(3, True, 10), (8, True, 8.5), (45, True, 5),
     (3, False, 6.75), (8, False, 6), (45, False, 4.25)],
)
def test_rings_reach_3_5_px_past_the_stroke(
    *,
    tmp_path: Path, mpp: int, solid: bool, radius: float,
) -> None:
    """A ring's radius is half its stroke plus 3.5, with a paper centre."""
    stop = (CENTRE[0] + 40.0 * mpp, CENTRE[1] + 40.0 * mpp)
    grey = drawn(
        tmp_path=tmp_path, mpp=mpp,
        stops=(stop,) if solid else (),
        dashed_stops=() if solid else (stop,),
    )
    x, y = pixel(point=stop, mpp=mpp)
    assert abs(ink_extent(values=grey[:, x], at=y) - 2 * radius) <= 1
    assert grey[y, x] == PAPER


def test_names_over_a_dashed_line_are_redrawn(tmp_path: Path) -> None:
    """A street under a dashed line keeps its name on top."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    with closing(build(out=tmp_path / 'named.db', labels=[label])) as repo:
        named = draw_map_base(
            base=replace(base(), lines=(straight(solid=None),)),
            tiles=repo,
            variant=TileVariant.GREY,
        )
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + LABEL_SHIFT)
    assert np.count_nonzero(
        named[row - 3 : row + 4, x - 30 : x + 31] == DARK,
    ) > 0
