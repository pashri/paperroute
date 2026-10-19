"""Strip badges as drawn: on their hops, off names, discs and each other."""

import sqlite3
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest
import skia
from hypothesis import example, given
from hypothesis import strategies as st

from paperroute.core.map_view import Marker
from paperroute.core.strip_badges import Badge, Keepouts, Spot, pushed
from paperroute.core.strip_extents import half_width
from paperroute.core.strip_model import TRACK_X
from paperroute.render.canvas import Box, Face, font
from paperroute.render.markers import (
    badge_box,
    badge_half,
    tab_box,
    wedge_points,
)
from paperroute.render.strip import BASELINE_DROP, DISC_RADIUS, MAIN_PX
from paperroute.render.strip_view import NAME_X

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'
NAME_GAP: Final[float] = NAME_X - TRACK_X  # a name's start past its track
SAMPLE_NAME: Final[str] = 'Gypsy Quay'  # capitals and descenders
LABELS: Final[tuple[str, ...]] = ('T1', 'SH100', '610X', 'M', '320')
AGES: Final[tuple[float, ...]] = (60.0, 3_540.0, 82_800.0)
STEP: Final[float] = 0.5  # pixels between tried places


def name_reach() -> tuple[float, float]:
    """
    Measure how far a drawn name reaches above and below its row.

    Returns
    -------
    tuple[float, float]
        Pixels above and below the row's centre line.
    """
    bounds = skia.Rect()
    font(Face(weight=700, px=MAIN_PX)).measureText(
        SAMPLE_NAME, skia.TextEncoding.kUTF8, bounds,
    )
    return -bounds.top() - BASELINE_DROP, bounds.bottom() + BASELINE_DROP


NAME_TOP, NAME_BOTTOM = name_reach()


def union(*, boxes: Sequence[Box]) -> Box:
    """
    Give the smallest box holding some boxes.

    Parameters
    ----------
    boxes : Sequence[Box]
        Boxes.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def drawn(*, marker: Marker) -> Box:
    """
    Give the box a badge paints, outline, wedge and age tab included.

    Parameters
    ----------
    marker : Marker
        Badge.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    centre = (marker.x, marker.y)
    left, top, right, bottom = badge_box(route=marker.route, centre=centre)
    boxes = [(left - 1, top - 1, right + 1, bottom + 1)]
    if marker.live and marker.bearing is not None:
        points = wedge_points(
            centre=centre,
            half=badge_half(route=marker.route),
            bearing=marker.bearing,
        )
        boxes.append(union(boxes=[(x, y, x, y) for x, y in points]))
    if not marker.live and marker.age is not None:
        boxes.append(tab_box(centre=centre, age=marker.age))
    return union(boxes=boxes)


def meet(*, one: Box, other: Box) -> bool:
    """
    Say whether two boxes share any area.

    Parameters
    ----------
    one : Box
        One box.
    other : Box
        The other.

    Returns
    -------
    bool
        True when they overlap.
    """
    return (
        one[0] < other[2] and other[0] < one[2]
        and one[1] < other[3] and other[1] < one[3]
    )


def disc_box(*, disc: Spot) -> Box:
    """
    Give a disc's box.

    Parameters
    ----------
    disc : Spot
        Disc centre.

    Returns
    -------
    Box
        Left, top, right, bottom.
    """
    return (
        disc.x - DISC_RADIUS, disc.y - DISC_RADIUS,
        disc.x + DISC_RADIUS, disc.y + DISC_RADIUS,
    )


def name_box(*, spot: Spot) -> Box:
    """
    Give the box a row's name may take.

    Parameters
    ----------
    spot : Spot
        The row's track spot.

    Returns
    -------
    Box
        From the name's start to far right, its height round the row.
    """
    return (
        spot.x + NAME_GAP, spot.y - NAME_TOP,
        10_000.0, spot.y + NAME_BOTTOM,
    )


def gutter_x(*, marker: Marker, spots: Sequence[Spot]) -> float:
    """
    Give the x a badge takes off the names its drawing reaches.

    Parameters
    ----------
    marker : Marker
        Badge on its own track.
    spots : Sequence[Spot]
        Visible rows.

    Returns
    -------
    float
        The leftmost of its track and the tracks of rows whose name
        height its drawing reaches.
    """
    box = drawn(marker=marker)
    return min((
        marker.x,
        *(
            spot.x
            for spot in spots
            if box[1] < spot.y + NAME_BOTTOM and spot.y - NAME_TOP < box[3]
        ),
    ))


def clear(*, marker: Marker, others: Sequence[Marker],
          spots: Sequence[Spot], discs: Sequence[Spot]) -> bool:
    """
    Say whether a badge as drawn keeps off everything.

    Parameters
    ----------
    marker : Marker
        Badge.
    others : Sequence[Marker]
        The other badges.
    spots : Sequence[Spot]
        Visible rows.
    discs : Sequence[Spot]
        The discs.

    Returns
    -------
    bool
        True when it meets no other badge, disc or name.
    """
    box = drawn(marker=marker)
    beside = [
        disc for disc in discs if disc.y == marker.y and marker.x > disc.x
    ]
    return not any((
        *(meet(one=box, other=drawn(marker=each)) for each in others),
        *(meet(one=box, other=disc_box(disc=disc)) for disc in discs),
        *(
            meet(one=box, other=name_box(spot=spot))
            for spot in spots
            if spot not in beside
        ),
    ))


def has_room(*, badge: Badge, others: Sequence[Marker],
             spots: Sequence[Spot], discs: Sequence[Spot]) -> bool:
    """
    Say whether a badge would fit anywhere on its hop.

    Parameters
    ----------
    badge : Badge
        Badge as placed.
    others : Sequence[Marker]
        The other badges.
    spots : Sequence[Spot]
        Visible rows.
    discs : Sequence[Spot]
        The discs.

    Returns
    -------
    bool
        True when some place on its hop, every `STEP` pixels, keeps it
        clear on the leftmost track it may take there.
    """
    steps = int((badge.high - badge.low) / STEP)
    tries = (
        Marker(
            route=badge.marker.route, x=badge.marker.x,
            y=badge.low + step * STEP, bearing=badge.marker.bearing,
            live=badge.marker.live, pinned=True, age=badge.marker.age,
        )
        for step in range(steps + 1)
    )
    return any(
        clear(
            marker=Marker(
                route=each.route, x=gutter_x(marker=each, spots=spots),
                y=each.y, bearing=each.bearing, live=each.live,
                pinned=True, age=each.age,
            ),
            others=others,
            spots=spots,
            discs=discs,
        )
        for each in tries
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
        Spots, badges with wide labels, wedges and tabs, and discs.
    """
    depths = draw(st.lists(st.integers(0, 3), min_size=2, max_size=11))
    spots = {
        f'S{slot}': Spot(x=TRACK_X + 16 * depth, y=62 + 36 * slot)
        for slot, depth in enumerate(depths)
    }
    rows = list(spots.values())
    hops = st.tuples(
        st.integers(0, len(rows) - 1),
        st.integers(0, len(rows) - 1),
        st.floats(0, 1),
        st.sampled_from([None, 0.0, 180.0, *AGES]),
        st.sampled_from(LABELS),
    ).filter(lambda hop: hop[0] != hop[1])
    badges = [
        Badge(
            marker=Marker(
                route=label,
                x=rows[end].x,
                y=rows[start].y + (rows[end].y - rows[start].y) * share,
                bearing=kind if kind in (0.0, 180.0) else None,
                live=kind in (0.0, 180.0),
                pinned=True,
                age=kind if kind in AGES else None,
            ),
            low=min(rows[start].y, rows[end].y),
            high=max(rows[start].y, rows[end].y),
        )
        for start, end, share, kind, label in draw(st.lists(hops, max_size=12))
    ]
    marked = draw(st.lists(st.integers(0, len(rows) - 1), max_size=2))
    return spots, badges, [rows[index] for index in set(marked)]


def count_of(*, marker: Marker) -> int:
    """
    Count the vehicles a badge stands for.

    Parameters
    ----------
    marker : Marker
        Badge.

    Returns
    -------
    int
        n for a '+n' count, else 1.
    """
    return int(marker.route[1:]) if marker.route[0] == '+' else 1


def plain(*, route: str, x: float, y: float) -> Marker:
    """
    Make a pinned badge with no wedge and no age tab.

    Parameters
    ----------
    route : str
        Label.
    x : float
        Track x.
    y : float
        Centre y.

    Returns
    -------
    Marker
        The badge.
    """
    return Marker(
        route=route, x=x, y=y, bearing=None, live=False, pinned=True,
        age=None,
    )


CROWD_BESIDE_DISC: Final[tuple[dict[str, Spot], list[Badge], list[Spot]]] = (
    {
        'S0': Spot(x=30, y=62),
        'S1': Spot(x=62, y=98),
        'S2': Spot(x=30, y=134),
    },
    [
        Badge(marker=plain(route='T1', x=62, y=62.0), low=62, high=98),
        Badge(marker=plain(route='SH100', x=30, y=62.0), low=62, high=134),
        Badge(marker=plain(route='SH100', x=30, y=62.0), low=62, high=134),
        Badge(marker=plain(route='T1', x=62, y=116.0), low=98, high=134),
    ],
    [Spot(x=62, y=98)],
)  # a '+2' narrower than the badge it began as


@given(case=strip_cases())
@example(case=CROWD_BESIDE_DISC)
def test_badges_as_drawn_stay_on_their_hops_and_clear(
    case: tuple[dict[str, Spot], list[Badge], list[Spot]],
) -> None:
    """
    Drawn badges keep off discs and names, and off each other with room.

    Two badges may meet only when one of them has no place on its own
    hop clear of everything else.
    """
    spots, badges, discs = case
    found = pushed(badges=badges, keep=Keepouts(spots=spots, discs=discs))
    rows = list(spots.values())
    markers = [each.marker for each in found]
    assert all(each.low <= each.marker.y <= each.high for each in found)
    assert sum(count_of(marker=marker) for marker in markers) == len(badges)
    for index, each in enumerate(found):
        others = markers[:index] + markers[index + 1:]
        assert clear(
            marker=each.marker, others=[], spots=rows, discs=discs,
        )
        if not clear(
            marker=each.marker, others=others, spots=rows, discs=discs,
        ):
            assert not has_room(
                badge=each, others=others, spots=rows, discs=discs,
            ) or any(
                not has_room(
                    badge=other, spots=rows, discs=discs,
                    others=[m for m in markers if m is not other.marker],
                )
                for other in found
                if other is not each
                and meet(
                    one=drawn(marker=each.marker),
                    other=drawn(marker=other.marker),
                )
            )


def test_counts_stay_on_their_own_hops() -> None:
    """Badges with no room on a hop never add to another hop's count."""
    spots = {'A': Spot(x=TRACK_X, y=100), 'B': Spot(x=TRACK_X, y=136)}
    crowd = [
        Badge(
            marker=Marker(
                route='T1', x=TRACK_X, y=100 + i if i < 4 else 100,
                bearing=None, live=False, pinned=True, age=None,
            ),
            low=100 if i < 4 else 64,
            high=136 if i < 4 else 100,
        )
        for i in range(6)
    ]
    found = pushed(badges=crowd, keep=Keepouts(spots=spots))
    by_hop: Counter[tuple[float, float]] = Counter()
    for each in found:
        by_hop[each.low, each.high] += count_of(marker=each.marker)
    assert by_hop == {(100, 136): 4, (64, 100): 2}


def real_routes() -> list[str]:
    """
    List every route label in the real timetable, if there is one.

    Returns
    -------
    list[str]
        Route labels; none without data/timetable.db.
    """
    if not REAL_DB.exists():
        return []
    with closing(sqlite3.connect(f'file:{REAL_DB}?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT DISTINCT route FROM runs').fetchall()
    return [row[0] for row in rows]


@pytest.mark.parametrize(
    'extra', [list(LABELS), ['+3', '+12', 'WWWWW', 'Stkn', '32/4', 'mw']],
)
def test_core_badge_width_is_never_less_than_drawn(extra: list[str]) -> None:
    """The core's badge half width covers the drawn one, closely."""
    for route in [*extra, *real_routes()]:
        drawn_half = badge_half(route=route)[0] + 1
        assert drawn_half <= half_width(route=route) <= drawn_half + 1
