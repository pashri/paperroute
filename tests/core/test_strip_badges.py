"""Tests for keeping strip badges off names, discs and each other."""

from collections.abc import Sequence

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.map_view import Marker
from paperroute.core.strip_badges import BADGE_SPAN, BADGE_STEP, Spot, pushed


def badge(*, x: float, y: float, route: str = 'T1') -> Marker:
    """
    Make a live strip badge.

    Parameters
    ----------
    x : float
        Track x.
    y : float
        Centre y.
    route : str
        Route label.

    Returns
    -------
    Marker
        The badge.
    """
    return Marker(
        route=route, x=x, y=y, bearing=180.0, live=True, pinned=True,
    )


def test_pushed_caps_a_crowded_hop_at_two_badges() -> None:
    """Eight badges on one hop show one badge and a '+7' just below it."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=136)}
    crowd = [badge(x=30, y=100 + i, route=f'T{i}') for i in range(8)]
    first, rest = pushed(markers=crowd, spots=spots)
    assert (first.route, first.y, first.live) == ('T0', 100, True)
    assert (rest.route, rest.y, rest.live) == ('+7', 100 + BADGE_STEP, False)
    assert (rest.bearing, rest.age) == (None, None)


def test_pushed_keeps_badges_apart_in_separate_hops() -> None:
    """Badges far enough apart are each kept, pushed at most one step."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=172)}
    shown = pushed(
        markers=[badge(x=30, y=y) for y in (100, 110, 172, 172)],
        spots=spots,
    )
    assert [each.y for each in shown] == [100, 122, 172, 194]
    assert {each.route for each in shown} == {'T1'}


def test_pushed_keeps_spur_badges_off_main_line_names() -> None:
    """A spur badge level with a main-line row moves onto its track."""
    spots = {
        'main': Spot(x=30, y=100),
        'spur': Spot(x=46, y=136),
    }
    junction, spur = pushed(
        markers=[badge(x=46, y=100), badge(x=46, y=136)], spots=spots,
    )
    assert (junction.x, junction.y) == (30, 100)
    assert (spur.x, spur.y) == (46, 136)
    (between,) = pushed(markers=[badge(x=46, y=118)], spots=spots)
    assert between.x == 46


def test_pushed_stacks_badges_in_neighbouring_columns() -> None:
    """A spur badge level with a main-line badge goes below it, not on it."""
    spots = {'main': Spot(x=30, y=100), 'spur': Spot(x=46, y=172)}
    main, spur = pushed(
        markers=[badge(x=30, y=140), badge(x=46, y=150)], spots=spots,
    )
    assert (main.x, main.y) == (30, 140)
    assert (spur.x, spur.y) == (46, 162)


def overlapping(*, shown: Sequence[Marker]) -> bool:
    """
    Say whether any two badges overlap.

    Parameters
    ----------
    shown : Sequence[Marker]
        Badges.

    Returns
    -------
    bool
        True when two sit nearer than `BADGE_SPAN` across and
        `BADGE_STEP` down.
    """
    return any(
        abs(one.x - other.x) < BADGE_SPAN and abs(one.y - other.y) < BADGE_STEP
        for index, one in enumerate(shown)
        for other in shown[index + 1:]
    )


def test_pushed_restacks_a_badge_moved_left_onto_another() -> None:
    """A badge pushed left onto a busy track is stacked again there."""
    spots = {
        'main': Spot(x=30, y=180),
        'mid': Spot(x=62, y=100),
        'deep': Spot(x=78, y=130),
    }
    shown = pushed(
        markers=[
            badge(x=62, y=150, route='B'),
            badge(x=78, y=160, route='A'),
            badge(x=30, y=190, route='C'),
        ],
        spots=spots,
    )
    assert not overlapping(shown=shown)
    assert [(each.route, each.x, each.y) for each in shown] == [
        ('A', 30, 172), ('C', 30, 194), ('B', 62, 150),
    ]


def test_pushed_folds_a_badge_into_the_neighbouring_column() -> None:
    """A spur badge pushed too far joins the main-line badge's count."""
    spots = {'main': Spot(x=30, y=100), 'spur': Spot(x=46, y=200)}
    shown = pushed(
        markers=[
            badge(x=30, y=140, route='M1'),
            badge(x=30, y=150, route='M2'),
            badge(x=46, y=150, route='S'),
        ],
        spots=spots,
    )
    assert [(each.route, each.x, each.y) for each in shown] == [
        ('M1', 30, 140), ('+2', 30, 162),
    ]


@given(
    rows=st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, 11)), max_size=8,
    ),
    places=st.lists(
        st.tuples(st.integers(0, 3), st.integers(44, 476)), max_size=12,
    ),
)
def test_pushed_never_leaves_badges_overlapping(
    rows: list[tuple[int, int]], places: list[tuple[int, int]],
) -> None:
    """Whatever the rows and badges, no two drawn badges overlap."""
    spots = {
        f'{depth}-{slot}': Spot(x=30 + 16 * depth, y=62 + 36 * slot)
        for depth, slot in rows
    }
    shown = pushed(
        markers=[badge(x=30 + 16 * depth, y=y) for depth, y in places],
        spots=spots,
    )
    assert not overlapping(shown=shown)


def on_a_disc(*, shown: Sequence[Marker], discs: Sequence[Spot]) -> bool:
    """
    Say whether any badge covers a From or Destination disc.

    Parameters
    ----------
    shown : Sequence[Marker]
        Badges.
    discs : Sequence[Spot]
        Disc centres.

    Returns
    -------
    bool
        True when a badge sits nearer than `BADGE_SPAN` across and
        `BADGE_STEP` down from a disc.
    """
    return any(
        abs(one.x - disc.x) < BADGE_SPAN and abs(one.y - disc.y) < BADGE_STEP
        for one in shown
        for disc in discs
    )


def test_pushed_moves_badges_off_the_from_disc() -> None:
    """Badges just before and on FROM move above and below its disc."""
    spots = {'A': Spot(x=30, y=100), 'C': Spot(x=30, y=136)}
    discs = (spots['C'],)
    shown = pushed(
        markers=[badge(x=30, y=126, route='A'), badge(x=30, y=140)],
        spots=spots,
        discs=discs,
    )
    assert [(each.route, each.y) for each in shown] == [
        ('A', 136 - BADGE_STEP), ('T1', 136 + BADGE_STEP),
    ]


def test_pushed_folds_a_badge_pushed_across_a_disc() -> None:
    """A badge stacked onto the disc folds into the one above it."""
    spots = {'A': Spot(x=30, y=100), 'C': Spot(x=30, y=136)}
    shown = pushed(
        markers=[badge(x=30, y=100, route='A'), badge(x=30, y=110)],
        spots=spots,
        discs=(spots['C'],),
    )
    assert [(each.route, each.y) for each in shown] == [('+2', 100)]


@given(
    rows=st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, 11)), max_size=8,
    ),
    places=st.lists(
        st.tuples(st.integers(0, 3), st.integers(44, 476)), max_size=12,
    ),
    marked=st.lists(st.integers(0, 7), max_size=2),
)
def test_pushed_never_leaves_badges_on_discs(
    rows: list[tuple[int, int]], places: list[tuple[int, int]],
    marked: list[int],
) -> None:
    """Whatever the rows, badges and discs, no badge covers a disc."""
    spots = {
        f'{depth}-{slot}': Spot(x=30 + 16 * depth, y=62 + 36 * slot)
        for depth, slot in rows
    }
    discs = [
        spot
        for index, spot in enumerate(spots.values())
        if index in marked
    ]
    shown = pushed(
        markers=[badge(x=30 + 16 * depth, y=y) for depth, y in places],
        spots=spots,
        discs=discs,
    )
    assert not overlapping(shown=shown)
    assert not on_a_disc(shown=shown, discs=discs)
