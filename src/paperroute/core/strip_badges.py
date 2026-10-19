"""Keep a strip's vehicle badges off its names, discs and each other."""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute.core.map_view import Marker

BADGE_STEP: Final[int] = 22
STACK_REACH: Final[int] = BADGE_STEP  # furthest a badge is pushed down
NAME_REACH: Final[float] = 18.0  # half a strip row
BADGE_SPAN: Final[float] = 36.0  # badges nearer than this across overlap


@dataclass(frozen=True)
class Spot:
    """
    Where a visible strip row's track is on screen.

    Attributes
    ----------
    x : float
        Track x at the row's depth.
    y : float
        The row's centre line.
    """

    x: float
    y: float

def in_gutter(*, marker: Marker, spots: Mapping[str, Spot]) -> Marker:
    """
    Keep a badge off the names of the rows level with it.

    Parameters
    ----------
    marker : Marker
        Badge.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.

    Returns
    -------
    Marker
        The badge moved left onto the track of the shallowest row within
        `NAME_REACH` of it, whose name starts right of that track; as it
        was when no row that close sits further left.
    """
    level = (
        spot.x
        for spot in spots.values()
        if abs(spot.y - marker.y) < NAME_REACH
    )
    return replace(marker, x=min((marker.x, *level)))


def counted(*, marker: Marker, extra: int) -> Marker:
    """
    Turn the last badge of a full stack into a count.

    Parameters
    ----------
    marker : Marker
        The stack's last badge.
    extra : int
        Badges folded into it.

    Returns
    -------
    Marker
        The badge as it is with none folded in, else a hollow '+<n>'
        badge counting itself and the folded ones.
    """
    if not extra:
        return marker
    return replace(
        marker,
        route=f'+{extra + 1}',
        bearing=None,
        live=False,
        age=None,
    )


def slot_below(*, plan: Sequence[Marker | int], marker: Marker
               ) -> tuple[int | None, float]:
    """
    Find where a badge goes under the badges already placed.

    Parameters
    ----------
    plan : Sequence[Marker | int]
        The stack so far, from `stack_plan`.
    marker : Marker
        The next badge, no higher than any placed.

    Returns
    -------
    tuple[int | None, float]
        The plan index of the lowest drawn badge within `BADGE_SPAN`
        across, or None, and the badge's y: at least `BADGE_STEP` below
        that one.
    """
    near = {
        index: other
        for index, other in enumerate(plan)
        if isinstance(other, Marker) and abs(other.x - marker.x) < BADGE_SPAN
    }
    above = max(near, key=lambda index: near[index].y, default=None)
    if above is None:
        return None, marker.y
    return above, max(marker.y, near[above].y + BADGE_STEP)


def near_discs(*, marker: Marker, discs: Sequence[Spot]) -> list[Spot]:
    """
    List the discs a badge could cover at its x, top to bottom.

    Parameters
    ----------
    marker : Marker
        Badge.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    list[Spot]
        Discs nearer than `BADGE_SPAN` across, in y order.
    """
    return sorted(
        (disc for disc in discs if abs(disc.x - marker.x) < BADGE_SPAN),
        key=lambda disc: disc.y,
    )


def off_discs(*, marker: Marker, discs: Sequence[Spot]) -> Marker:
    """
    Move a badge covering a disc to the nearest clear place.

    Parameters
    ----------
    marker : Marker
        Badge.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    Marker
        The badge as it is when no disc is within `BADGE_STEP` of it,
        else moved to the nearest y `BADGE_STEP` above or below a disc
        that clears every disc; above on a tie.
    """
    near = near_discs(marker=marker, discs=discs)
    clear = [
        y
        for disc in near
        for y in (disc.y - BADGE_STEP, disc.y + BADGE_STEP)
        if all(abs(y - other.y) >= BADGE_STEP for other in near)
    ]
    if all(abs(marker.y - disc.y) >= BADGE_STEP for disc in near):
        return marker
    return replace(marker, y=min(clear, key=lambda y: (abs(y - marker.y), y)))


def past_discs(*, marker: Marker, y: float, discs: Sequence[Spot]) -> float:
    """
    Push a badge's stacked y down past any disc it would cover.

    Parameters
    ----------
    marker : Marker
        Badge, at its own x.
    y : float
        Its stacked y.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    float
        The y, or `BADGE_STEP` below each disc it comes within
        `BADGE_STEP` of, top to bottom.
    """
    for disc in near_discs(marker=marker, discs=discs):
        if abs(y - disc.y) < BADGE_STEP:
            y = disc.y + BADGE_STEP
    return y


def stack_plan(*, markers: Sequence[Marker], discs: Sequence[Spot]
               ) -> list[Marker | int]:
    """
    Work out where each badge goes when overlapping badges are pushed apart.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges in y order, none covering a disc.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    list[Marker | int]
        One entry per badge: the badge moved down to at least
        `BADGE_STEP` below the lowest drawn badge overlapping it across,
        and on past any disc it would then cover, or, when that would
        move it more than `STACK_REACH`, the index of that badge, which
        it is folded into.
    """
    plan: list[Marker | int] = []
    for marker in markers:
        above, y = slot_below(plan=plan, marker=marker)
        y = past_discs(marker=marker, y=y, discs=discs)
        if above is not None and y - marker.y > STACK_REACH:
            plan.append(above)
        else:
            plan.append(replace(marker, y=y))
    return plan


def stacked(*, plan: Sequence[Marker | int]) -> list[Marker]:
    """
    Draw a stack plan, turning badges with others folded in into counts.

    Parameters
    ----------
    plan : Sequence[Marker | int]
        The plan, from `stack_plan`.

    Returns
    -------
    list[Marker]
        The drawn badges; one with others folded into it becomes a
        '+<n>' count.
    """
    extras = Counter(entry for entry in plan if isinstance(entry, int))
    return [
        counted(marker=entry, extra=extras[index])
        for index, entry in enumerate(plan)
        if isinstance(entry, Marker)
    ]


def leftward(*, markers: Sequence[Marker], plan: Sequence[Marker | int],
             spots: Mapping[str, Spot]) -> list[Marker]:
    """
    Move badges left where their stacked place is level with a name.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges at their own y, in y order.
    plan : Sequence[Marker | int]
        Their stack plan.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.

    Returns
    -------
    list[Marker]
        The badges at their own y, each drawn one given the x
        `in_gutter` finds at its stacked y; folded ones as they are.
    """
    return [
        marker
        if isinstance(entry, int)
        else replace(marker, x=in_gutter(marker=entry, spots=spots).x)
        for marker, entry in zip(markers, plan, strict=True)
    ]


def arranged(*, markers: Iterable[Marker], discs: Sequence[Spot]
             ) -> list[Marker]:
    """
    Move badges off the discs and put them in y order.

    Parameters
    ----------
    markers : Iterable[Marker]
        Badges.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    list[Marker]
        The badges, each moved by `off_discs`, top to bottom.
    """
    return sorted(
        (off_discs(marker=marker, discs=discs) for marker in markers),
        key=lambda each: each.y,
    )


def pushed(*, markers: Sequence[Marker], spots: Mapping[str, Spot],
           discs: Sequence[Spot] = ()) -> tuple[Marker, ...]:
    """
    Keep badges off station names, the discs and each other.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    tuple[Marker, ...]
        Badges by x then y. Each is moved onto the track of any
        shallower row level with it and off any disc, then the badges are
        stacked, each pushed at most `STACK_REACH` from its own y or
        folded into a count. A badge the push brings level with a
        shallower row moves left onto its track, and the stack is worked
        out again from the badges' own y, until no badge moves left.
        Badges only ever move left, so this ends.
    """
    moved = arranged(
        markers=(in_gutter(marker=marker, spots=spots) for marker in markers),
        discs=discs,
    )
    plan = stack_plan(markers=moved, discs=discs)
    settled = arranged(
        markers=leftward(markers=moved, plan=plan, spots=spots), discs=discs,
    )
    while settled != moved:
        moved = settled
        plan = stack_plan(markers=moved, discs=discs)
        settled = arranged(
            markers=leftward(markers=moved, plan=plan, spots=spots),
            discs=discs,
        )
    return tuple(
        sorted(stacked(plan=plan), key=lambda each: (each.x, each.y)),
    )
