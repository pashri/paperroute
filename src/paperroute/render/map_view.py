"""Draw the Map view: header, map with markers, sidebar and banner."""

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    MapView,
    screen_point,
)
from paperroute.core.stop_view import Pill, Row, arrival_line
from paperroute.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Box,
    Canvas,
    Face,
    Point,
    text_width,
)
from paperroute.render.chrome import (
    BANNER,
    FASTER,
    HEADER,
    cancelled,
    leaves,
    pill,
    route_pair,
    view_banner,
    view_header,
)
from paperroute.render.keep_out import NO_KEEP_OUT, KeepOut
from paperroute.render.map_base import HALO
from paperroute.render.map_marks import STOP_DISC, draw_stop, ridden_ring
from paperroute.render.markers import (
    CHANGE_HALF,
    END_HALF,
    STOP_HALF,
    draw_markers,
    nearest_first,
    square,
)
from paperroute.render.pair_list import draw_pair_list
from paperroute.render.tile_codec import THRESHOLD, quantise4
from paperroute.render.trip_marks import draw_trip_points

SIDEBAR_LEFT: Final[int] = 520
RULE_WIDTH: Final[int] = 3
SIDEBAR_TEXT: Final[int] = 538
SIDEBAR_RIGHT: Final[int] = 784
EMPTY_FACE: Final[Face] = Face(weight=700, px=22)
EMPTY_FACES: Final[tuple[Face, ...]] = (
    EMPTY_FACE,
    Face(weight=700, px=18),
    Face(weight=700, px=15),
)
EMPTY_LINES: Final[int] = 2
OFF_MAP: Final[float] = 1000.0  # pixels each edge box reaches out
EMPTY_LEADING: Final[int] = 4  # pixels between the empty text's lines
EMPTY_MARGIN: Final[int] = 32  # pixels each side of the empty text
EMPTY_DROP: Final[float] = STOP_DISC + 42.0  # below the centre
DESTINATION_FACE: Final[Face] = Face(weight=700, px=16)
DESTINATION_Y: Final[int] = 70
DESTINATION_WIDTH: Final[int] = 250
ROWS_TOP: Final[int] = 110
ROW_STEP: Final[int] = 72
BADGE_PX: Final[int] = 17
COUNTDOWN_FACE: Final[Face] = Face(weight=800, px=34)
COUNTDOWN_DROP: Final[int] = 12  # baseline below the badge centre
ARRIVES_FACE: Final[Face] = Face(weight=400, px=14)
ARRIVES_DROP: Final[int] = 26  # baseline below the badge centre
ROW_RULE_DROP: Final[int] = 40  # below the badge centre
ROW_RULE_LEFT: Final[int] = 534
ROW_RULE_RIGHT: Final[int] = 788
CANCELLED_LEFT: Final[int] = 630
CANCELLED_HALF: Final[int] = 13  # pixels above and below the badge centre
CANCELLED_PX: Final[int] = 15
FASTER_LEFT: Final[int] = 710
FASTER_TOP: Final[int] = 17  # below the badge centre
FASTER_BOTTOM: Final[int] = 32  # below the badge centre
FASTER_PX: Final[int] = 11
PAIRS_HEAD: Final[str] = 'Connections · leave by'
PAIRS_HEAD_Y: Final[int] = 70
PAIRS_TOP: Final[int] = 84
PAIR_STEP: Final[int] = 72


def wrap(*, text: str, face: Face, width: float) -> tuple[str, ...]:
    """
    Break text into lines at spaces, filling each line greedily.

    Parameters
    ----------
    text : str
        Text.
    face : Face
        Font.
    width : float
        Available width in pixels.

    Returns
    -------
    tuple[str, ...]
        Lines; a single word wider than the width gets a line of its own.
    """
    lines: list[str] = []
    for word in text.split():
        joined = f'{lines[-1]} {word}' if lines else word
        if lines and text_width(text=joined, face=face) <= width:
            lines[-1] = joined
        else:
            lines.append(word)
    return tuple(lines)


def empty_lines(
    *,
    text: str,
    width: int = MAP_WIDTH,
) -> tuple[tuple[str, ...], Face]:
    """
    Fit the empty text into at most two lines across the map.

    Parameters
    ----------
    text : str
        Empty text.
    width : int
        Map width in pixels.

    Returns
    -------
    tuple[tuple[str, ...], Face]
        The lines and the face, the largest of `EMPTY_FACES` that fits;
        the smallest face's lines when none does.
    """
    room = width - 2 * EMPTY_MARGIN
    for face in EMPTY_FACES:
        lines = wrap(text=text, face=face, width=room)
        fits = all(text_width(text=line, face=face) <= room for line in lines)
        if len(lines) <= EMPTY_LINES and fits:
            return lines, face
    return lines, face


def draw_empty(canvas: Canvas, *, text: str, width: int) -> None:
    """
    Draw the empty text below the stop, one line under another.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        Empty text.
    width : int
        Map width in pixels.
    """
    lines, face = empty_lines(text=text, width=width)
    top = HEADER + MAP_HEIGHT / 2 + EMPTY_DROP
    for index, line in enumerate(lines):
        canvas.text(
            at=(width / 2, top + index * (face.px + EMPTY_LEADING)),
            text=line,
            face=face,
            grey=INK,
            anchor='mm',
            halo=HALO,
        )


def stop_at(*, base: MapBase) -> tuple[float, float] | None:
    """
    Find where the From stop sits on the map.

    Parameters
    ----------
    base : MapBase
        The base map key.

    Returns
    -------
    tuple[float, float] | None
        Map pixels of the trip's origin when there are trip marks, else of
        the centre; None without either.
    """
    centre = base.centre
    point = centre if base.trip is None else base.trip.origin
    if centre is None or point is None:
        return None
    return screen_point(
        centre=centre, mpp=base.mpp, point=point, width=base.width,
    )


def map_pixels(*, base: MapBase, points: Sequence[Point]) -> list[Point]:
    """
    Place points in metres on the map.

    Parameters
    ----------
    base : MapBase
        The base map key.
    points : Sequence[Point]
        Points in metres.

    Returns
    -------
    list[Point]
        Map pixels of each point; none without a centre.
    """
    centre = base.centre
    if centre is None:
        return []
    return [
        screen_point(
            centre=centre, mpp=base.mpp, point=point, width=base.width,
        )
        for point in points
    ]


def end_boxes(*, base: MapBase) -> list[Box]:
    """
    Give the boxes of the From, the To and each change, in map pixels.

    Parameters
    ----------
    base : MapBase
        The base map key.

    Returns
    -------
    list[Box]
        The From's box where it is drawn, then the To's and each change
        square's when there are trip marks.
    """
    stop = stop_at(base=base)
    boxes = [] if stop is None else [square(at=stop, half=STOP_HALF)]
    trip = base.trip
    if trip is None:
        return boxes
    ends = () if trip.end is None else (trip.end,)
    changes = (
        *(() if trip.change is None else (trip.change,)),
        *(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in trip.others),
    )
    return [
        *boxes,
        *(
            square(at=end, half=END_HALF)
            for end in map_pixels(base=base, points=ends)
        ),
        *(
            square(at=change, half=CHANGE_HALF)
            for change in map_pixels(base=base, points=changes)
        ),
    ]


def edge_boxes(*, width: int, banner: bool) -> list[Box]:
    """
    Give the boxes off the map and under the banner, in map pixels.

    Parameters
    ----------
    width : int
        Map width in pixels.
    banner : bool
        True when a banner covers the map's top.

    Returns
    -------
    list[Box]
        Strips above, below, left and right of what shows of the map.
    """
    top = BANNER if banner else 0
    return [
        (-OFF_MAP, -OFF_MAP, width + OFF_MAP, top),
        (-OFF_MAP, MAP_HEIGHT, width + OFF_MAP, MAP_HEIGHT + OFF_MAP),
        (-OFF_MAP, -OFF_MAP, 0, MAP_HEIGHT + OFF_MAP),
        (width, -OFF_MAP, width + OFF_MAP, MAP_HEIGHT + OFF_MAP),
    ]


def stop_boxes(*, base: MapBase) -> list[Box]:
    """
    Give the boxes of the ridden stops, in map pixels.

    Parameters
    ----------
    base : MapBase
        The base map key.

    Returns
    -------
    list[Box]
        A square one pixel wider than each stop's ring.
    """
    return [
        square(at=stop, half=ridden_ring(mpp=base.mpp)[0] + 1)
        for stop in map_pixels(base=base, points=base.stops)
    ]


def draw_overlay(
    canvas: Canvas, *, view: MapView, keep_out: KeepOut = NO_KEEP_OUT,
) -> None:
    """
    Draw the markers, the empty text and the From stop over the map.

    The empty text sits below the stop, under the "Map not installed"
    note, and over the markers. The stop is drawn last so nothing buries
    it.

    Parameters
    ----------
    canvas : Canvas
        Target, with the base already drawn below the header.
    view : MapView
        View-model.
    keep_out : KeepOut
        Map-pixel boxes of the baked names and the corner boxes.
    """
    stop = stop_at(base=view.base)
    with canvas.clipped(box=(0, HEADER, view.base.width, HEIGHT)):
        draw_markers(
            canvas,
            markers=nearest_first(markers=view.markers, near=stop),
            mpp=view.base.mpp,
            origin=(0, HEADER),
            reserved=end_boxes(base=view.base),
            edges=edge_boxes(
                width=view.base.width, banner=view.banner is not None,
            ),
            stops=stop_boxes(base=view.base),
            keep_out=keep_out,
        )
        if view.empty:
            draw_empty(canvas, text=view.empty, width=view.base.width)
        if view.base.trip is not None:
            draw_trip_points(canvas, base=view.base)
        elif stop is not None:
            draw_stop(canvas, at=(stop[0], stop[1] + HEADER))


def row_time(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw when a sidebar departure leaves, or a block if it is cancelled.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        The badge's vertical centre.
    """
    if row.pill is Pill.CANCELLED:
        pill(
            canvas,
            box=(
                CANCELLED_LEFT,
                y - CANCELLED_HALF,
                SIDEBAR_RIGHT,
                y + CANCELLED_HALF,
            ),
            text=cancelled(row=row),
            px=CANCELLED_PX,
        )
        return
    canvas.text(
        at=(SIDEBAR_RIGHT, y + COUNTDOWN_DROP),
        text=leaves(row=row),
        face=COUNTDOWN_FACE,
        grey=INK,
        anchor='rs',
    )


def sidebar_row(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw one sidebar departure.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        The badge's vertical centre.
    """
    route_pair(
        canvas,
        x=SIDEBAR_TEXT,
        y=y,
        route=row.route,
        onto=row.onto,
        px=BADGE_PX,
    )
    row_time(canvas, row=row, y=y)
    canvas.text(
        at=(SIDEBAR_TEXT, y + ARRIVES_DROP),
        text=arrival_line(row=row),
        face=ARRIVES_FACE,
        grey=INK,
    )
    if row.pill is Pill.FASTER:
        pill(
            canvas,
            box=(
                FASTER_LEFT,
                y + FASTER_TOP,
                SIDEBAR_RIGHT,
                y + FASTER_BOTTOM,
            ),
            text=FASTER,
            px=FASTER_PX,
        )
    canvas.rect(
        box=(
            ROW_RULE_LEFT,
            y + ROW_RULE_DROP,
            ROW_RULE_RIGHT,
            y + ROW_RULE_DROP + 1,
        ),
        grey=INK,
    )


def draw_pairs(canvas: Canvas, *, view: MapView, drop: int) -> None:
    """
    Draw a connection's pairs in the sidebar, the first inverted.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : MapView
        View-model.
    drop : int
        Pixels the banner pushes the sidebar down.
    """
    canvas.text(
        at=(SIDEBAR_TEXT, PAIRS_HEAD_Y + drop),
        text=PAIRS_HEAD,
        face=DESTINATION_FACE,
        grey=INK,
    )
    draw_pair_list(
        canvas,
        rows=view.pairs,
        top=PAIRS_TOP + drop,
        left=SIDEBAR_LEFT + RULE_WIDTH,
        right=WIDTH,
        step=PAIR_STEP,
        selected=0,
    )


def draw_sidebar(canvas: Canvas, *, view: MapView) -> None:
    """
    Draw the rule, the destination and the next departures.

    With a banner showing, everything but the rule moves down below it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : MapView
        View-model.
    """
    canvas.rect(
        box=(SIDEBAR_LEFT, HEADER, SIDEBAR_LEFT + RULE_WIDTH, HEIGHT),
        grey=INK,
    )
    drop = BANNER if view.banner else 0
    if view.pairs:
        draw_pairs(canvas, view=view, drop=drop)
        return
    if view.destination is not None:
        canvas.text(
            at=(SIDEBAR_TEXT, DESTINATION_Y + drop),
            text=canvas.fit(
                text=f'to {view.destination}',
                face=DESTINATION_FACE,
                width=DESTINATION_WIDTH,
            ),
            face=DESTINATION_FACE,
            grey=INK,
        )
    for index, row in enumerate(view.sidebar):
        sidebar_row(canvas, row=row, y=ROWS_TOP + drop + index * ROW_STEP)


def snap(
    *,
    grey: NDArray[np.uint8],
    width: int = SIDEBAR_LEFT,
) -> NDArray[np.uint8]:
    """
    Keep four greys in the map and only ink and paper elsewhere.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        The drawn frame.
    width : int
        Map width in pixels.

    Returns
    -------
    NDArray[np.uint8]
        The finished frame.
    """
    out = np.where(grey < THRESHOLD, INK, PAPER).astype(np.uint8)
    out[HEADER:, :width] = quantise4(grey=grey[HEADER:, :width])
    return out


def draw_map_view(
    *,
    view: MapView,
    base: NDArray[np.uint8],
    keep_out: KeepOut = NO_KEEP_OUT,
) -> NDArray[np.uint8]:
    """
    Draw a Map view frame over a cached map base.

    Parameters
    ----------
    view : MapView
        View-model.
    base : NDArray[np.uint8]
        The map base, ``MAP_HEIGHT`` by ``view.base.width``; never changed.
    keep_out : KeepOut
        Map-pixel boxes of the base's baked names and corner boxes.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels: four greys in the map, ink and paper elsewhere.
    """
    canvas = Canvas()
    canvas.image(grey=base, at=(0, HEADER))
    draw_overlay(canvas, view=view, keep_out=keep_out)
    view_header(canvas, view=view, active='Map')
    draw_sidebar(canvas, view=view)
    view_banner(canvas, view=view)
    return snap(grey=canvas.grey(), width=view.base.width)
