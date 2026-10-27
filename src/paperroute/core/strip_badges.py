"""Keep a strip's vehicle badges off its names, discs and each other."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute.core.map_view import Marker
from paperroute.core.strip_extents import Extent, extent_of, reaches

NAME_REACH: Final[float] = 18.0  # half a strip row
NAME_ABOVE: Final[float] = 7.0  # a name's capitals above its row's centre
NAME_BELOW: Final[float] = 10.0  # a name's descenders below it
DISC_REACH: Final[float] = 11.0  # a disc's radius
AT_REACH: Final[float] = 12.0  # a vehicle this near a disc is at it
HALF_WIDE: Final[float] = 28.0  # half a five-letter badge, and a pixel
SLOT_SPAN: Final[float] = 2 * HALF_WIDE  # badge centres side by side


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
    exit : bool
        True for an exit row, whose spot is its stub's end.
    """

    x: float
    y: float
    exit: bool = False


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
    spur : bool
        True on the last row into an exit, where it sits fixed on the
        exit hook's straight.
    """

    marker: Marker
    low: float
    high: float
    extra: int = 0
    spur: bool = False


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


def level_with(*, marker: Marker, spot: Spot) -> bool:
    """
    Say whether a badge's drawing reaches a row's name band.

    Parameters
    ----------
    marker : Marker
        Badge.
    spot : Spot
        The row's track spot.

    Returns
    -------
    bool
        True when the badge, wedge and age tab included, meets the band
        from `NAME_ABOVE` above the row's centre line to `NAME_BELOW`
        below it.
    """
    box = extent_of(marker=marker)
    return (
        box.top < spot.y + NAME_BELOW and box.bottom > spot.y - NAME_ABOVE
    )


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
        The badge moved left onto the track of the shallowest row whose
        name band its drawing reaches, as names start right of their
        tracks; as it was when no such row sits further left.
    """
    level = (
        spot.x
        for spot in spots.values()
        if level_with(marker=marker, spot=spot)
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
        True when its drawing ends at least `DISC_REACH` from the disc's
        centre across or down.
    """
    box = extent_of(marker=marker)
    return (
        box.left >= disc.x + DISC_REACH
        or box.right <= disc.x - DISC_REACH
        or box.bottom <= disc.y - DISC_REACH
        or box.top >= disc.y + DISC_REACH
    )


def at_disc(*, badge: Badge, keep: Keepouts) -> Spot | None:
    """
    Find the disc whose station a badge's vehicle is at.

    Parameters
    ----------
    badge : Badge
        Badge.
    keep : Keepouts
        What badges stay off, the discs among them.

    Returns
    -------
    Spot | None
        The disc at the nearer end of the badge's hop, when the badge is
        within `AT_REACH` of it or its hop has no place clear of the
        discs; None otherwise.
    """
    marker = badge.marker
    near = min(
        (disc for disc in keep.discs if disc.y in (badge.low, badge.high)),
        key=lambda disc: abs(disc.y - marker.y),
        default=None,
    )
    if near is None or abs(near.y - marker.y) <= AT_REACH:
        return near
    roomless = place(badge=badge, placed=(), keep=keep) is None
    return near if roomless else None


def beside(*, disc: Spot, crowd: Sequence[Badge]) -> list[Badge]:
    """
    Line up the badges of vehicles at a disc's station on and beside it.

    Parameters
    ----------
    disc : Spot
        The disc.
    crowd : Sequence[Badge]
        Badges at its station, in y order.

    Returns
    -------
    list[Badge]
        The first badge centred on the disc; a second right beside it,
        level with it, or, with more, a '+<n>' count of the rest.
    """
    first, *rest = crowd
    extra = max(len(rest) - 1, 0)
    on_disc = replace(
        first, marker=replace(first.marker, x=disc.x, y=disc.y),
    )
    return [on_disc] + [
        replace(
            each,
            marker=counted(
                marker=replace(each.marker, x=disc.x + SLOT_SPAN, y=disc.y),
                extra=extra,
            ),
            extra=extra,
        )
        for each in rest[:1]
    ]


def on_hooks(*, badges: Sequence[Badge]) -> list[Badge]:
    """
    Keep spur badges where they are, counting ones at the same spot.

    Parameters
    ----------
    badges : Sequence[Badge]
        Spur badges, each at its exit's spot.

    Returns
    -------
    list[Badge]
        One badge per spot, unmoved; a spot with more than one vehicle
        has a '+<n>' count.
    """
    spots: dict[tuple[float, float], list[Badge]] = {}
    for each in badges:
        spots.setdefault((each.marker.x, each.marker.y), []).append(each)
    return [
        replace(
            first,
            marker=counted(marker=first.marker, extra=len(rest)),
            extra=len(rest),
        )
        for first, *rest in spots.values()
    ]


def overlaps(*, one: Marker, other: Marker) -> bool:
    """
    Say whether two badges' drawings overlap.

    Parameters
    ----------
    one : Marker
        One badge.
    other : Marker
        The other.

    Returns
    -------
    bool
        True when their drawings, outlines, wedges and age tabs
        included, share any area.
    """
    a, b = extent_of(marker=one), extent_of(marker=other)
    return (
        a.left < b.right
        and b.left < a.right
        and a.top < b.bottom
        and b.top < a.bottom
    )


def edges(*, badge: Badge, boxes: Sequence[Extent], keep: Keepouts
          ) -> set[float]:
    """
    List the ys where a badge's drawing just meets what it keeps off.

    Parameters
    ----------
    badge : Badge
        Badge.
    boxes : Sequence[Extent]
        Drawings of the badges already placed.
    keep : Keepouts
        What badges stay off.

    Returns
    -------
    set[float]
        Badge centres that put its drawing just above or below each
        placed badge, disc and name band, and just under the top.
    """
    up, down = reaches(marker=badge.marker)
    return {
        keep.top + up,
        *(box.top - down for box in boxes),
        *(box.bottom + up for box in boxes),
        *(disc.y - DISC_REACH - down for disc in keep.discs),
        *(disc.y + DISC_REACH + up for disc in keep.discs),
        *(spot.y - NAME_ABOVE - down for spot in keep.spots.values()),
        *(spot.y + NAME_BELOW + up for spot in keep.spots.values()),
    }


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
        Its own y, its hop's ends, and the `edges` of what it must keep
        off, within its hop; ones that keep it under the top first.
    """
    up, _ = reaches(marker=badge.marker)
    found = {
        badge.marker.y,
        badge.low,
        badge.high,
        *edges(
            badge=badge,
            boxes=[extent_of(marker=each.marker) for each in placed],
            keep=keep,
        ),
    }
    return sorted(
        (y for y in found if badge.low <= y <= badge.high),
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
        names level with it (see `in_gutter`), it fits; None when none
        does.
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


def host_of(*, badge: Badge, placed: Sequence[Badge], first: int
            ) -> int | None:
    """
    Choose the placed badge on the same hop a badge is counted in.

    Parameters
    ----------
    badge : Badge
        The badge with no room.
    placed : Sequence[Badge]
        Badges already placed.
    first : int
        Index of the first placed badge that may be a host; the ones
        before are beside the discs.

    Returns
    -------
    int | None
        Index of the last placed badge from `first` on the same hop, or
        None when there is none.
    """
    same = [
        index
        for index in range(first, len(placed))
        if (placed[index].low, placed[index].high) == (badge.low, badge.high)
    ]
    return same[-1] if same else None


def settle(*, badge: Badge, placed: list[Badge], keep: Keepouts,
           first: int) -> bool:
    """
    Place one badge, or count it in a badge on its hop.

    Parameters
    ----------
    badge : Badge
        Badge.
    placed : list[Badge]
        Badges already placed; the badge is added or counted.
    keep : Keepouts
        What badges stay off.
    first : int
        Index of the first placed badge that may be a host.

    Returns
    -------
    bool
        False when the badge had no room and was drawn over another.
    """
    found = place(badge=badge, placed=placed, keep=keep)
    host = None if found else host_of(badge=badge, placed=placed, first=first)
    if found is not None:
        placed.append(found)
    elif host is not None:
        extra = placed[host].extra + 1
        placed[host] = replace(placed[host], extra=extra, marker=counted(
            marker=placed[host].marker, extra=extra,
        ))
    else:
        # Last resort, no room on its hop: drawn on it over another badge,
        # never moved off it or counted in another hop's badge.
        placed.append(place(badge=badge, placed=(), keep=keep) or badge)
    return found is not None or host is not None


def retried(*, placed: list[Badge], forced: Sequence[tuple[int, Badge]],
            keep: Keepouts) -> None:
    """
    Try again to clear the badges drawn over others, now all are placed.

    Parameters
    ----------
    placed : list[Badge]
        Every placed badge; a forced one, counted or not, that now fits
        is moved.
    forced : Sequence[tuple[int, Badge]]
        Where in `placed` each badge drawn with no room is, and the badge
        as it was before placing.
    keep : Keepouts
        What badges stay off.
    """
    for index, badge in forced:
        now = placed[index]
        found = place(
            badge=replace(now, marker=replace(
                now.marker, x=badge.marker.x, y=badge.marker.y,
            )),
            placed=placed[:index] + placed[index + 1:],
            keep=keep,
        )
        placed[index] = found or now


def settled_all(*, badges: Sequence[Badge], placed: list[Badge],
                keep: Keepouts, first: int) -> list[tuple[int, Badge]]:
    """
    Place badges in turn and list the ones to try again.

    Parameters
    ----------
    badges : Sequence[Badge]
        Badges in y order.
    placed : list[Badge]
        Badges already placed; each badge is added or counted.
    keep : Keepouts
        What badges stay off.
    first : int
        Index of the first placed badge that may be a host.

    Returns
    -------
    list[tuple[int, Badge]]
        Where in `placed` each '+<n>' count and each badge drawn with no
        room is, and the badge as it was before placing; the counts
        first. A count is placed for the badge it began as, and is
        often a different size, so it is placed again.
    """
    homes: dict[int, Badge] = {}
    forced: list[tuple[int, Badge]] = []
    for badge in badges:
        size = len(placed)
        if not settle(badge=badge, placed=placed, keep=keep, first=first):
            forced.append((len(placed) - 1, badge))
        if len(placed) > size:
            homes[size] = badge
    counts = [
        (index, homes[index])
        for index, each in enumerate(placed)
        if each.extra and index in homes
    ]
    return [*counts, *forced]


def at_stations(*, badges: Sequence[Badge], keep: Keepouts,
                hooks: Sequence[Badge]) -> tuple[list[Badge], list[Badge]]:
    """
    Line up badges at the From and Destination beside their discs.

    Parameters
    ----------
    badges : Sequence[Badge]
        Badges in y order.
    keep : Keepouts
        What badges stay off, the discs among them.
    hooks : Sequence[Badge]
        Spur badges, which never move; the lineups keep off them.

    Returns
    -------
    tuple[list[Badge], list[Badge]]
        The badges beside the discs, and the rest.
    """
    found = {id(each): at_disc(badge=each, keep=keep) for each in badges}
    lined: list[Badge] = []
    fixed = [*hooks]
    for disc in keep.discs:
        if crowd := [each for each in badges if found[id(each)] == disc]:
            lined.extend(off_lined(
                lineup=beside(disc=disc, crowd=crowd), lined=fixed + lined,
            ))
    return lined, [each for each in badges if found[id(each)] is None]


def off_lined(*, lineup: Sequence[Badge], lined: Sequence[Badge]
              ) -> list[Badge]:
    """
    Move a disc's badges along, a slot at a time, off ones lined already.

    Parameters
    ----------
    lineup : Sequence[Badge]
        Badges beside one disc.
    lined : Sequence[Badge]
        Badges beside the discs before it.

    Returns
    -------
    list[Badge]
        The lineup, still level with its disc, moved right by the fewest
        whole slots that keep its drawings off the lined badges. Wedges
        of badges at discs on neighbouring rows can otherwise meet.
    """
    shift = 0.0
    while any(
        overlaps(
            one=replace(each.marker, x=each.marker.x + shift),
            other=other.marker,
        )
        for each in lineup
        for other in lined
    ):
        shift += SLOT_SPAN
    return [
        replace(each, marker=replace(each.marker, x=each.marker.x + shift))
        for each in lineup
    ]


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
        Badges by x then y, each still on its own hop. The first at the
        From or Destination (see `at_disc`) goes on its disc and the next
        beside it, level with it. A spur badge never moves, and others
        keep off it; spur badges at one spot become a count. The rest
        move the least they need to clear the names level with them, the
        discs and each other, top to bottom. One with no room
        on its hop is counted in a badge on the same hop, which becomes a
        '+<n>', or with none there is drawn on its hop anyway.
    """
    order = sorted(
        (
            replace(each, marker=in_gutter(
                marker=each.marker, spots=keep.spots,
            ))
            for each in badges
            if not each.spur
        ),
        key=lambda each: (each.marker.y, each.marker.x),
    )
    hooks = on_hooks(badges=[each for each in badges if each.spur])
    placed, rest = at_stations(badges=order, keep=keep, hooks=hooks)
    placed.extend(hooks)
    again = settled_all(
        badges=rest, placed=placed, keep=keep, first=len(placed),
    )
    retried(placed=placed, forced=again, keep=keep)
    return tuple(sorted(
        placed, key=lambda each: (each.marker.x, each.marker.y),
    ))
