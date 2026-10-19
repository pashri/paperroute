"""Tests for keeping strip badges off names, discs and each other."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from paperroute.core.map_view import Marker
from paperroute.core.strip_badges import (
    DISC_SPAN,
    HALF_WIDE,
    Badge,
    Keepouts,
    Spot,
    overlaps,
    pushed,
)

STEP: Final[float] = 22.0  # two plain badges' centres, one under another
BESIDE: Final[float] = 30 + DISC_SPAN  # first slot beside a disc at x 30


def badge(*, x: float, y: float, low: float, high: float,
          route: str = 'T1', bearing: float | None = 180.0,
          age: float | None = None) -> Badge:
    """
    Make a strip badge on a hop.

    Parameters
    ----------
    x : float
        Track x.
    y : float
        Centre y.
    low : float
        The hop's upper row y.
    high : float
        The hop's lower row y.
    route : str
        Route label.
    bearing : float | None
        Wedge bearing for a live badge, or None for a timetable one.
    age : float | None
        Seconds since a stale vehicle reported.

    Returns
    -------
    Badge
        The badge.
    """
    return Badge(
        marker=Marker(
            route=route, x=x, y=y, bearing=bearing,
            live=bearing is not None, pinned=True, age=age,
        ),
        low=low,
        high=high,
    )


def shown(*, badges: Sequence[Badge], spots: dict[str, Spot],
          discs: Sequence[Spot] = (), top: float = 0.0
          ) -> list[tuple[str, float, float]]:
    """
    Push badges and list each drawn one's label and centre.

    Parameters
    ----------
    badges : Sequence[Badge]
        Badges.
    spots : dict[str, Spot]
        Visible rows' spots.
    discs : Sequence[Spot]
        The discs.
    top : float
        Top of the rows.

    Returns
    -------
    list[tuple[str, float, float]]
        Label, x and y, by x then y.
    """
    return [
        (each.marker.route, each.marker.x, each.marker.y)
        for each in pushed(
            badges=badges, keep=Keepouts(spots=spots, discs=discs, top=top),
        )
    ]


def test_pushed_caps_a_crowded_hop_at_two_badges() -> None:
    """Eight badges on one hop show one badge and a '+7' on that hop."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=136)}
    crowd = [
        badge(x=30, y=100 + i, low=100, high=136, route=f'T{i}', bearing=None)
        for i in range(8)
    ]
    first, rest = pushed(badges=crowd, keep=Keepouts(spots=spots))
    assert (first.marker.route, first.marker.y) == ('T0', 100)
    assert (rest.marker.route, rest.marker.y) == ('+7', 100 + STEP)
    assert (rest.marker.live, rest.marker.bearing) == (False, None)


def test_pushed_keeps_badges_apart_in_separate_hops() -> None:
    """Badges far enough apart are each kept, pushed at most one step."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=172)}
    found = shown(
        badges=[
            badge(x=30, y=100, low=100, high=136, bearing=None),
            badge(x=30, y=110, low=100, high=136, bearing=None),
            badge(x=30, y=172, low=172, high=208, bearing=None),
            badge(x=30, y=172, low=172, high=208, bearing=None),
        ],
        spots=spots,
    )
    assert [y for _, _, y in found] == [100, 122, 172, 194]


def test_pushed_keeps_spur_badges_off_main_line_names() -> None:
    """A spur badge level with a main-line row moves onto its track."""
    spots = {'main': Spot(x=30, y=100), 'spur': Spot(x=46, y=136)}
    found = shown(
        badges=[
            badge(x=46, y=100, low=100, high=136),
            badge(x=46, y=136, low=100, high=136, route='S'),
        ],
        spots=spots,
    )
    assert found == [('T1', 30, 100), ('S', 46, 136)]
    between = shown(
        badges=[badge(x=46, y=122, low=100, high=136)], spots=spots,
    )
    assert between == [('T1', 46, 122)]


def test_pushed_stacks_badges_in_neighbouring_columns() -> None:
    """A spur badge level with a main-line badge goes below it, not on it."""
    spots = {'main': Spot(x=30, y=100), 'spur': Spot(x=46, y=172)}
    found = shown(
        badges=[
            badge(x=30, y=140, low=100, high=172, route='M', bearing=None),
            badge(x=46, y=150, low=100, high=172, route='S', bearing=None),
        ],
        spots=spots,
    )
    assert found == [('M', 30, 140), ('S', 46, 162)]


def test_pushed_moves_a_badge_left_onto_a_free_place() -> None:
    """A badge moved level with a shallower row takes that row's track."""
    spots = {
        'main': Spot(x=30, y=180),
        'mid': Spot(x=62, y=100),
        'deep': Spot(x=78, y=130),
    }
    found = shown(
        badges=[
            badge(x=62, y=150, low=100, high=180, route='B', bearing=None),
            badge(x=78, y=160, low=130, high=180, route='A', bearing=None),
            badge(x=30, y=190, low=180, high=216, route='C', bearing=None),
        ],
        spots=spots,
    )
    assert found == [('A', 30, 172), ('C', 30, 194), ('B', 62, 150)]


def test_pushed_moves_a_badge_up_its_own_hop_before_folding() -> None:
    """A spur badge with no room below moves up its hop, still uncounted."""
    spots = {'main': Spot(x=30, y=90), 'spur': Spot(x=46, y=200)}
    found = shown(
        badges=[
            badge(x=30, y=140, low=90, high=200, route='M1', bearing=None),
            badge(x=30, y=150, low=90, high=200, route='M2', bearing=None),
            badge(x=46, y=150, low=90, high=200, route='S', bearing=None),
        ],
        spots=spots,
    )
    assert found == [('M1', 30, 140), ('M2', 30, 162), ('S', 46, 118)]


def test_pushed_lines_up_vehicles_at_the_from_beside_its_disc() -> None:
    """Trains at FROM stay on its row, beside the disc, then a count."""
    spots = {'A': Spot(x=30, y=100), 'C': Spot(x=30, y=136)}
    discs = (spots['C'],)
    pair = shown(
        badges=[
            badge(x=30, y=126, low=100, high=136, route='A'),
            badge(x=30, y=140, low=136, high=172, route='B'),
        ],
        spots=spots,
        discs=discs,
    )
    assert pair == [('A', BESIDE, 136), ('B', BESIDE + 2 * HALF_WIDE, 136)]
    crowd = shown(
        badges=[
            badge(x=30, y=136 + i, low=136, high=172, route=f'T{i}')
            for i in range(4)
        ],
        spots=spots,
        discs=discs,
    )
    assert crowd == [('T0', BESIDE, 136), ('+3', BESIDE + 2 * HALF_WIDE, 136)]


def test_pushed_keeps_wedges_and_tabs_off_the_disc() -> None:
    """A wedge or tab pointing at the disc needs room for itself too."""
    spots = {'C': Spot(x=30, y=136)}
    discs = (spots['C'],)
    found = [
        shown(badges=[each], spots=spots, discs=discs)[0][2]
        for each in (
            badge(x=30, y=116, low=100, high=136),
            badge(x=30, y=116, low=100, high=136, bearing=None, age=120),
            badge(x=30, y=156, low=136, high=172, bearing=0.0),
        )
    ]
    assert found == [105, 103, 167]


def test_badge_at_a_disc_on_the_top_row_stays_level_with_it() -> None:
    """A train at a Destination on the top row is drawn on that row."""
    spots = {'P': Spot(x=30, y=98), 'W': Spot(x=30, y=134)}
    found = shown(
        badges=[badge(x=30, y=104, low=98, high=134, bearing=0.0)],
        spots=spots,
        discs=(spots['P'],),
        top=80,
    )
    assert found == [('T1', BESIDE, 98)]


def test_counted_badge_drops_its_wedge() -> None:
    """A count is a hollow badge with no wedge or age."""
    crowd = [
        replace(badge(x=30, y=100, low=100, high=136, age=60), extra=0)
        for _ in range(3)
    ]
    *_, count = pushed(
        badges=crowd, keep=Keepouts(spots={'A': Spot(x=30, y=100)}),
    )
    assert (count.marker.route, count.marker.age) == ('+2', None)


def test_badge_near_the_from_but_not_at_it_stays_on_its_hop() -> None:
    """Only a vehicle at the station goes beside its disc."""
    spots = {'A': Spot(x=30, y=100), 'C': Spot(x=30, y=136)}
    discs = (spots['C'],)
    near = shown(
        badges=[badge(x=30, y=120, low=100, high=136)],
        spots=spots,
        discs=discs,
    )
    assert near == [('T1', 30, 105)]
    at = shown(
        badges=[badge(x=30, y=126, low=100, high=136)],
        spots=spots,
        discs=discs,
    )
    assert at == [('T1', BESIDE, 136)]


def test_badge_between_two_neighbouring_discs_goes_beside_one() -> None:
    """A hop too short to clear both discs counts as at the nearer one."""
    spots = {'C': Spot(x=30, y=100), 'D': Spot(x=30, y=136)}
    found = shown(
        badges=[badge(x=30, y=114, low=100, high=136)],
        spots=spots,
        discs=tuple(spots.values()),
    )
    assert found == [('T1', BESIDE, 100)]


def test_badge_with_no_room_is_drawn_on_its_own_hop() -> None:
    """A badge with no room is never counted in another hop's badge."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=136)}
    found = shown(
        badges=[
            *(
                badge(
                    x=30, y=100 + 22 * i, low=64, high=172,
                    route=f'L{i + 1}', bearing=None,
                )
                for i in range(3)
            ),
            badge(x=30, y=136, low=100, high=136, route='S', bearing=None),
        ],
        spots=spots,
    )
    assert found == [
        ('L1', 30, 100), ('L2', 30, 122), ('S', 30, 136), ('L3', 30, 158),
    ]


def test_wide_badges_in_neighbouring_columns_stack() -> None:
    """Badge widths decide whether badges side by side touch."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=70, y=172)}
    wide = shown(
        badges=[
            badge(
                x=30, y=140, low=100, high=172, route='SH100', bearing=None,
            ),
            badge(
                x=70, y=145, low=100, high=172, route='610X', bearing=None,
            ),
        ],
        spots=spots,
    )
    assert wide == [('SH100', 30, 140), ('610X', 70, 162)]
    narrow = shown(
        badges=[
            badge(x=30, y=140, low=100, high=172, route='T1'),
            badge(x=62, y=140, low=100, high=172, route='T2'),
        ],
        spots={'A': Spot(x=30, y=100), 'B': Spot(x=62, y=172)},
    )
    assert narrow == [('T1', 30, 140), ('T2', 62, 140)]


def test_down_wedge_keeps_the_badge_below_clear() -> None:
    """A wedge pointing down needs its own room above the next badge."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=208)}
    found = shown(
        badges=[
            badge(x=30, y=130, low=100, high=208, route='A'),
            badge(x=30, y=154, low=100, high=208, route='B', bearing=None),
        ],
        spots=spots,
    )
    assert found == [('A', 30, 130), ('B', 30, 161)]


def test_wedges_and_tabs_keep_off_names() -> None:
    """A tab or wedge reaching a name's row moves the badge off the name."""
    spots = {'main': Spot(x=30, y=180), 'spur': Spot(x=46, y=100)}
    for each in (
        badge(x=46, y=155, low=100, high=216, bearing=None, age=120),
        badge(x=46, y=155, low=100, high=216),
    ):
        assert shown(badges=[each], spots=spots) == [('T1', 30, 155)]
    plain = badge(x=46, y=155, low=100, high=216, bearing=None)
    assert shown(badges=[plain], spots=spots) == [('T1', 46, 155)]


def test_one_count_beside_a_disc() -> None:
    """A badge with no room never folds into a badge beside a disc."""
    spots = {
        'Z': Spot(x=30, y=64), 'A': Spot(x=30, y=100), 'C': Spot(x=30, y=136),
    }
    found = shown(
        badges=[
            badge(x=30, y=100, low=64, high=100, route='V'),
            badge(x=30, y=110, low=100, high=136, route='U'),
            badge(x=30, y=130, low=100, high=136, route='T0'),
            *(
                badge(x=30, y=136, low=136, high=172, route=f'T{i}')
                for i in range(1, 4)
            ),
        ],
        spots=spots,
        discs=(spots['C'],),
    )
    assert found == [
        ('V', 30, 100),
        ('U', 30, 105),
        ('T0', BESIDE, 136),
        ('+3', BESIDE + 2 * HALF_WIDE, 136),
    ]


def test_a_count_is_placed_again_at_its_own_size() -> None:
    """A '+2' narrower than its first badge moves up, leaving room below."""
    spots = {
        'S0': Spot(x=30, y=62),
        'S1': Spot(x=62, y=98),
        'S2': Spot(x=30, y=134),
    }
    found = pushed(
        badges=[
            badge(x=62, y=62, low=62, high=98, bearing=None),
            badge(x=30, y=62, low=62, high=134, route='SH100', bearing=None),
            badge(x=30, y=62, low=62, high=134, route='SH100', bearing=None),
            badge(x=62, y=116, low=98, high=134, bearing=None),
        ],
        keep=Keepouts(spots=spots, discs=[Spot(x=62, y=98)]),
    )
    markers = [each.marker for each in found]
    assert [each.route for each in markers].count('+2') == 1
    assert not any(
        overlaps(one=one, other=other)
        for index, one in enumerate(markers)
        for other in markers[index + 1:]
    )
