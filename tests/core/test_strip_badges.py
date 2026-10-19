"""Tests for keeping strip badges off names, discs and each other."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.map_view import Marker
from paperroute.core.strip_badges import (
    BADGE_SPAN,
    BADGE_STEP,
    Badge,
    Keepouts,
    Spot,
    pushed,
)

BOX_HALF: Final[float] = 10.0  # half a drawn badge's height
WEDGE: Final[float] = 10.0  # a drawn wedge's length past the box
TAB: Final[float] = 12.0  # a drawn age tab's depth below the box
DISC: Final[float] = 11.0  # a drawn disc's radius
WIDEST: Final[float] = 21.0  # half the widest drawn badge, outline included
NAME_GAP: Final[float] = 22.0  # a name's start right of its track
TEXT_HALF: Final[float] = 8.0  # half a name's drawn height


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
        badge(x=30, y=100 + i, low=100, high=136, route=f'T{i}')
        for i in range(8)
    ]
    first, rest = pushed(badges=crowd, keep=Keepouts(spots=spots))
    assert (first.marker.route, first.marker.y) == ('T0', 100)
    assert (rest.marker.route, rest.marker.y) == ('+7', 100 + BADGE_STEP)
    assert (rest.marker.live, rest.marker.bearing) == (False, None)


def test_pushed_keeps_badges_apart_in_separate_hops() -> None:
    """Badges far enough apart are each kept, pushed at most one step."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=172)}
    found = shown(
        badges=[
            badge(x=30, y=100, low=100, high=136),
            badge(x=30, y=110, low=100, high=136),
            badge(x=30, y=172, low=172, high=208),
            badge(x=30, y=172, low=172, high=208),
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
        badges=[badge(x=46, y=118, low=100, high=136)], spots=spots,
    )
    assert between == [('T1', 46, 118)]


def test_pushed_stacks_badges_in_neighbouring_columns() -> None:
    """A spur badge level with a main-line badge goes below it, not on it."""
    spots = {'main': Spot(x=30, y=100), 'spur': Spot(x=46, y=172)}
    found = shown(
        badges=[
            badge(x=30, y=140, low=100, high=172, route='M'),
            badge(x=46, y=150, low=100, high=172, route='S'),
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
            badge(x=62, y=150, low=100, high=180, route='B'),
            badge(x=78, y=160, low=130, high=180, route='A'),
            badge(x=30, y=190, low=180, high=216, route='C'),
        ],
        spots=spots,
    )
    assert found == [('A', 30, 172), ('C', 30, 194), ('B', 62, 150)]


def test_pushed_moves_a_badge_up_its_own_hop_before_folding() -> None:
    """A spur badge with no room below moves up its hop, still uncounted."""
    spots = {'main': Spot(x=30, y=100), 'spur': Spot(x=46, y=200)}
    found = shown(
        badges=[
            badge(x=30, y=140, low=100, high=200, route='M1'),
            badge(x=30, y=150, low=100, high=200, route='M2'),
            badge(x=46, y=150, low=100, high=200, route='S'),
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
    assert pair == [('A', 64, 136), ('B', 108, 136)]
    crowd = shown(
        badges=[
            badge(x=30, y=136 + i, low=136, high=172, route=f'T{i}')
            for i in range(4)
        ],
        spots=spots,
        discs=discs,
    )
    assert crowd == [('T0', 64, 136), ('+3', 108, 136)]


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
    assert found == [104, 102, 168]


def test_badge_at_a_disc_on_the_top_row_stays_level_with_it() -> None:
    """A train at a Destination on the top row is drawn on that row."""
    spots = {'P': Spot(x=30, y=98), 'W': Spot(x=30, y=134)}
    found = shown(
        badges=[badge(x=30, y=104, low=98, high=134, bearing=0.0)],
        spots=spots,
        discs=(spots['P'],),
        top=80,
    )
    assert found == [('T1', 64, 98)]


def overlapping(*, drawn: Sequence[Marker]) -> bool:
    """
    Say whether any two badges overlap.

    Parameters
    ----------
    drawn : Sequence[Marker]
        Badges.

    Returns
    -------
    bool
        True when two sit nearer than `BADGE_SPAN` across and
        `BADGE_STEP` down.
    """
    return any(
        abs(one.x - other.x) < BADGE_SPAN and abs(one.y - other.y) < BADGE_STEP
        for index, one in enumerate(drawn)
        for other in drawn[index + 1:]
    )


def extent(*, marker: Marker) -> tuple[float, float]:
    """
    Give the top and bottom of a badge as drawn, wedge and tab included.

    Parameters
    ----------
    marker : Marker
        Badge.

    Returns
    -------
    tuple[float, float]
        Top and bottom y.
    """
    up = marker.live and marker.bearing == 0.0
    down = marker.live and marker.bearing == 180.0
    tab = marker.age is not None
    return (
        marker.y - BOX_HALF - (WEDGE if up else 0.0),
        marker.y + BOX_HALF + max(WEDGE if down else 0.0, TAB if tab else 0.0),
    )


def covers_disc(*, marker: Marker, disc: Spot) -> bool:
    """
    Say whether a badge's drawing covers a disc.

    Parameters
    ----------
    marker : Marker
        Badge.
    disc : Spot
        Disc centre.

    Returns
    -------
    bool
        True when they meet across and down.
    """
    top, bottom = extent(marker=marker)
    return (
        abs(marker.x - disc.x) < DISC + WIDEST
        and top < disc.y + DISC
        and bottom > disc.y - DISC
    )


def covers_name(*, marker: Marker, spot: Spot, discs: Sequence[Spot]
                ) -> bool:
    """
    Say whether a badge covers a row's name where it is drawn.

    Parameters
    ----------
    marker : Marker
        Badge.
    spot : Spot
        The row's track spot.
    discs : Sequence[Spot]
        The discs; a badge beside a disc pushes that row's name right.

    Returns
    -------
    bool
        True when level with the name and reaching past its start.
    """
    beside = spot in discs and marker.x >= spot.x + DISC + WIDEST
    return (
        not beside
        and abs(marker.y - spot.y) < BOX_HALF + TEXT_HALF
        and marker.x + WIDEST > spot.x + NAME_GAP
    )


@st.composite
def strip_cases(draw: st.DrawFn) -> tuple[
    dict[str, Spot], list[Badge], list[Spot],
]:
    """
    Draw rows one per slot, badges on hops between them, and discs.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    tuple[dict[str, Spot], list[Badge], list[Spot]]
        Spots, badges and discs.
    """
    depths = draw(st.lists(st.integers(0, 3), min_size=2, max_size=11))
    spots = {
        f'S{slot}': Spot(x=30 + 16 * depth, y=62 + 36 * slot)
        for slot, depth in enumerate(depths)
    }
    rows = list(spots.values())
    hops = st.tuples(
        st.integers(0, len(rows) - 1),
        st.integers(0, len(rows) - 1),
        st.floats(0, 1),
        st.sampled_from([None, 0.0, 180.0, 'stale']),
    ).filter(lambda hop: hop[0] != hop[1])
    badges = [
        badge(
            x=rows[end].x,
            y=rows[start].y + (rows[end].y - rows[start].y) * share,
            low=min(rows[start].y, rows[end].y),
            high=max(rows[start].y, rows[end].y),
            route=f'B{index}',
            bearing=None if kind in (None, 'stale') else float(kind),
            age=120 if kind == 'stale' else None,
        )
        for index, (start, end, share, kind) in enumerate(
            draw(st.lists(hops, max_size=12)),
        )
    ]
    marked = draw(st.lists(st.integers(0, len(rows) - 1), max_size=2))
    return spots, badges, [rows[index] for index in set(marked)]


@given(case=strip_cases())
def test_pushed_keeps_badges_accurate_and_clear(
    case: tuple[dict[str, Spot], list[Badge], list[Spot]],
) -> None:
    """Badges stay on their hops and off each other, discs and names."""
    spots, badges, discs = case
    found = pushed(badges=badges, keep=Keepouts(spots=spots, discs=discs))
    drawn = [each.marker for each in found]
    assert all(each.low <= each.marker.y <= each.high for each in found)
    assert not overlapping(drawn=drawn)
    assert not any(
        covers_disc(marker=marker, disc=disc)
        for marker in drawn
        for disc in discs
    )
    assert not any(
        covers_name(marker=marker, spot=spot, discs=discs)
        for marker in drawn
        for spot in spots.values()
    )
    assert sum(
        int(each.marker.route[1:]) if each.marker.route[0] == '+' else 1
        for each in found
    ) == len(badges)


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
