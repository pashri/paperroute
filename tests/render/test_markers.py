"""Facts about drawn vehicle markers (no golden images)."""

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest

from paperroute.render.keep_out import KeepOut
from paperroute.render.markers import (
    AGE_FACE,
    BADGE_FACE,
    OFFSETS,
    STOP_HALF,
    badge_box,
    badge_half,
    draw_markers,
    draw_tab,
    marker_box,
    nearest_first,
    overlaps,
    place,
    square,
    tab_box,
    wedge_points,
)
from paperroute_device.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Point,
    text_width,
)
from paperroute_transit.core.map_view import MAP_HEIGHT, MAP_WIDTH, Marker
from paperroute_transit.core.strip_extents import age_text
from paperroute_transit.core.tiles import CITY, ZOOMS

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
        reserved=[square(at=MIDDLE, half=STOP_HALF)],
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


def test_second_badge_on_a_spot_is_dropped() -> None:
    """A second badge on a spot is not slid away; only the first shows."""
    markers = [marker(), replace(marker(), route='T1')]
    placed = place(markers=markers, reserved=[])
    assert [item.marker for item in placed] == markers[:1]
    assert placed[0].centre == SPOT
    grey = drawn(markers=markers)
    assert int(grey[86, 100]) == PAPER


def test_crowded_badges_show_once() -> None:
    """Forty badges on one spot draw one badge, on the spot."""
    placed = place(markers=[marker()] * 40, reserved=[])
    assert [item.centre for item in placed] == [SPOT]


@pytest.mark.parametrize('mpp', ZOOMS)
def test_other_vehicles_never_get_badges(mpp: int) -> None:
    """At every zoom another vehicle is only its arrow and number."""
    other = replace(marker(bearing=90.0), pinned=False)
    canvas = CountingCanvas()
    draw_markers(
        canvas,
        markers=[other],
        mpp=mpp,
        origin=(0.0, 0.0),
        reserved=[],
    )
    ink = np.argwhere(canvas.grey() == INK)
    assert len(ink) > 0
    assert ink[:, 0].min() >= 92 and ink[:, 0].max() <= 108
    assert ink[:, 1].min() >= 92
    assert not canvas.lines


def test_pinned_badges_over_other_arrows() -> None:
    """An arrow under a pinned badge leaves the badge whole."""
    pinned = marker()
    under = replace(
        marker(at=(90.0, 110.0), bearing=90.0), route='T1', pinned=False,
    )
    alone = drawn(markers=[pinned])
    over = drawn(markers=[under, pinned])
    left, top, right, bottom = badge_box(route='606', centre=SPOT)
    inside = (
        slice(math.ceil(top), math.floor(bottom)),
        slice(math.ceil(left), math.floor(right)),
    )
    assert (over[inside] == alone[inside]).all()
    assert (over != alone).any()


def test_marker_on_the_stop_slides_away() -> None:
    """A badge just below the stop slides further down, off it."""
    stop = (MAP_WIDTH / 2, MAP_HEIGHT / 2)
    near = marker(at=(stop[0], stop[1] + 6))
    reserved = [square(at=stop, half=STOP_HALF)]
    placed = place(markers=[near], reserved=reserved)
    assert placed[0].centre == (stop[0], stop[1] + 34)
    grey = drawn(markers=[near])
    edge = int(stop[0] - badge_half(route='606')[0]) + 2
    assert int(grey[int(stop[1]) + 34, edge]) == INK
    assert int(grey[int(stop[1]) + 6, edge]) == PAPER


def test_badge_on_the_from_slides_clear() -> None:
    """A bus waiting exactly at the From slides 36 px up, clear of it."""
    stop = (MAP_WIDTH / 2, MAP_HEIGHT / 2)
    waiting = marker(at=stop)
    reserved = [square(at=stop, half=STOP_HALF)]
    placed = place(markers=[waiting], reserved=reserved)
    assert placed[0].centre == (stop[0], stop[1] - 36)
    box = badge_box(route='606', centre=placed[0].centre)
    assert not overlaps(first=box, second=reserved[0])


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


def test_stale_other_vehicle_has_no_tab() -> None:
    """A stale unpinned vehicle is only its mark, with no tab."""
    stale = replace(marker(live=False, age=240.0), pinned=False)
    grey = drawn(markers=[stale], mpp=CITY)
    assert int(grey[100, 100]) == INK
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
    assert [item.marker for item in placed] == [pinned]
    grey = drawn(markers=[*others, pinned])
    left = first_column(edge=100 - badge_half(route='606')[0])
    assert int(grey[93, left + 3]) == PAPER


@pytest.mark.parametrize(('gap', 'drops'), [(23.5, True), (24.5, False)])
def test_badges_keep_12_px_apart_sideways(gap: float, drops: bool) -> None:
    """Side by side, badges need a 24 px gap: 12 px grown on each."""
    width = 2 * badge_half(route='606')[0]
    beside = marker(at=(100.0 + width + gap, 100.0))
    placed = place(markers=[marker(), beside], reserved=[])
    assert (len(placed) == 1) is drops


@pytest.mark.parametrize(('gap', 'drops'), [(7.5, True), (8.5, False)])
def test_badges_keep_4_px_apart_vertically(gap: float, drops: bool) -> None:
    """Stacked, badges need an 8 px gap: 4 px grown on each."""
    below = marker(at=(100.0, 120.0 + gap))
    placed = place(markers=[marker(), below], reserved=[])
    assert (len(placed) == 1) is drops


def test_origin_moves_badges_and_leaders() -> None:
    """Badges and leaders are drawn offset by the map's origin."""
    grey = drawn(markers=[marker(at=MIDDLE)], origin=(30.0, 50.0))
    x, y = MIDDLE[0] + 30, MIDDLE[1] + 50
    edge = first_column(edge=x - badge_half(route='606')[0]) + 1
    assert int(grey[int(y) - 36, edge]) == INK
    assert int(grey[int(y) - 36, edge - 30]) == PAPER
    assert int(grey[int(y) - 10, int(x)]) == INK
    assert int(grey[int(MIDDLE[1]) - 10, int(MIDDLE[0])]) == PAPER


def test_stuck_badge_is_dropped_without_claiming_its_box() -> None:
    """A badge with no free spot is dropped and blocks nothing later."""
    points = [(100.0 + dx, 100.0 + dy) for dx, dy in OFFSETS]
    reserved = [(x, y, x, y) for x, y in points]
    below = marker(at=(100.0, 116.0))
    placed = place(markers=[marker(), below], reserved=reserved)
    assert [item.marker for item in placed] == [below]


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
    markers = [marker(), replace(marker(at=MIDDLE), route='T1')]
    reserved = [square(at=spot, half=STOP_HALF) for spot in (SPOT, MIDDLE)]
    draw_markers(
        canvas,
        markers=markers,
        mpp=BADGE_ZOOM,
        origin=(0, 0),
        reserved=reserved,
    )
    moved = sum(
        (item.marker.x, item.marker.y) != item.centre
        for item in place(markers=markers, reserved=reserved)
    )
    assert moved == 2
    strokes = [(width, grey) for _, width, grey in canvas.lines]
    assert strokes == [(3, PAPER), (1.5, INK)] * moved
    assert canvas.calls == [(moved, 3.5, INK)]


@pytest.mark.parametrize(('y', 'slides'), [(251.0, True), (252.0, False)])
def test_stop_box_is_18_px_each_side(y: float, slides: bool) -> None:
    """A badge must clear the stop's 36 px box by its 4 px growth."""
    canvas = CountingCanvas()
    below = marker(at=(MAP_WIDTH / 2, y))
    draw_markers(
        canvas,
        markers=[below],
        mpp=BADGE_ZOOM,
        origin=(0, 0),
        reserved=[square(at=MIDDLE, half=STOP_HALF)],
    )
    assert bool(canvas.lines) is slides


@pytest.mark.parametrize(
    ('stop', 'slides'),
    [((MAP_WIDTH / 2, MAP_HEIGHT / 2 + 1), True), (None, False)],
)
def test_reserved_box_sits_where_given(
    stop: Point | None,
    slides: bool,
) -> None:
    """Badges keep clear of the reserved boxes given, and only those."""
    canvas = CountingCanvas()
    below = marker(at=(MAP_WIDTH / 2, 252.5))
    draw_markers(
        canvas,
        markers=[below],
        mpp=BADGE_ZOOM,
        origin=(0, 0),
        reserved=[] if stop is None else [square(at=stop, half=STOP_HALF)],
    )
    assert bool(canvas.lines) is slides


def test_badge_half_pads_the_route() -> None:
    """A badge is the route's width plus 5 px a side, and 20 px tall."""
    width = text_width(text='606', face=BADGE_FACE)
    assert badge_half(route='606') == (width / 2 + 5, 10)


def test_pinned_badges_at_city_zoom() -> None:
    """At 45 m/px a pinned live marker is a badge, not a dot."""
    grey = drawn(markers=[marker()], mpp=CITY)
    edge = math.ceil(100 - badge_half(route='606')[0])
    assert int(grey[100, edge]) == INK
    assert int(grey[100, 100]) == INK


def test_overlapping_badges_keep_only_the_nearest_to_the_stop() -> None:
    """Of badges that would overlap, only the one nearest the From shows."""
    far = marker(at=(100.0, 90.0))
    near = replace(marker(at=(100.0, 104.0)), route='T1')
    ordered = nearest_first(markers=[far, near], near=(100.0, 200.0))
    placed = place(markers=ordered, reserved=[])
    assert [item.marker for item in placed] == [near]
    assert placed[0].centre == (near.x, near.y)


def test_badge_with_no_free_spot_is_dropped() -> None:
    """A badge with every spot reserved is not drawn at all."""
    reserved = [(-1000.0, -1000.0, 1000.0, 1000.0)]
    assert not place(markers=[marker()], reserved=reserved)


def test_badges_and_arrows_keep_out_of_the_corner_boxes() -> None:
    """Badges slide clear of a corner box; vehicles inside it are not drawn."""
    corner = (80.0, 80.0, 120.0, 120.0)
    keep = KeepOut(places=(corner,), streets=(), corners=(corner,))
    arrow = replace(
        marker(at=(110.0, 110.0), bearing=90.0), route='T1', pinned=False,
    )
    canvas = CountingCanvas()
    canvas.rect(box=(0, 0, WIDTH, HEIGHT), grey=PAPER)
    beside = replace(marker(at=(100.0, 128.0)), route='T1')
    draw_markers(
        canvas,
        markers=[marker(), beside, arrow],
        mpp=CITY,
        origin=(0.0, 0.0),
        reserved=[],
        keep_out=keep,
    )
    grey = canvas.grey()
    assert (grey[80:120, 80:120] == PAPER).all()
    assert canvas.lines


def test_badge_crowding_a_kept_one_is_dropped_not_slid() -> None:
    """A badge on a kept badge is dropped, though a slide would free it."""
    beside = replace(marker(at=(100.0, 116.0)), route='T1')
    reserved = [square(at=(100.0, y), half=2.0) for y in (80.0, 120.0)]
    placed = place(markers=[marker(), beside], reserved=reserved)
    assert [item.marker.route for item in placed] == ['606']
