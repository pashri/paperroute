"""Facts about drawn vehicle markers (no golden images)."""

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest

from paperroute.core.map_view import MAP_HEIGHT, MAP_WIDTH, Marker
from paperroute.core.tiles import CITY
from paperroute.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Point,
    text_width,
)
from paperroute.render.markers import (
    AGE_FACE,
    TAB_ZOOMS,
    age_text,
    badge_half,
    draw_markers,
    draw_tab,
    marker_box,
    place,
    tab_box,
    wedge_points,
)

BADGE_ZOOM = 8
SPOT: Point = (100.0, 100.0)


class CountingCanvas(Canvas):
    """A canvas that records each batched dots call."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[int, float, int]] = []

    def dots(self, *, points: Sequence[Point], diameter: float,
             grey: int) -> None:
        """
        Record the call, then draw.

        Parameters
        ----------
        points : Sequence[Point]
            Dot centres.
        diameter : float
            Dot width in pixels.
        grey : int
            Level.
        """
        self.calls.append((len(points), diameter, grey))
        super().dots(points=points, diameter=diameter, grey=grey)


def marker(
    *,
    at: Point = SPOT,
    bearing: float | None = None,
    live: bool = True,
    age: float | None = None,
) -> Marker:
    """
    A 606 marker.

    Parameters
    ----------
    at : Point
        Map pixel position.
    bearing : float | None
        Heading.
    live : bool
        Solid or hollow.
    age : float | None
        Seconds since last heard.

    Returns
    -------
    Marker
        The marker.
    """
    return Marker(
        route='606',
        x=at[0],
        y=at[1],
        bearing=bearing,
        live=live,
        pinned=True,
        age=age,
    )


def drawn(*, markers: list[Marker], mpp: int = BADGE_ZOOM) -> np.ndarray:
    """
    Draw markers with the map at the canvas's top left.

    Parameters
    ----------
    markers : list[Marker]
        Markers.
    mpp : int
        Metres per pixel.

    Returns
    -------
    np.ndarray
        Grey pixels.
    """
    canvas = Canvas()
    draw_markers(canvas, markers=markers, mpp=mpp, origin=(0.0, 0.0))
    return canvas.grey()


def test_wedge_snaps_to_eight_directions() -> None:
    """A bearing just short of north points straight up from the top."""
    apex, left, right = wedge_points(
        centre=SPOT, half=(15.0, 10.0), bearing=359.9)
    assert apex == pytest.approx((100.0, 80.0))
    assert left[1] == pytest.approx(93.0)
    assert right[1] == pytest.approx(93.0)
    assert sorted((left[0], right[0])) == pytest.approx([93.0, 107.0])


def test_diagonal_wedge_sits_on_the_corner() -> None:
    """44 degrees rounds to 45, so the wedge leaves the top-right corner."""
    apex, left, right = wedge_points(
        centre=SPOT, half=(15.0, 10.0), bearing=44.0)
    step = math.sqrt(0.5)
    corner = (115.0, 90.0)
    assert apex == pytest.approx(
        (corner[0] + 10 * step, corner[1] - 10 * step))
    middle = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
    assert middle == pytest.approx(
        (corner[0] - 3 * step, corner[1] + 3 * step))


def test_badge_without_bearing_has_no_wedge() -> None:
    """Above a badge with no bearing stays paper; north puts ink there."""
    plain = drawn(markers=[marker(bearing=None)])
    north = drawn(markers=[marker(bearing=0.0)])
    assert int(plain[84, 100]) == PAPER
    assert int(north[84, 100]) == INK
    edge = math.ceil(100 - badge_half(route='606')[0])
    assert int(plain[100, edge]) == INK


def test_hollow_badge_is_paper_inside_with_no_wedge() -> None:
    """A timetable badge has an ink border, paper inside and no wedge."""
    grey = drawn(markers=[marker(bearing=0.0, live=False)])
    half_width = badge_half(route='606')[0]
    left = math.ceil(100 - half_width)
    assert int(grey[90, 100]) == INK
    assert int(grey[100, left]) == INK
    assert int(grey[93, left + 3]) == PAPER
    assert int(grey[84, 100]) == PAPER


def test_overlapping_badges_slide_with_a_leader() -> None:
    """The second badge on a spot slides up, joined by a leader line."""
    markers = [marker(), replace(marker(), route='T1')]
    placed = place(markers=markers, reserved=[])
    assert placed[0].centre == SPOT
    assert placed[1].centre == (100.0, 72.0)
    grey = drawn(markers=markers)
    assert int(grey[86, 100]) == INK
    assert int(grey[86, 104]) == PAPER


def test_crowded_badges_overlap_rather_than_vanish() -> None:
    """Forty badges on one spot are all placed."""
    placed = place(markers=[marker()] * 40, reserved=[])
    assert len(placed) == 40
    assert placed[-1].centre == SPOT


def test_city_zoom_draws_dots() -> None:
    """Live dots are ink; hollow dots are ringed with a paper centre."""
    canvas = CountingCanvas()
    draw_markers(
        canvas,
        markers=[marker(), marker(at=(200.0, 100.0), live=False)],
        mpp=CITY,
        origin=(0.0, 0.0),
    )
    grey = canvas.grey()
    assert int(grey[100, 100]) == INK
    assert int(grey[100, 200]) == PAPER
    assert int(grey[100, 202]) == INK
    assert [(count, diameter) for count, diameter, _ in canvas.calls] == [
        (1, 9),
        (1, 7),
        (1, 9),
        (1, 7),
        (1, 4),
    ]


def test_marker_on_the_stop_slides_away() -> None:
    """A badge on the stop slides off it."""
    stop = (MAP_WIDTH / 2, MAP_HEIGHT / 2)
    reserved = [(stop[0] - 14, stop[1] - 14, stop[0] + 14, stop[1] + 14)]
    placed = place(markers=[marker(at=stop)], reserved=reserved)
    assert placed[0].centre == (stop[0], stop[1] - 28)
    grey = drawn(markers=[marker(at=stop)])
    edge = int(stop[0] - badge_half(route='606')[0]) + 2
    assert int(grey[int(stop[1]) - 28, edge]) == INK
    assert int(grey[int(stop[1]), edge]) == PAPER


@pytest.mark.parametrize('mpp', TAB_ZOOMS)
def test_stale_vehicle_has_age_tab(mpp: int) -> None:
    """A stale badge hangs an inverted tab of the right size."""
    grey = drawn(markers=[marker(live=False, age=240.0)], mpp=mpp)
    half_width = text_width(text='4m', face=AGE_FACE) / 2 + 3
    assert int(grey[110, 100]) == INK
    inside = grey[111:120, math.ceil(100 - half_width) + 1:100]
    assert (inside == PAPER).any()
    left, top, right, bottom = tab_box(centre=SPOT, age=240.0)
    assert right - left == pytest.approx(2 * half_width + 2)
    assert bottom - top == 13
    canvas = Canvas()
    canvas.rect(box=(0, 0, WIDTH, HEIGHT), grey=INK)
    draw_tab(canvas, centre=SPOT, age=240.0)
    rows, columns = np.nonzero(canvas.grey() == PAPER)
    assert int(rows.max() - rows.min() + 1) == 13
    width = int(columns.max() - columns.min() + 1)
    assert abs(width - (2 * half_width + 2)) <= 1


def test_never_heard_trip_has_no_tab() -> None:
    """A hollow badge with no age has nothing below it."""
    grey = drawn(markers=[marker(live=False, age=None)])
    assert (grey[110:140, 70:130] == PAPER).all()


def test_live_vehicle_has_no_tab() -> None:
    """A live badge has nothing below it."""
    grey = drawn(markers=[marker()])
    assert (grey[110:140, 70:130] == PAPER).all()


@pytest.mark.parametrize(
    ('seconds', 'text'),
    [
        (121, '2m'),
        (240, '4m'),
        (3_599, '59m'),
        (3_600, '1h'),
        (7_199, '1h'),
        (7_200, '2h'),
    ],
)
def test_age_text_minutes_and_hours(seconds: float, text: str) -> None:
    """Ages floor to whole minutes, then whole hours."""
    assert age_text(seconds=seconds) == text


def test_city_zoom_has_no_tab() -> None:
    """At city zoom a stale vehicle is only its hollow dot."""
    grey = drawn(markers=[marker(live=False, age=240.0)], mpp=CITY)
    assert int(grey[100, 100]) == PAPER
    assert (grey[106:130, 80:120] == PAPER).all()


def test_tab_counts_as_part_of_the_badge_for_placement() -> None:
    """The tab adds 12 px below the badge and moves it off a reserved box."""
    stale = marker(live=False, age=240.0)
    plain = marker(live=False)
    stale_box = marker_box(marker=stale, centre=SPOT)
    plain_box = marker_box(marker=plain, centre=SPOT)
    assert stale_box[3] - plain_box[3] == 12
    assert tab_box(centre=SPOT, age=240.0)[1] == plain_box[3] - 1
    reserved = [(90.0, 118.0, 110.0, 120.0)]
    assert place(markers=[plain], reserved=reserved)[0].centre == SPOT
    assert place(markers=[stale], reserved=reserved)[0].centre == (
        100.0,
        72.0,
    )
