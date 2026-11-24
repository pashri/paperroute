"""Tests for where a strip ring and spur sit."""

import math

import pytest

from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.strip_detours import DetourKind, DetourPart
from paperroute_transit.core.strip_model import (
    INDENT,
    PANE_TOP,
    PANE_TRACK_X,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    StripRow,
)
from paperroute_transit.core.strip_rings import (
    NARROW,
    WIDE,
    ring_of,
    ring_point,
    ring_radius,
    ring_stop,
    spur_point,
)


def row(*, y: float, part: DetourPart | None = None) -> StripRow:
    """
    Make a strip row.

    Parameters
    ----------
    y : float
        Row centre.
    part : DetourPart | None
        Its place on a detour.

    Returns
    -------
    StripRow
        The row.
    """
    return StripRow(
        name='Stop',
        depth=0,
        kind=RowKind.MAIN,
        y=y,
        parent_y=y - ROW_H,
        parent_depth=0,
        tag='',
        time='',
        ride=False,
        cursor=False,
        bold=False,
        station=f's{y}',
        detour=part,
    )


def loop(*, stops: int, top: float = STRIP_TOP + ROW_H * 1.5) -> list[StripRow]:
    """
    Make a stand, a loop's rows last call first, and the stand back.

    Parameters
    ----------
    stops : int
        Stops on the ring.
    top : float
        The leaving stand's y.

    Returns
    -------
    list[StripRow]
        The rows.
    """
    inside = [
        row(
            y=top + ROW_H * (1 + place),
            part=DetourPart(
                kind=DetourKind.LOOP,
                group=0,
                slot=stops - 1 - place,
                size=stops,
            ),
        )
        for place in range(stops)
    ]
    return [row(y=top), *inside, row(y=top + ROW_H * (stops + 1))]


def spur(*, levels: int) -> list[StripRow]:
    """
    Make a junction and a spur's levels.

    Parameters
    ----------
    levels : int
        Levels past the junction.

    Returns
    -------
    list[StripRow]
        The rows.
    """
    return [
        row(
            y=STRIP_TOP + ROW_H * (1 + level),
            part=DetourPart(
                kind=DetourKind.SPUR, group=0, slot=level, size=levels,
            ),
        )
        for level in range(levels + 1)
    ]


@pytest.mark.parametrize(('stops', 'radius'), [(1, 4), (4, 58), (6, 94)])
def test_ring_radius_matches_the_mock_up(stops: int, radius: float) -> None:
    """Grow 18 px a stop from 4 px."""
    assert ring_radius(stops=stops) == radius


@pytest.mark.parametrize(('stops', 'width'), [(2, 44), (3, 70), (4, 70)])
def test_ring_of_caps_width_at_seventy_px_up_to_four_stops(
    stops: int,
    width: float,
) -> None:
    """Keep small circles and cap the rest at 70 px wide."""
    ring = ring_of(rows=loop(stops=stops), group=0, track_x=TRACK_X)
    assert ring is not None
    assert (ring.left + ring.right, ring.r) == (width, ring_radius(stops=stops))
    assert ring.right == min(ring.r, WIDE)


def test_ring_of_is_fifty_px_wide_from_five_stops() -> None:
    """Draw five or more stops as a 50 px ellipse."""
    ring = ring_of(rows=loop(stops=5), group=0, track_x=TRACK_X)
    assert ring is not None
    assert (ring.left, ring.right, ring.r) == (NARROW, NARROW, 76)


def test_ring_of_meets_the_trunk_at_two_hundred_degrees() -> None:
    """Put the centre so the trunk meets the left side at 200 degrees."""
    ring = ring_of(rows=loop(stops=3), group=0, track_x=TRACK_X)
    assert ring is not None
    assert ring.at(angle=200)[0] == pytest.approx(TRACK_X)


def test_ring_stop_sits_on_the_right_side() -> None:
    """Put a stop where its row meets the right side."""
    rows = loop(stops=3)
    ring = ring_of(rows=rows, group=0, track_x=TRACK_X)
    assert ring is not None
    assert ring_stop(ring=ring, row=rows[2]) == (ring.cx + ring.right, rows[2].y)


def test_ring_point_heads_up_and_right_at_hyde_park() -> None:
    """Point up and slightly right just below the middle of the 441 ring."""
    rows = loop(stops=5)
    ring = ring_of(rows=rows, group=0, track_x=TRACK_X)
    assert ring is not None
    hyde = rows[4]
    angle = math.degrees(math.asin((hyde.y - ring.cy) / ring.r))
    point, bearing = ring_point(ring=ring, share=(160 - angle) / 320)
    assert point == pytest.approx(ring_stop(ring=ring, row=hyde))
    assert 0 < bearing < 45


def test_ring_point_runs_from_leaving_to_back() -> None:
    """Start at the lower left and end at the upper left."""
    ring = ring_of(rows=loop(stops=3), group=0, track_x=TRACK_X)
    assert ring is not None
    assert ring_point(ring=ring, share=0)[0] == pytest.approx(ring.at(angle=160))
    assert ring_point(ring=ring, share=1)[0] == pytest.approx(ring.at(angle=200))


@pytest.mark.parametrize('top', [STRIP_TOP - ROW_H, STRIP_TOP + ROW_H * 9])
def test_ring_rows_draw_as_rows_when_cut_off(top: float) -> None:
    """Give no ring when any part of it is off the strip."""
    assert ring_of(rows=loop(stops=4, top=top), group=0, track_x=TRACK_X) is None


def test_ring_of_needs_every_row() -> None:
    """Give no ring when a ring row is missing, as under a fold."""
    rows = loop(stops=4)
    del rows[2]
    assert ring_of(rows=rows, group=0, track_x=TRACK_X) is None


def test_ring_of_needs_the_pane_to_hold_it() -> None:
    """Give a pane ring only when it and its stands fit in the pane."""
    rows = loop(stops=6, top=PANE_TOP + ROW_H * 1.5)
    assert ring_of(rows=rows, group=0, track_x=PANE_TRACK_X) is not None
    rows = loop(stops=6, top=PANE_TOP + ROW_H * 2.5)
    assert ring_of(rows=rows, group=0, track_x=PANE_TRACK_X) is None
    rows = loop(stops=3, top=PANE_TOP - ROW_H / 2)
    assert ring_of(rows=rows, group=0, track_x=PANE_TRACK_X) is None
    assert ring_of(rows=rows, group=0, track_x=TRACK_X) is not None


def test_ring_of_ignores_other_groups() -> None:
    """Give no ring for a group with no rows."""
    assert ring_of(rows=loop(stops=3), group=1, track_x=TRACK_X) is None


def test_spur_point_heads_out_down_the_branch() -> None:
    """Start at the junction and head down to the far end."""
    rows = spur(levels=2)
    start, bearing = spur_point(
        rows=rows, group=0, share=0, back=False, track_x=TRACK_X,
    )
    end, _ = spur_point(
        rows=rows, group=0, share=1, back=False, track_x=TRACK_X,
    )
    assert start == (TRACK_X, rows[0].y)
    assert end == (TRACK_X + INDENT, rows[-1].y)
    assert 90 < bearing < 180


def test_spur_point_heads_back_up_the_branch() -> None:
    """Start at the far end and head up to the junction."""
    rows = spur(levels=2)
    middle, bearing = spur_point(
        rows=rows, group=0, share=0.25, back=True, track_x=TRACK_X,
    )
    assert middle == pytest.approx((TRACK_X + INDENT, rows[1].y + ROW_H / 2))
    assert bearing == 0


def test_spur_point_falls_back_to_the_track_without_rows() -> None:
    """Sit on the track heading down when the spur has no rows."""
    assert spur_point(
        rows=[], group=0, share=0.5, back=False, track_x=TRACK_X,
    ) == ((TRACK_X, 0.0), 180.0)


def test_ring_of_needs_both_stands() -> None:
    """Give no ring when a stand row is missing."""
    assert ring_of(rows=loop(stops=3)[1:], group=0, track_x=TRACK_X) is None
