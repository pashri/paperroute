"""Facts about other vehicles' arrows and route numbers."""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute.core.map_view import MAP_HEIGHT, MAP_WIDTH, Marker
from paperroute.render.canvas import INK, PAPER, Box, Canvas, Face, text_width
from paperroute.render.keep_out import NO_KEEP_OUT, KeepOut
from paperroute.render.others import (
    MARK_HALF,
    NUMBER_DROP,
    NUMBER_FACE,
    NUMBER_GAP,
    Number,
    arrow_points,
    draw_mark,
    draw_others,
    mark_box,
    number_spots,
    overlaps_any,
    place_numbers,
    signed,
)

SPOT = (100.0, 100.0)


def other(*, at: tuple[float, float] = SPOT, route: str = '401') -> Marker:
    """
    Make another vehicle's marker heading east.

    Parameters
    ----------
    at : tuple[float, float]
        Map pixel position.
    route : str
        Route label.

    Returns
    -------
    Marker
        A live, unpinned marker.
    """
    return Marker(
        route=route, x=at[0], y=at[1], bearing=90.0, live=True, pinned=False,
    )


def drawn_others(*, markers: list[Marker], mpp: int) -> np.ndarray:
    """
    Draw other vehicles on paper.

    Parameters
    ----------
    markers : list[Marker]
        Vehicles.
    mpp : int
        Metres per pixel.

    Returns
    -------
    np.ndarray
        Grey pixels.
    """
    canvas = Canvas()
    draw_others(
        canvas,
        markers=markers,
        mpp=mpp,
        origin=(0.0, 0.0),
        taken=[],
        keep_out=NO_KEEP_OUT,
    )
    return canvas.grey()


def overlap(*, first: Box, second: Box) -> bool:
    """
    Check whether two boxes share area.

    Parameters
    ----------
    first : Box
        One box.
    second : Box
        The other.

    Returns
    -------
    bool
        True when they overlap; touching edges do not.
    """
    return (
        first[0] < second[2]
        and first[2] > second[0]
        and first[1] < second[3]
        and first[3] > second[1]
    )


def test_arrow_points_along_bearing() -> None:
    """At bearing 90 the tip is the rightmost point, 5 px right."""
    points = arrow_points(centre=SPOT, bearing=90.0, scale=1.0)
    tip = max(points)
    assert tip[0] == SPOT[0] + 5
    assert tip[1] == SPOT[1]
    assert points[0] == tip


def test_arrow_points_up_at_north() -> None:
    """At bearing 0 the tip is 5 px above the centre."""
    tip = arrow_points(centre=SPOT, bearing=0.0, scale=1.0)[0]
    assert tip == (SPOT[0], SPOT[1] - 5)


def test_no_bearing_draws_a_dot() -> None:
    """With no bearing the mark is a 4 px ink dot on a 6 px paper dot."""
    canvas = Canvas()
    canvas.rect(box=(0, 0, 200, 200), grey=INK)
    draw_mark(canvas, centre=SPOT, bearing=None)
    grey = canvas.grey()
    assert grey[100, 100] == INK
    assert grey[100, 102] == PAPER
    assert grey[100, 104] == INK
    assert grey[96, 100] == INK


def test_arrow_has_a_paper_halo() -> None:
    """An arrow on ink is ringed with paper."""
    canvas = Canvas()
    canvas.rect(box=(0, 0, 200, 200), grey=INK)
    draw_mark(canvas, centre=SPOT, bearing=90.0)
    grey = canvas.grey()
    assert grey[100, 103] == INK
    assert grey[100, 106] == PAPER
    assert grey[100, 110] == INK


def test_mark_box_is_8_px_each_side() -> None:
    """A mark claims a 16 px square."""
    assert MARK_HALF == 8.0
    assert mark_box(centre=SPOT) == (92.0, 92.0, 108.0, 108.0)


def test_numbers_only_at_14_m_per_px_and_closer() -> None:
    """At 25 m/px a lone mark has no number; at 14 m/px it has."""
    right = (slice(90, 110), slice(108, 140))
    far = drawn_others(markers=[other()], mpp=25)
    near = drawn_others(markers=[other()], mpp=14)
    assert (far[right] == PAPER).all()
    assert (near[right] == INK).any()


def test_number_sits_right_of_the_mark() -> None:
    """A free number starts 6 px right of the mark, 4 px below it."""
    (number,) = place_numbers(
        markers=[other()], taken=[], keep_out=NO_KEEP_OUT,
    )
    assert number == Number(route='401', at=(106.0, 104.0), anchor='ls')


def test_number_moves_left_of_a_place_name() -> None:
    """A place name on the right sends the number left."""
    name = (110.0, 90.0, 160.0, 110.0)
    (number,) = place_numbers(
        markers=[other()],
        taken=[],
        keep_out=KeepOut(places=(name,), streets=()),
    )
    assert number == Number(route='401', at=(94.0, 104.0), anchor='rs')


def test_number_left_out_when_both_sides_taken() -> None:
    """With both sides taken there is no number, but the mark stays."""
    taken = [(40.0, 80.0, 92.0, 120.0), (108.0, 80.0, 160.0, 120.0)]
    assert not place_numbers(
        markers=[other()], taken=taken, keep_out=NO_KEEP_OUT,
    )
    canvas = Canvas()
    draw_others(
        canvas,
        markers=[other()],
        mpp=14,
        origin=(0.0, 0.0),
        taken=taken,
        keep_out=NO_KEEP_OUT,
    )
    grey = canvas.grey()
    assert grey[100, 103] == INK
    assert (grey[90:110, 109:140] == PAPER).all()


def test_street_name_yields_only_when_no_side_is_free() -> None:
    """A street name moves a number left, and yields when both are."""
    right = (110.0, 90.0, 160.0, 110.0)
    left = (40.0, 90.0, 90.0, 110.0)
    (moved,) = place_numbers(
        markers=[other()],
        taken=[],
        keep_out=KeepOut(places=(), streets=(right,)),
    )
    (over,) = place_numbers(
        markers=[other()],
        taken=[],
        keep_out=KeepOut(places=(), streets=(right, left)),
    )
    assert moved.anchor == 'rs'
    assert over == Number(route='401', at=(106.0, 104.0), anchor='ls')


def test_numbers_avoid_other_marks() -> None:
    """A mark just right of another sends the first one's number left."""
    first, second = other(), other(at=(120.0, 100.0), route='T1')
    numbers = place_numbers(
        markers=[first, second], taken=[], keep_out=NO_KEEP_OUT,
    )
    assert [number.anchor for number in numbers] == ['rs', 'ls']


def test_number_spots_right_then_left() -> None:
    """The right spot starts at the gap; the left one ends there."""
    right, left = number_spots(route='401', mark=SPOT)
    width = text_width(text='401', face=NUMBER_FACE)
    assert right[0] == 106.0 - 2
    assert right[2] == 106.0 + width + 2
    assert left[2] == 94.0 + 2
    assert left[0] == 94.0 - width - 2
    assert right[1] == left[1] < 100.0 < right[3] == left[3]


def test_overlaps_any_ignores_touching_edges() -> None:
    """Boxes that only touch do not overlap."""
    boxes = np.array([
        signed(box=(0.0, 0.0, 10.0, 10.0)),
        signed(box=(20.0, 0.0, 30.0, 10.0)),
    ])
    assert not overlaps_any(box=(10.0, 0.0, 20.0, 10.0), boxes=boxes)
    assert overlaps_any(box=(9.0, 0.0, 20.0, 10.0), boxes=boxes)
    assert not overlaps_any(box=(0.0, 0.0, 1.0, 1.0), boxes=boxes[:0])


def test_number_face_is_narrow() -> None:
    """The number face is 0.78 of the plain 800 weight's width."""
    plain = text_width(text='401', face=Face(weight=800, px=12))
    squeezed = text_width(text='401', face=NUMBER_FACE)
    assert abs(squeezed - 0.78 * plain) <= 1


BOXES = st.tuples(
    st.floats(min_value=0, max_value=MAP_WIDTH),
    st.floats(min_value=0, max_value=MAP_HEIGHT),
    st.floats(min_value=2, max_value=80),
    st.floats(min_value=2, max_value=30),
).map(lambda box: (box[0], box[1], box[0] + box[2], box[1] + box[3]))
MARKERS = st.builds(
    other,
    at=st.tuples(
        st.floats(min_value=0, max_value=MAP_WIDTH),
        st.floats(min_value=0, max_value=MAP_HEIGHT),
    ),
    route=st.sampled_from(['401', 'T1', 'M52', '606X', '8']),
)


@settings(max_examples=60, deadline=None)
@given(
    markers=st.lists(MARKERS, max_size=60),
    taken=st.lists(BOXES, max_size=10),
    places=st.lists(BOXES, max_size=10),
    streets=st.lists(BOXES, max_size=10),
)
def test_numbers_never_overlap_taken(
    markers: list[Marker],
    taken: list[Box],
    places: list[Box],
    streets: list[Box],
) -> None:
    """No number covers a taken box, a place, another mark or number."""
    numbers = place_numbers(
        markers=markers,
        taken=taken,
        keep_out=KeepOut(places=tuple(places), streets=tuple(streets)),
    )
    placed = [number_box(number=number) for number in numbers]
    for index, (box, mark) in enumerate(placed):
        marks = [(marker.x, marker.y) for marker in markers]
        marks.pop(
            next(
                spot
                for spot, centre in enumerate(marks)
                if abs(centre[0] - mark[0]) + abs(centre[1] - mark[1]) < 1e-6
            ),
        )
        blocking = [
            *taken,
            *places,
            *(mark_box(centre=centre) for centre in marks),
            *(other_box for other_box, _ in placed[index + 1 :]),
        ]
        assert not any(overlap(first=box, second=wall) for wall in blocking)


def number_box(*, number: Number) -> tuple[Box, tuple[float, float]]:
    """
    Find a placed number's box and its mark.

    Parameters
    ----------
    number : Number
        A placed number.

    Returns
    -------
    tuple[Box, tuple[float, float]]
        The number's box and the centre of the mark it names.
    """
    right = number.anchor == 'ls'
    gap = NUMBER_GAP if right else -NUMBER_GAP
    mark = (number.at[0] - gap, number.at[1] - NUMBER_DROP)
    spots = number_spots(route=number.route, mark=mark)
    return spots[0] if right else spots[1], mark
