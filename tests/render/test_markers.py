"""Facts about drawn vehicle markers (no golden images)."""

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest

from paperroute.core.map_view import MAP_HEIGHT, MAP_WIDTH, Marker
from paperroute.core.tiles import CITY, ZOOMS
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
    BADGE_FACE,
    OFFSETS,
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
MIDDLE: Point = (MAP_WIDTH / 2, MAP_HEIGHT / 2)


class CountingCanvas(Canvas):
    """A canvas that records its batched dots calls and its lines."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[int, float, int]] = []
        self.lines: list[tuple[tuple[Point, ...], float, int]] = []

    def dots(
        self,
        *,
        points: Sequence[Point],
        diameter: float,
        grey: int,
    ) -> None:
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

    def line(
        self,
        *,
        points: Sequence[Point],
        width: float,
        grey: int,
        dash: tuple[float, float] | None = None,
    ) -> None:
        """
        Record the line, then draw.

        Parameters
        ----------
        points : Sequence[Point]
            Vertices.
        width : float
            Stroke width.
        grey : int
            Stroke level.
        dash : tuple[float, float] | None
            Dash and gap lengths; None strokes solid.
        """
        self.lines.append((tuple(points), width, grey))
        super().line(points=points, width=width, grey=grey, dash=dash)


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


def drawn(
    *,
    markers: list[Marker],
    mpp: int = BADGE_ZOOM,
    origin: Point = (0.0, 0.0),
    ground: int = PAPER,
) -> np.ndarray:
    """
    Draw markers over a plain ground.

    Parameters
    ----------
    markers : list[Marker]
        Markers.
    mpp : int
        Metres per pixel.
    origin : Point
        The map's top left on the canvas.
    ground : int
        Grey filled before drawing.

    Returns
    -------
    np.ndarray
        Grey pixels.
    """
    canvas = Canvas()
    canvas.rect(box=(0, 0, WIDTH, HEIGHT), grey=ground)
    draw_markers(
        canvas,
        markers=markers,
        mpp=mpp,
        origin=origin,
        stop=MIDDLE,
    )
    return canvas.grey()


def first_column(*, edge: float) -> int:
    """
    Find the first pixel column an aliased fill from an edge covers.

    Parameters
    ----------
    edge : float
        Left edge in pixels.

    Returns
    -------
    int
        Column whose centre is at or right of the edge.
    """
    return math.ceil(edge - 0.5)


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
        stop=MIDDLE,
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


@pytest.mark.parametrize(
    'mpp',
    [zoom for zoom in ZOOMS if zoom != CITY],
)
def test_stale_vehicle_has_age_tab(mpp: int) -> None:
    """A stale badge hangs an inverted tab of the right size."""
    grey = drawn(markers=[marker(live=False, age=240.0)], mpp=mpp)
    half_width = text_width(text='4m', face=AGE_FACE) / 2 + 3
    tab_edge = first_column(edge=100 - half_width)
    assert int(grey[115, tab_edge]) == INK
    assert int(grey[115, tab_edge - 2]) == PAPER
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
        (120, '2m'),
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


def test_pinned_badge_is_placed_first() -> None:
    """A pinned badge keeps its spot even when listed after another."""
    other = replace(marker(), route='T1', pinned=False)
    grey = drawn(markers=[other, marker()])
    edge = first_column(edge=100 - badge_half(route='606')[0])
    assert int(grey[100, edge + 1]) == INK


def test_pinned_badge_is_drawn_last() -> None:
    """Badges stuck on a pinned badge's spot are drawn under it."""
    pinned = marker(live=False)
    others = [replace(marker(), pinned=False)] * 12
    placed = place(markers=[pinned, *others], reserved=[])
    assert sum(item.centre == SPOT for item in placed) > 1
    grey = drawn(markers=[*others, pinned])
    left = first_column(edge=100 - badge_half(route='606')[0])
    assert int(grey[93, left + 3]) == PAPER


@pytest.mark.parametrize(('gap', 'slides'), [(23.5, True), (24.5, False)])
def test_badges_keep_12_px_apart_sideways(gap: float, slides: bool) -> None:
    """Side by side, badges need a 24 px gap: 12 px grown on each."""
    width = 2 * badge_half(route='606')[0]
    beside = marker(at=(100.0 + width + gap, 100.0))
    placed = place(markers=[marker(), beside], reserved=[])
    assert (placed[1].centre != (beside.x, beside.y)) is slides


@pytest.mark.parametrize(('gap', 'slides'), [(7.5, True), (8.5, False)])
def test_badges_keep_4_px_apart_vertically(gap: float, slides: bool) -> None:
    """Stacked, badges need an 8 px gap: 4 px grown on each."""
    below = marker(at=(100.0, 120.0 + gap))
    placed = place(markers=[marker(), below], reserved=[])
    assert (placed[1].centre != (below.x, below.y)) is slides


def test_origin_moves_badges_and_leaders() -> None:
    """Badges and leaders are drawn offset by the map's origin."""
    markers = [marker(), replace(marker(), route='T1')]
    grey = drawn(markers=markers, origin=(30.0, 50.0))
    edge = first_column(edge=130 - badge_half(route='606')[0]) + 1
    assert int(grey[150, edge]) == INK
    assert int(grey[100, edge - 30]) == PAPER
    assert int(grey[136, 130]) == INK
    assert int(grey[86, 100]) == PAPER


def test_stuck_badge_stays_put_without_claiming_its_box() -> None:
    """A badge with no free spot stays put and blocks nothing later."""
    points = [(100.0 + dx, 100.0 + dy) for dx, dy in OFFSETS]
    reserved = [(x, y, x, y) for x, y in points]
    below = marker(at=(100.0, 114.0))
    placed = place(markers=[marker(), below], reserved=reserved)
    assert placed[0].centre == SPOT
    assert placed[1].centre == (100.0, 114.0)


@pytest.mark.parametrize(
    ('walls', 'centre'),
    [((100.0,), (140.0, 100.0)), ((100.0, 140.0), (60.0, 100.0))],
)
def test_badges_slide_40_px_sideways(
    walls: tuple[float, ...],
    centre: Point,
) -> None:
    """Blocked above and below, a badge slides 40 px right, then left."""
    reserved = [(x - 1, 0.0, x + 1, 300.0) for x in walls]
    placed = place(markers=[marker()], reserved=reserved)
    assert placed[0].centre == centre


@pytest.mark.parametrize('live', [True, False])
def test_badges_have_a_paper_outline(live: bool) -> None:
    """Each badge has a 1 px paper outline; hollow ones a 2 px border."""
    grey = drawn(markers=[marker(live=live)], ground=INK)
    left = 100 - badge_half(route='606')[0]
    outline = first_column(edge=left - 1)
    assert outline < first_column(edge=left)
    assert int(grey[100, outline]) == PAPER
    assert int(grey[100, outline - 1]) == INK
    inner = first_column(edge=left + 2)
    expected = (INK, INK) if live else (INK, PAPER)
    assert (int(grey[100, inner - 1]), int(grey[100, inner])) == expected


def test_live_marker_age_takes_no_tab_space() -> None:
    """A live marker never draws a tab, so its age adds no room."""
    live = marker(age=240.0)
    assert marker_box(marker=live, centre=SPOT) == marker_box(
        marker=marker(), centre=SPOT)


def test_leaders_are_two_strokes_and_one_dot_batch() -> None:
    """Each leader is a 3 px paper line under 1.5 px ink; one dot call."""
    canvas = CountingCanvas()
    markers = [marker(), replace(marker(), route='T1')] * 2
    draw_markers(
        canvas,
        markers=markers,
        mpp=BADGE_ZOOM,
        origin=(0, 0),
        stop=MIDDLE,
    )
    moved = sum(
        item.centre != SPOT for item in place(markers=markers, reserved=[])
    )
    assert moved > 1
    strokes = [(width, grey) for _, width, grey in canvas.lines]
    assert strokes == [(3, PAPER), (1.5, INK)] * moved
    assert canvas.calls == [(moved, 3.5, INK)]


@pytest.mark.parametrize(('y', 'slides'), [(247.0, True), (248.0, False)])
def test_stop_box_is_14_px_each_side(y: float, slides: bool) -> None:
    """A badge must clear the stop's 28 px box by its 4 px growth."""
    canvas = CountingCanvas()
    below = marker(at=(MAP_WIDTH / 2, y))
    draw_markers(
        canvas,
        markers=[below],
        mpp=BADGE_ZOOM,
        origin=(0, 0),
        stop=MIDDLE,
    )
    assert bool(canvas.lines) is slides


@pytest.mark.parametrize(
    ('stop', 'slides'),
    [((MAP_WIDTH / 2, MAP_HEIGHT / 2 + 1), True), (None, False)],
)
def test_stop_box_sits_where_the_stop_is_drawn(
    stop: Point | None,
    slides: bool,
) -> None:
    """The reserved box follows the given stop point, or is absent."""
    canvas = CountingCanvas()
    below = marker(at=(MAP_WIDTH / 2, 248.5))
    draw_markers(
        canvas,
        markers=[below],
        mpp=BADGE_ZOOM,
        origin=(0, 0),
        stop=stop,
    )
    assert bool(canvas.lines) is slides


def test_badge_half_pads_the_route() -> None:
    """A badge is the route's width plus 5 px a side, and 20 px tall."""
    width = text_width(text='606', face=BADGE_FACE)
    assert badge_half(route='606') == (width / 2 + 5, 10)
