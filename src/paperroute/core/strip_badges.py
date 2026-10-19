"""Keep a strip's vehicle badges off its names, discs and each other."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute.core.map_view import Marker

BADGE_STEP: Final[int] = 22
NAME_REACH: Final[float] = 18.0  # half a strip row
BADGE_SPAN: Final[float] = 36.0  # badges nearer than this across overlap
BADGE_HALF: Final[float] = 10.0  # half a badge's height
WEDGE_REACH: Final[float] = 10.0  # a live badge's wedge past its box
TAB_REACH: Final[float] = 12.0  # a stale badge's age tab below its box
DISC_REACH: Final[float] = 12.0  # a disc's radius and a pixel
HALF_WIDE: Final[float] = 28.0  # half a five-letter badge, and a pixel
DISC_SPAN: Final[float] = DISC_REACH + HALF_WIDE  # badges nearer overlap


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


@dataclass(frozen=True)
class Badge:
    """
    A vehicle's badge and the hop it is on.

    Attributes
    ----------
    marker : Marker
        The badge.
    low : float
        Screen y of the hop's upper row.
    high : float
        Screen y of the hop's lower row; the badge's y stays between.
    extra : int
        Other badges counted in this one.
    """

    marker: Marker
    low: float
    high: float
    extra: int = 0


@dataclass(frozen=True)
class Keepouts:
    """
    What badges must stay off besides each other.

    Attributes
    ----------
    spots : Mapping[str, Spot]
        Visible rows' track spots by station, whose names start right of
        their tracks.
    discs : Sequence[Spot]
        The From and Destination discs on screen.
    top : float
        Top of the visible rows.
    """

    spots: Mapping[str, Spot]
    discs: Sequence[Spot] = ()
    top: float = 0.0


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
    Turn a badge with others folded into it into a count.

    Parameters
    ----------
    marker : Marker
        The badge.
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


def reaches(*, marker: Marker) -> tuple[float, float]:
    """
    Measure how far a badge's drawing reaches above and below its centre.

    Parameters
    ----------
    marker : Marker
        Badge.

    Returns
    -------
    tuple[float, float]
        Pixels above and below: half the badge, plus a live badge's
        wedge on the side it points to and a stale badge's age tab
        below.
    """
    heading = (
        0.0
        if marker.bearing is None or not marker.live
        else round(math.cos(math.radians(marker.bearing)), 6)
    )
    up = BADGE_HALF + (WEDGE_REACH if heading > 0 else 0.0)
    down = BADGE_HALF + max(
        WEDGE_REACH if heading < 0 else 0.0,
        0.0 if marker.age is None else TAB_REACH,
    )
    return up, down


def clear_of(*, marker: Marker, disc: Spot) -> bool:
    """
    Say whether a badge's drawing keeps off a disc.

    Parameters
    ----------
    marker : Marker
        Badge.
    disc : Spot
        Disc centre.

    Returns
    -------
    bool
        True when the badge is at least `DISC_SPAN` across from the disc,
        or its drawing ends at least `DISC_REACH` above or below the
        disc's centre.
    """
    up, down = reaches(marker=marker)
    return (
        abs(marker.x - disc.x) >= DISC_SPAN
        or marker.y <= disc.y - DISC_REACH - down
        or marker.y >= disc.y + DISC_REACH + up
    )


def at_disc(*, badge: Badge, discs: Sequence[Spot]) -> Spot | None:
    """
    Find the disc whose station a badge's vehicle is at.

    Parameters
    ----------
    badge : Badge
        Badge.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    Spot | None
        A disc the badge covers, at one end of its hop and no further
        than `NAME_REACH` from it; None when there is none.
    """
    marker = badge.marker
    return next(
        (
            disc
            for disc in discs
            if disc.y in (badge.low, badge.high)
            and abs(marker.y - disc.y) <= NAME_REACH
            and not clear_of(marker=marker, disc=disc)
        ),
        None,
    )


def beside(*, disc: Spot, crowd: Sequence[Badge]) -> list[Badge]:
    """
    Line up the badges of vehicles at a disc's station beside it.

    Parameters
    ----------
    disc : Spot
        The disc.
    crowd : Sequence[Badge]
        Badges at its station, in y order.

    Returns
    -------
    list[Badge]
        The first badge right of the disc, level with it; a second
        beside that, or, with more, a '+<n>' count of the rest.
    """
    first, *rest = crowd
    return [
        replace(
            each,
            marker=replace(
                each.marker, x=disc.x + DISC_SPAN + slot * 2 * HALF_WIDE,
                y=disc.y,
            ),
            extra=len(rest) - 1 if slot else 0,
        )
        for slot, each in enumerate((first, *rest[:1]))
    ]


def overlaps(*, one: Marker, other: Marker) -> bool:
    """
    Say whether two badges overlap.

    Parameters
    ----------
    one : Marker
        One badge.
    other : Marker
        The other.

    Returns
    -------
    bool
        True when nearer than `BADGE_SPAN` across and `BADGE_STEP` down.
    """
    return (
        abs(one.x - other.x) < BADGE_SPAN and abs(one.y - other.y) < BADGE_STEP
    )


def candidates(*, badge: Badge, placed: Sequence[Badge],
               keep: Keepouts) -> list[float]:
    """
    List the ys where a badge could go, nearest its own first.

    Parameters
    ----------
    badge : Badge
        Badge.
    placed : Sequence[Badge]
        Badges already placed.
    keep : Keepouts
        What badges stay off.

    Returns
    -------
    list[float]
        Its own y, its hop's ends, and the edges of what it must keep
        off (other badges, discs, names, the top), within its hop.
    """
    up, down = reaches(marker=badge.marker)
    edges = {
        badge.marker.y, badge.low, badge.high, keep.top + up,
        *(each.marker.y + side * BADGE_STEP for each in placed
          for side in (-1, 1)),
        *(disc.y - DISC_REACH - down for disc in keep.discs),
        *(disc.y + DISC_REACH + up for disc in keep.discs),
        *(spot.y + side * NAME_REACH for spot in keep.spots.values()
          for side in (-1, 1)),
    }
    return sorted(
        (y for y in edges if badge.low <= y <= badge.high),
        key=lambda y: (y - up < keep.top, abs(y - badge.marker.y), y),
    )


def fits(*, marker: Marker, placed: Sequence[Badge],
         discs: Sequence[Spot]) -> bool:
    """
    Say whether a badge keeps off the placed badges and the discs.

    Parameters
    ----------
    marker : Marker
        Badge where it would go.
    placed : Sequence[Badge]
        Badges already placed.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    bool
        True when it overlaps no placed badge and covers no disc.
    """
    return not any(
        overlaps(one=marker, other=each.marker) for each in placed
    ) and all(clear_of(marker=marker, disc=disc) for disc in discs)


def place(*, badge: Badge, placed: Sequence[Badge], keep: Keepouts
          ) -> Badge | None:
    """
    Find the nearest place on a badge's hop clear of everything.

    Parameters
    ----------
    badge : Badge
        Badge.
    placed : Sequence[Badge]
        Badges already placed.
    keep : Keepouts
        What badges stay off.

    Returns
    -------
    Badge | None
        The badge at the first candidate y where, moved left off the
        names level with it, it fits; None when none does.
    """
    moves = (
        in_gutter(marker=replace(badge.marker, y=y), spots=keep.spots)
        for y in candidates(badge=badge, placed=placed, keep=keep)
    )
    return next(
        (
            replace(badge, marker=marker)
            for marker in moves
            if fits(marker=marker, placed=placed, discs=keep.discs)
        ),
        None,
    )


def fold_into(*, badge: Badge, placed: Sequence[Badge]) -> int:
    """
    Choose the placed badge a badge with no room is counted in.

    Parameters
    ----------
    badge : Badge
        The badge with no room.
    placed : Sequence[Badge]
        Badges already placed; at least one overlaps it.

    Returns
    -------
    int
        Index of the last placed badge on the same hop; with none, of the
        nearest placed badge.
    """
    same = [
        index
        for index, each in enumerate(placed)
        if (each.low, each.high) == (badge.low, badge.high)
    ]
    return same[-1] if same else min(
        range(len(placed)),
        key=lambda index: abs(placed[index].marker.y - badge.marker.y),
    )


def at_stations(*, badges: Sequence[Badge], discs: Sequence[Spot]
                ) -> tuple[list[Badge], list[Badge]]:
    """
    Line up badges at the From and Destination beside their discs.

    Parameters
    ----------
    badges : Sequence[Badge]
        Badges in y order.
    discs : Sequence[Spot]
        The From and Destination discs on screen.

    Returns
    -------
    tuple[list[Badge], list[Badge]]
        The badges beside the discs, and the rest.
    """
    found = {id(each): at_disc(badge=each, discs=discs) for each in badges}
    lined = [
        placed
        for disc in discs
        if (crowd := [each for each in badges if found[id(each)] == disc])
        for placed in beside(disc=disc, crowd=crowd)
    ]
    return lined, [each for each in badges if found[id(each)] is None]


def pushed(*, badges: Sequence[Badge], keep: Keepouts) -> tuple[Badge, ...]:
    """
    Keep badges off station names, the discs and each other.

    Parameters
    ----------
    badges : Sequence[Badge]
        Badges at their vehicles' places.
    keep : Keepouts
        What badges stay off.

    Returns
    -------
    tuple[Badge, ...]
        Badges by x then y, each still on its own hop. One at the From or
        Destination goes beside its disc, level with it; the rest move
        the least they need to clear the names level with them, the
        discs and each other, top to bottom. One with no room on its hop
        is counted in a badge on the same hop, which becomes a '+<n>'.
    """
    order = sorted(
        (
            replace(each, marker=in_gutter(
                marker=each.marker, spots=keep.spots,
            ))
            for each in badges
        ),
        key=lambda each: (each.marker.y, each.marker.x),
    )
    placed, rest = at_stations(badges=order, discs=keep.discs)
    for badge in rest:
        found = place(badge=badge, placed=placed, keep=keep)
        if found is None and placed:
            index = fold_into(badge=badge, placed=placed)
            host = placed[index]
            placed[index] = replace(host, extra=host.extra + 1)
        else:
            placed.append(found or badge)
    return tuple(sorted(
        (
            replace(each, marker=counted(
                marker=each.marker, extra=each.extra,
            ))
            for each in placed
        ),
        key=lambda each: (each.marker.x, each.marker.y),
    ))
