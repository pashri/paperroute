"""Where a strip's loop ring and spur branch sit on screen."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from paperroute_transit.core.strip_detours import DetourKind
from paperroute_transit.core.strip_model import (
    INDENT,
    PANE_TOP,
    PANE_TRACK_X,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    StripRow,
)

type Point = tuple[float, float]  # px

CIRCLE_UP_TO: Final[int] = 4  # stops
WIDE: Final[float] = 35.0  # px, half of the 70 px cap up to 4 stops
NARROW: Final[float] = 25.0  # px, half of the 50 px ellipse
MARGIN: Final[float] = 4.0  # px the ring reaches past its end stops
JOIN: Final[float] = 200.0  # degrees where the trunk meets the left side
LEAVE: Final[float] = 160.0  # degrees where the ring leaves the trunk
SWEEP: Final[float] = 320.0  # degrees from leaving to back
BOTTOM: Final[float] = STRIP_TOP + STRIP_ROWS * ROW_H  # px
DOWN: Final[float] = 180.0  # degrees


def ring_radius(*, stops: int) -> float:
    """
    Give the vertical semi-axis of a ring at the row pitch.

    Parameters
    ----------
    stops : int
        Stops on the ring.

    Returns
    -------
    float
        Pixels.
    """
    return (stops - 1) * ROW_H / 2 + MARGIN


def compass(*, dx: float, dy: float) -> float:
    """
    Give the compass bearing of a screen step, y down.

    Parameters
    ----------
    dx : float
        Step right.
    dy : float
        Step down.

    Returns
    -------
    float
        Degrees from up, clockwise, in [0, 360).
    """
    return math.degrees(math.atan2(dx, -dy)) % 360


@dataclass(frozen=True)
class Ring:
    """
    A loop's ring: two half-ellipses sharing a vertical axis.

    Attributes
    ----------
    cx : float
        Centre x.
    cy : float
        Centre y.
    r : float
        Vertical semi-axis.
    left : float
        Horizontal semi-axis left of centre.
    right : float
        Horizontal semi-axis right of centre.
    """

    cx: float
    cy: float
    r: float
    left: float
    right: float

    def at(self, *, angle: float) -> Point:
        """
        Give the outline point at a parameter angle, y down.

        Parameters
        ----------
        angle : float
            Degrees; 0 is the right side, 90 the bottom.

        Returns
        -------
        Point
            The point.
        """
        rad = math.radians(angle)
        side = self.left if math.cos(rad) < 0 else self.right
        return self.cx + side * math.cos(rad), self.cy + self.r * math.sin(rad)

    def bearing(self, *, angle: float) -> float:
        """
        Give the direction of travel at a parameter angle.

        Travel runs with the angle falling: up the right side.

        Parameters
        ----------
        angle : float
            Degrees.

        Returns
        -------
        float
            Compass bearing in degrees.
        """
        rad = math.radians(angle)
        side = self.left if math.cos(rad) < 0 else self.right
        return compass(
            dx=side * math.sin(rad),
            dy=-self.r * math.cos(rad),
        )


def ring_of(
    *,
    rows: Sequence[StripRow],
    group: int,
    track_x: float,
) -> Ring | None:
    """
    Fit a loop's ring to its rows on screen.

    The ring runs from 4 px above its top stop to 4 px below its bottom
    one, so it follows the rows' actual spacing.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    group : int
        The loop's detour group.
    track_x : float
        The trunk's x.

    Returns
    -------
    Ring | None
        The ring, or None when the loop and its two stands are not all
        on screen, so it draws as rows.
    """
    parts = [
        (index, each.detour.size)
        for index, each in enumerate(rows)
        if each.detour is not None
        and each.detour.kind is DetourKind.LOOP
        and each.detour.group == group
    ]
    if not parts:
        return None
    places, stops = [index for index, _ in parts], parts[0][1]
    if not fits(rows=rows, places=places, size=stops, track_x=track_x):
        return None
    top, bottom = rows[places[0]].y, rows[places[-1]].y
    r = abs(bottom - top) / 2 + MARGIN
    cap = WIDE if stops <= CIRCLE_UP_TO else NARROW
    side = min(r, cap)
    return Ring(
        cx=track_x - side * math.cos(math.radians(JOIN)),
        cy=(top + bottom) / 2,
        r=r,
        left=side,
        right=side,
    )


def fits(
    *,
    rows: Sequence[StripRow],
    places: list[int],
    size: int,
    track_x: float,
) -> bool:
    """
    Tell whether a loop's rows and stands are all whole on screen.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    places : list[int]
        Indexes of the loop's rows.
    size : int
        Stops on the ring.
    track_x : float
        The trunk's x; the pane's starts lower down.

    Returns
    -------
    bool
        True when every ring row is there, a stand sits either side, and
        each lies within the strip or pane.
    """
    first, last = places[0], places[-1]
    if len(places) != size or last - first + 1 != size:
        return False
    if first == 0 or last + 1 >= len(rows):
        return False
    top = (PANE_TOP if track_x == PANE_TRACK_X else STRIP_TOP) + ROW_H / 2
    return all(
        top <= each.y <= BOTTOM - ROW_H / 2
        for each in rows[first - 1:last + 2]
    )


def ring_stop(*, ring: Ring, row: StripRow) -> Point:
    """
    Give where a row's y meets the ring's right side.

    Parameters
    ----------
    ring : Ring
        The ring.
    row : StripRow
        A row on it.

    Returns
    -------
    Point
        The stop's place.
    """
    share = max(-1.0, min(1.0, (row.y - ring.cy) / ring.r))
    return ring.cx + ring.right * math.sqrt(1 - share * share), row.y


def ring_point(*, ring: Ring, share: float) -> tuple[Point, float]:
    """
    Give a point along the ring and the bearing there.

    Parameters
    ----------
    ring : Ring
        The ring.
    share : float
        0 leaving the trunk at the lower left, 1 back at the upper left.

    Returns
    -------
    tuple[Point, float]
        The point and the compass bearing.
    """
    angle = LEAVE - SWEEP * share
    return ring.at(angle=angle), ring.bearing(angle=angle)


def spur_path(
    *,
    rows: Sequence[StripRow],
    group: int,
    track_x: float,
) -> list[Point]:
    """
    Give a spur's branch from the junction to the far end.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    group : int
        The spur's detour group.
    track_x : float
        The trunk's x.

    Returns
    -------
    list[Point]
        The junction on its row, then each level one indent in.
    """
    levels = sorted(
        (
            (each.detour.slot, each)
            for each in rows
            if each.detour is not None
            and each.detour.kind is DetourKind.SPUR
            and each.detour.group == group
        ),
        key=lambda pair: pair[0],
    )
    return [
        (track_x + INDENT * (each.depth + min(slot, 1)), each.y)
        for slot, each in levels
    ]


def spur_point(
    *,
    rows: Sequence[StripRow],
    group: int,
    share: float,
    back: bool,
    track_x: float,
) -> tuple[Point, float]:
    """
    Give a point along a spur's branch and the bearing there.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    group : int
        The spur's detour group.
    share : float
        0 at the start of the leg, 1 at its end, an equal share a level.
    back : bool
        True on the way back from the far end to the junction.
    track_x : float
        The trunk's x.

    Returns
    -------
    tuple[Point, float]
        The point and the compass bearing; the trunk's top heading down
        when the spur has no rows.
    """
    path = spur_path(rows=rows, group=group, track_x=track_x)
    if back:
        path.reverse()
    if len(path) < 2:
        return (path[0] if path else (track_x, 0.0)), DOWN
    spot = max(0.0, min(1.0, share)) * (len(path) - 1)
    index = min(int(spot), len(path) - 2)
    (x0, y0), (x1, y1) = path[index], path[index + 1]
    part = spot - index
    point = (x0 + (x1 - x0) * part, y0 + (y1 - y0) * part)
    return point, compass(dx=x1 - x0, dy=y1 - y0)


def spur_places(*, rows: Sequence[StripRow], group: int) -> list[int] | None:
    """
    Find a spur's rows, junction first.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    group : int
        The spur's detour group.

    Returns
    -------
    list[int] | None
        Indexes of the junction and each level in turn, or None when any
        is missing, so the spur draws as rows.
    """
    places = sorted(
        (
            (each.detour.slot, index)
            for index, each in enumerate(rows)
            if each.detour is not None
            and each.detour.kind is DetourKind.SPUR
            and each.detour.group == group
        ),
    )
    if not places:
        return None
    indexes = [index for _, index in places]
    first = rows[indexes[0]].detour
    size = first.size if first is not None else -1
    whole = [slot for slot, _ in places] == list(range(size + 1))
    contiguous = indexes == list(range(indexes[0], indexes[-1] + 1))
    return indexes if whole and contiguous else None


def groups_of(*, rows: Sequence[StripRow], kind: DetourKind) -> list[int]:
    """
    List the detour groups of one kind on a strip's rows.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    kind : DetourKind
        Ring or spur.

    Returns
    -------
    list[int]
        Each group once, in row order.
    """
    return list(dict.fromkeys(
        each.detour.group
        for each in rows
        if each.detour is not None and each.detour.kind is kind
    ))


def handed_rows(
    *,
    rows: Sequence[StripRow],
    track_x: float,
) -> tuple[frozenset[int], frozenset[int]]:
    """
    Say which rows a ring or spur draws in place of the usual drawing.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    track_x : float
        The trunk's x.

    Returns
    -------
    tuple[frozenset[int], frozenset[int]]
        Indexes whose track it draws, and indexes whose mark, name and
        time it draws: a whole ring's rows and the stand below it, and a
        whole spur's levels past the junction.
    """
    tracks: set[int] = set()
    marks: set[int] = set()
    for group in groups_of(rows=rows, kind=DetourKind.LOOP):
        if ring_of(rows=rows, group=group, track_x=track_x) is None:
            continue
        places = loop_places(rows=rows, group=group)
        marks.update(places)
        tracks.update([*places, places[-1] + 1])
    for group in groups_of(rows=rows, kind=DetourKind.SPUR):
        levels = spur_places(rows=rows, group=group) or []
        marks.update(levels[1:])
        tracks.update(levels[1:])
    return frozenset(tracks), frozenset(marks)


def loop_places(*, rows: Sequence[StripRow], group: int) -> list[int]:
    """
    Find a loop's rows.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The strip's rows.
    group : int
        The loop's detour group.

    Returns
    -------
    list[int]
        Indexes of its rows, top first.
    """
    return [
        index
        for index, each in enumerate(rows)
        if each.detour is not None
        and each.detour.kind is DetourKind.LOOP
        and each.detour.group == group
    ]
