"""Strip badges as drawn: on their hops, off names, discs and each other."""

import sqlite3
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest
import skia
from hypothesis import example, given
from hypothesis import strategies as st

from paperroute.core.map_view import Marker
from paperroute.core.strip_badges import Badge, Keepouts, Spot, pushed
from paperroute.core.strip_extents import half_width, tab_half
from paperroute.core.strip_model import (
    HOOK_GAP,
    HOOK_LENGTH,
    HOOK_RADIUS,
    HOOK_TIP,
    ROW_H,
    TRACK_X,
    hook_straight,
)
from paperroute.core.strip_vehicles import hop_marker
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
        label_x(spot=spot) if spot.exit else spot.x + NAME_GAP,
        spot.y - NAME_TOP,
        10_000.0,
        spot.y + NAME_BOTTOM,
    )


def label_x(*, spot: Spot) -> float:
    """
    Give where an exit row's label starts.

    Parameters
    ----------
    spot : Spot
        The exit's spot, mid-way along its hook's straight.

    Returns
    -------
    float
        Past the hook's arrow and the gap after it.
    """
    junction = spot.x - (HOOK_RADIUS + HOOK_LENGTH) / 2
    return junction + HOOK_LENGTH + HOOK_TIP + HOOK_GAP


def on_its_disc(*, badge: Badge, disc: Spot) -> bool:
    """
    Say whether a badge is a vehicle at a disc's station, on the disc.

    Parameters
    ----------
    badge : Badge
        Badge as placed.
    disc : Spot
        Disc centre.

    Returns
    -------
    bool
        True when it is centred on the disc and its hop ends there.
    """
    marker = badge.marker
    return (
        (marker.x, marker.y) == (disc.x, disc.y)
        and disc.y in (badge.low, badge.high)
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
          spots: Sequence[Spot], discs: Sequence[Spot],
          spur: bool = False, badge: Badge | None = None) -> bool:
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
    spur : bool
        True for a badge fixed on an exit hook, which skips the names of
        rows whose track is left of it, as those names start past it. An
        exit's own label is never skipped.
    badge : Badge | None
        The badge the marker is for, when placed: it may cover a disc it
        is centred on when its vehicle is at that disc's station.

    Returns
    -------
    bool
        True when it meets no other badge, disc or name.
    """
    box = drawn(marker=marker)
    beside = [
        disc for disc in discs if disc.y == marker.y and marker.x > disc.x
    ]
    if spur:
        beside += [
            spot for spot in spots if spot.x < marker.x and not spot.exit
        ]
    return not any((
        *(meet(one=box, other=drawn(marker=each)) for each in others),
        *(
            meet(one=box, other=disc_box(disc=disc))
            for disc in discs
            if badge is None or not on_its_disc(badge=badge, disc=disc)
        ),
        *(
            meet(one=box, other=name_box(spot=spot))
            for spot in spots
            if spot not in beside
        ),
    ))


def tracks_of(*, badge: Badge, inputs: Sequence[Badge],
              spots: Sequence[Spot]) -> set[float]:
    """
    List the xs a placed badge's vehicles start from.

    Parameters
    ----------
    badge : Badge
        Badge as placed.
    inputs : Sequence[Badge]
        The badges at their vehicles' places.
    spots : Sequence[Spot]
        Visible rows.

    Returns
    -------
    set[float]
        The vehicle x, moved off the names level with the vehicle, of
        every input on the badge's hop it may stand for: one like it, or
        any on the hop for a '+<n>' count. A spur badge keeps its x.
    """
    return {
        each.marker.x if each.spur else gutter_x(
            marker=each.marker, spots=spots,
        )
        for each in inputs
        if (each.low, each.high) == (badge.low, badge.high)
        and (
            badge.extra
            or (each.marker.route, each.marker.bearing, each.marker.age)
            == (badge.marker.route, badge.marker.bearing, badge.marker.age)
        )
    }


def has_room(*, badge: Badge, others: Sequence[Marker],
             spots: Sequence[Spot], discs: Sequence[Spot],
             tracks: set[float]) -> bool:
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
    tracks : set[float]
        The xs its vehicle may start from (see `tracks_of`).

    Returns
    -------
    bool
        True when, from every x in `tracks`, some place on its hop,
        every `STEP` pixels, keeps it clear once moved left off the
        names level with it. A badge leaves its vehicle's track only to
        clear names, so a place at another x doesn't count.
    """
    steps = int((badge.high - badge.low) / STEP)
    return all(
        any(
            clear(
                marker=moved(
                    marker=badge.marker,
                    x=track,
                    y=badge.low + step * STEP,
                    spots=[] if badge.spur else spots,
                ),
                others=others,
                spots=spots,
                discs=discs,
                spur=badge.spur,
            )
            for step in range(steps + 1)
        )
        for track in tracks
    )


def moved(*, marker: Marker, x: float, y: float,
          spots: Sequence[Spot]) -> Marker:
    """
    Put a badge at a place, moved left off the names level with it.

    Parameters
    ----------
    marker : Marker
        Badge.
    x : float
        Its track.
    y : float
        Its centre.
    spots : Sequence[Spot]
        Visible rows.

    Returns
    -------
    Marker
        The badge there.
    """
    there = replace(marker, x=x, y=y)
    return replace(there, x=gutter_x(marker=there, spots=spots))


def exit_spot(*, junction: Spot, y: float) -> Spot:
    """
    Give an exit row's spot off a junction.

    Parameters
    ----------
    junction : Spot
        The junction row's spot.
    y : float
        The exit row's centre line.

    Returns
    -------
    Spot
        Mid-way along the hook's straight, on the exit's centre line.
    """
    left, right = hook_straight(track_x=junction.x)
    return Spot(x=(left + right) / 2, y=y, exit=True)


def hop_badge(*, rows: Sequence[Spot], start: int, end: int,
              share: float, kind: float | None, label: str) -> Badge:
    """
    Put a badge on a hop as `hop_marker` does.

    Parameters
    ----------
    rows : Sequence[Spot]
        Visible rows.
    start : int
        The hop's earlier row.
    end : int
        Its later row.
    share : float
        Share of the hop run.
    kind : float | None
        Wedge bearing down or up, an age, or None.
    label : str
        Route label.

    Returns
    -------
    Badge
        On the later row's track; bound for an exit, on the earlier
        row's track until its last row, then fixed on the exit's spot
        with its wedge pointing right.
    """
    first, last = rows[start], rows[end]
    y = first.y + (last.y - first.y) * share
    spur = last.exit and abs(last.y - y) < ROW_H
    live = kind in (0.0, 180.0)
    return Badge(
        marker=Marker(
            route=label,
            x=first.x if last.exit and not spur else last.x,
            y=last.y if spur else y,
            bearing=(90.0 if spur else kind) if live else None,
            live=live,
            pinned=True,
            age=kind if kind in AGES else None,
        ),
        low=min(first.y, last.y),
        high=max(first.y, last.y),
        spur=spur,
    )


def junctions(*, kinds: Sequence[int]) -> dict[int, int]:
    """
    Choose which rows are exits and the station each one leaves.

    Parameters
    ----------
    kinds : Sequence[int]
        Per row: 0 a station, 1 an exit off the row above, 2 an exit off
        the row below.

    Returns
    -------
    dict[int, int]
        Each exit row's junction row; an exit whose neighbour is not a
        station is a station instead.
    """
    found: dict[int, int] = {}
    for slot, kind in enumerate(kinds):
        if kind == 2 and slot + 1 < len(kinds) and kinds[slot + 1] == 0:
            found[slot] = slot + 1
        elif kind == 1 and slot and slot - 1 not in found:
            found[slot] = slot - 1
    return found


@st.composite
def strip_cases(draw: st.DrawFn) -> tuple[
    dict[str, Spot], list[Badge], list[Spot],
]:
    """
    Draw rows one per slot, badges on hops between them, and discs.

    A row next to a station row may be an exit off it, below or above
    it, so a junction can have one exit each way. An exit is reached
    only by the hop from its junction; no hop leaves an exit. Discs are
    on station rows.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    tuple[dict[str, Spot], list[Badge], list[Spot]]
        Spots, badges with wide labels, wedges and tabs, and discs.
    """
    drawn_rows = draw(st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, 2)),
        min_size=2,
        max_size=11,
    ))
    junction = junctions(kinds=[kind for _, kind in drawn_rows])
    stations = {
        slot: Spot(x=TRACK_X + 16 * depth, y=62 + 36 * slot)
        for slot, (depth, _) in enumerate(drawn_rows)
        if slot not in junction
    }
    rows = [
        stations[slot] if slot in stations else exit_spot(
            junction=stations[junction[slot]], y=62 + 36 * slot,
        )
        for slot in range(len(drawn_rows))
    ]
    hops = st.tuples(
        st.integers(0, len(rows) - 1),
        st.integers(0, len(rows) - 1),
        st.floats(0, 1),
        st.sampled_from([None, 0.0, 180.0, *AGES]),
        st.sampled_from(LABELS),
    ).filter(
        lambda hop: hop[0] != hop[1]
        and hop[0] in stations
        and junction.get(hop[1], hop[0]) == hop[0],
    )
    badges = [
        hop_badge(
            rows=rows, start=start, end=end, share=share, kind=kind,
            label=label,
        )
        for start, end, share, kind, label in draw(
            st.lists(hops, max_size=12),
        )
    ]
    marked = draw(st.lists(st.sampled_from(list(stations)), max_size=2))
    spots = {f'S{slot}': row for slot, row in enumerate(rows)}
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
FACING_AT_DISCS: Final[tuple[dict[str, Spot], list[Badge], list[Spot]]] = (
    {
        'S0': Spot(x=30, y=62),
        'S1': Spot(x=30, y=98),
        'S2': Spot(x=30, y=134),
    },
    [
        Badge(
            marker=Marker(
                route='T1', x=30, y=62.0, bearing=180.0, live=True,
                pinned=True, age=None,
            ),
            low=62,
            high=134,
        ),
        Badge(
            marker=Marker(
                route='T1', x=30, y=98.0, bearing=0.0, live=True,
                pinned=True, age=None,
            ),
            low=98,
            high=134,
        ),
    ],
    [Spot(x=30, y=62), Spot(x=30, y=98)],
)  # wedges pointing at each other from discs on neighbouring rows
NARROW_TAB: Final[tuple[dict[str, Spot], list[Badge], list[Spot]]] = (
    {
        f'S{slot}': Spot(x=x, y=62 + 36 * slot)
        for slot, x in enumerate((30, 30, 30, 62, 30, 30, 30, 62, 30, 46, 62))
    },
    [
        Badge(
            marker=Marker(
                route='SH100', x=30, y=206.0, bearing=0.0, live=True,
                pinned=True, age=None,
            ),
            low=62,
            high=206,
        ),
        Badge(marker=plain(route='T1', x=30, y=260.0), low=134, high=278),
        Badge(marker=plain(route='T1', x=62, y=404.0), low=278, high=422),
        Badge(marker=plain(route='320', x=30, y=314.0), low=62, high=314),
        Badge(
            marker=Marker(
                route='M', x=62, y=422.0, bearing=None, live=False,
                pinned=True, age=60.0,
            ),
            low=170,
            high=422,
        ),
    ],
    [Spot(x=30, y=350)],
)  # a '1m' tab narrower than the widest one


@given(case=strip_cases())
@example(case=CROWD_BESIDE_DISC)
@example(case=FACING_AT_DISCS)
@example(case=NARROW_TAB)
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
            spur=each.spur, badge=each,
        )
        if each.spur and not each.extra:
            assert each.marker.x in tracks_of(
                badge=each, inputs=badges, spots=rows,
            ) or any(
                disc.y == each.marker.y and each.marker.x > disc.x
                for disc in discs
            )
        if not clear(
            marker=each.marker, others=others, spots=rows, discs=discs,
            spur=each.spur, badge=each,
        ):
            assert not has_room(
                badge=each, others=others, spots=rows, discs=discs,
                tracks=tracks_of(badge=each, inputs=badges, spots=rows),
            ) or any(
                not has_room(
                    badge=other, spots=rows, discs=discs,
                    others=[m for m in markers if m is not other.marker],
                    tracks=tracks_of(badge=other, inputs=badges, spots=rows),
                )
                for other in found
                if other is not each
                and meet(
                    one=drawn(marker=each.marker),
                    other=drawn(marker=other.marker),
                )
            )


@pytest.mark.parametrize('end', [0, 2])
def test_hop_badge_agrees_with_hop_marker_on_spurs(end: int) -> None:
    """The property's spur badges, above or below, are hop_marker's."""
    junction = Spot(x=TRACK_X, y=98)
    rows = [exit_spot(junction=junction, y=62), junction]
    rows.append(exit_spot(junction=junction, y=134))
    assert hop_badge(
        rows=rows, start=1, end=end, share=0.9, kind=0.0, label='T1',
    ) == hop_marker(
        route='T1', ends=(junction, rows[end]), fraction=0.9, age=None,
        live=True,
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


@pytest.mark.parametrize(
    'seconds',
    [0.0, 60.0, 659.0, 3_540.0, 3_600.0, 36_000.0, 82_800.0, 360_000.0],
)
def test_core_tab_width_is_never_less_than_drawn(seconds: float) -> None:
    """The core's age tab half width covers the drawn one, closely."""
    left, _, right, _ = tab_box(centre=(0.0, 0.0), age=seconds)
    drawn_half = (right - left) / 2
    assert drawn_half <= tab_half(seconds=seconds) <= drawn_half + 1
