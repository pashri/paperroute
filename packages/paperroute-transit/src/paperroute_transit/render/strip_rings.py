"""Draw a strip's loops as rings and its spurs as a side branch."""

import math
from collections.abc import Sequence
from dataclasses import replace
from itertools import pairwise
from typing import Final

from paperroute_device.render.canvas import INK, Canvas, Face
from paperroute_transit.core.strip_detours import DetourKind
from paperroute_transit.core.strip_model import INDENT, PANE_TRACK_X, StripRow
from paperroute_transit.core.strip_rings import (
    DOWN,
    JOIN,
    LEAVE,
    Ring,
    groups_of,
    loop_places,
    ring_of,
    ring_stop,
    spur_places,
)
from paperroute_transit.render.strip import (
    RIDE_WIDTH,
    RING_RADIUS,
    SPUR_PX,
    TRACK_WIDTH,
    Columns,
    draw_end,
    draw_name,
    draw_row,
    draw_station,
    draw_track,
    hook_arrow,
    name_face,
)

NAME_GAP: Final[float] = 6.0  # px between a ring stop and its name
ARROW_SIZE: Final[float] = 0.9  # of an exit arrow
TURN_SIZE: Final[float] = 0.6  # of an exit arrow
TURN_DROP: Final[float] = 12.0  # px from the far end to the turn's middle
TURN_RADIUS: Final[float] = 4.0  # px
TURN_STEPS: Final[int] = 8  # points round the turn
OUT: Final[float] = 190.0  # degrees, down the left just past the trunk
BACK: Final[float] = 250.0  # degrees, over the top heading back


def draw_rings(
    canvas: Canvas, *, rows: Sequence[StripRow], columns: Columns,
) -> None:
    """
    Draw each whole loop as a ring hanging between its two stands.

    The trunk runs into the ring and forms its left side. A loop not
    wholly on screen is left as the rows `draw_rows` drew.

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : Sequence[StripRow]
        The strip's rows.
    columns : Columns
        Where the strip's parts sit.
    """
    for group in groups_of(rows=rows, kind=DetourKind.LOOP):
        ring = ring_of(rows=rows, group=group, track_x=columns.track_x)
        if ring is not None:
            draw_ring(
                canvas,
                rows=rows,
                places=loop_places(rows=rows, group=group),
                ring=ring,
                columns=columns,
            )


def draw_ring(
    canvas: Canvas,
    *,
    rows: Sequence[StripRow],
    places: list[int],
    ring: Ring,
    columns: Columns,
) -> None:
    """
    Draw one ring, the trunk to and from it, its arrows and its rows.

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : Sequence[StripRow]
        The strip's rows.
    places : list[int]
        Indexes of the ring's rows, top first.
    ring : Ring
        The ring.
    columns : Columns
        Where the strip's parts sit.
    """
    above, below = rows[places[0] - 1], rows[places[-1] + 1]
    width = RIDE_WIDTH if below.ride else TRACK_WIDTH
    x = columns.track_x
    canvas.ellipse(
        box=(ring.cx - ring.left, ring.cy - ring.r,
             ring.cx + ring.right, ring.cy + ring.r),
        grey=INK,
        stroke=width,
    )
    canvas.line(points=[(x, above.y), ring.at(angle=JOIN)], width=width,
                grey=INK)
    canvas.line(points=[ring.at(angle=LEAVE), (x, below.y)], width=width,
                grey=INK)
    draw_ring_arrows(
        canvas,
        ring=ring,
        ys=[rows[index].y for index in places],
        below=(x, below.y),
    )
    for stand in (above, below):
        draw_station(canvas, row=stand, x=track_at(columns=columns, row=stand))
    for index in places:
        ring_row(canvas, row=rows[index], ring=ring, columns=columns)


def draw_ring_arrows(
    canvas: Canvas,
    *,
    ring: Ring,
    ys: list[float],
    below: tuple[float, float],
) -> None:
    """
    Draw a ring's out, round and back arrows and the one below it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    ring : Ring
        The ring.
    ys : list[float]
        The ring rows' centre lines.
    below : tuple[float, float]
        The stand below the ring.
    """
    for angle in arrow_angles(ring=ring, ys=ys):
        ring_arrow(
            canvas,
            at=ring.at(angle=angle),
            bearing=ring.bearing(angle=angle),
        )
    (left, top), bottom = ring.at(angle=LEAVE), below[1] - RING_RADIUS
    ring_arrow(
        canvas, at=((left + below[0]) / 2, (top + bottom) / 2), bearing=DOWN,
    )


def arrow_angles(*, ring: Ring, ys: list[float]) -> tuple[float, ...]:
    """
    Give where the out, round and back arrows sit on a ring.

    Parameters
    ----------
    ring : Ring
        The ring.
    ys : list[float]
        The ring rows' centre lines.

    Returns
    -------
    tuple[float, ...]
        Angles in degrees; the round arrow sits on the right side
        between the two rows nearest the middle.
    """
    gaps = [(one + other) / 2 for one, other in pairwise(sorted(ys))]
    y = min(gaps or [ring.cy], key=lambda gap: abs(gap - ring.cy))
    share = max(-1.0, min(1.0, (y - ring.cy) / ring.r))
    return OUT, math.degrees(math.asin(share)), BACK


def ring_arrow(
    canvas: Canvas, *, at: tuple[float, float], bearing: float,
) -> None:
    """
    Draw a small arrow centred on a point of a line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : tuple[float, float]
        The arrow's middle.
    bearing : float
        Compass degrees it points.
    """
    back = math.radians(bearing)
    shift = ARROW_SIZE * 4
    hook_arrow(
        canvas,
        x=at[0] - math.sin(back) * shift,
        y=at[1] + math.cos(back) * shift,
        size=ARROW_SIZE,
        bearing=bearing,
    )


def ring_row(
    canvas: Canvas, *, row: StripRow, ring: Ring, columns: Columns,
) -> None:
    """
    Draw a ring row's stop on the ring, its name beside it and its time.

    The name starts past a badge beside the stop (see `name_after`).

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : StripRow
        A row on the ring.
    ring : Ring
        The ring.
    columns : Columns
        Where the strip's parts sit.
    """
    x, _ = ring_stop(ring=ring, row=row)
    draw_station(canvas, row=row, x=x)
    face = name_face(row=row)
    if columns.track_x == PANE_TRACK_X:
        face = Face(weight=face.weight, px=SPUR_PX)
    draw_name(
        canvas,
        row=row,
        left=max(x + RING_RADIUS + NAME_GAP, row.name_after or 0.0),
        face=face,
        edge=draw_end(canvas, row=row, columns=columns),
        naming=columns.naming,
    )


def draw_spurs(
    canvas: Canvas, *, rows: Sequence[StripRow], columns: Columns,
) -> None:
    """
    Draw each whole spur as a branch one indent in, ending in a turn.

    The trunk runs straight on from the junction. A spur not wholly on
    screen is left as the rows `draw_rows` drew.

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : Sequence[StripRow]
        The strip's rows.
    columns : Columns
        Where the strip's parts sit.
    """
    for group in groups_of(rows=rows, kind=DetourKind.SPUR):
        places = spur_places(rows=rows, group=group)
        if places is not None:
            draw_spur(
                canvas,
                rows=[rows[index] for index in places],
                columns=columns,
            )


def draw_spur(
    canvas: Canvas, *, rows: list[StripRow], columns: Columns,
) -> None:
    """
    Draw one spur: trunk past it, the branch, the turn and its levels.

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : list[StripRow]
        The junction, then each level.
    columns : Columns
        Where the strip's parts sit.
    """
    junction, far = rows[0], rows[-1]
    width = RIDE_WIDTH if junction.ride else TRACK_WIDTH
    x = track_at(columns=columns, row=junction)
    canvas.line(points=[(x, junction.y), (x, far.y)], width=width, grey=INK)
    levels = [
        replace(
            each,
            depth=each.depth + 1,
            parent_depth=each.depth + (0 if k == 0 else 1),
        )
        for k, each in enumerate(rows[1:])
    ]
    for level in levels:
        draw_track(canvas, row=level, track_x=columns.track_x)
    turn_back(canvas, x=track_at(columns=columns, row=levels[-1]), y=far.y)
    draw_station(canvas, row=junction, x=x)
    for level in levels:
        draw_row(canvas, row=level, columns=columns)


def track_at(*, columns: Columns, row: StripRow) -> float:
    """
    Give a row's track x at its depth.

    Parameters
    ----------
    columns : Columns
        Where the strip's parts sit.
    row : StripRow
        The row.

    Returns
    -------
    float
        Pixels.
    """
    return columns.track_x + INDENT * row.depth


def turn_back(canvas: Canvas, *, x: float, y: float) -> None:
    """
    Draw the far end's turn: down, round to the right, and up.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        The far end's track x.
    y : float
        The far end's centre line.
    """
    middle = y + TURN_DROP
    arc = [
        (
            x + TURN_RADIUS + TURN_RADIUS * math.cos(angle),
            middle + TURN_RADIUS * math.sin(angle),
        )
        for angle in (
            math.pi * (1 - step / TURN_STEPS)
            for step in range(TURN_STEPS + 1)
        )
    ]
    canvas.line(points=[(x, y), *arc], width=TRACK_WIDTH - 1, grey=INK)
    end = arc[-1]
    hook_arrow(canvas, x=end[0], y=end[1], size=TURN_SIZE, bearing=0.0)
