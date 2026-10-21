"""Facts about the drawn Map view frame (no golden images)."""

import math
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import SqliteTileRepository
from paperroute.build.tile_store import TileWriter
from paperroute.core.geo import Point, to_metres
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
    MapBase,
    MapView,
    Marker,
    screen_point,
)
from paperroute.core.model import Mode
from paperroute.core.stop_view import Pill, Row, empty_text
from paperroute.core.tiles import CITY, TILE, TileVariant, tile_of, to_pixels
from paperroute.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
    text_width,
)
from paperroute.render.chrome import BANNER, HEADER, badge, header
from paperroute.render.map_base import draw_map_base
from paperroute.render.map_view import (
    BADGE_PX,
    COUNTDOWN_FACE,
    EMPTY_MARGIN,
    SIDEBAR_LEFT,
    SIDEBAR_RIGHT,
    SIDEBAR_TEXT,
    draw_map_view,
    empty_lines,
    snap,
)
from paperroute.render.tile_codec import GREYS, pack_bits, pack_grey

MPP = 8
CENTRE: Point = to_metres(lon=151.0036, lat=-33.8170)
ROW_TOPS = (110, 182, 254, 326, 398)
EMPTY = 'No more services today'
LONG_EMPTY = empty_text(
    destination='Sydney Olympic Park Wharf, Parramatta River', loaded=True
)


def row(
    *,
    route: str = '606',
    onto: str | None = None,
    pill: Pill | None = None,
    live: bool = True,
    countdown: str = '9',
) -> Row:
    """
    Make a sidebar row.

    Parameters
    ----------
    route : str
        Route label.
    onto : str | None
        Route the vehicle becomes before the Destination.
    pill : Pill | None
        Marker.
    live : bool
        True for a realtime row.
    countdown : str
        Countdown text.

    Returns
    -------
    Row
        A bus row leaving at 8:09.
    """
    return Row(
        route=route,
        mode=Mode.BUS,
        platform='',
        destination='Parramatta',
        countdown=countdown,
        leaves_at='8:09',
        arrives_at='8:45',
        ride_minutes=36,
        live=live,
        pill=pill,
        onto=onto,
    )


VIEW = MapView(
    origin='Hollis St Opp Constitution Hill Library',
    destination='Parramatta Station',
    clock='8:00',
    base=MapBase(centre=CENTRE, mpp=MPP, route=()),
    zoom=2,
    markers=(),
    sidebar=(row(),) * 5,
    banner=None,
    empty=None,
)


def paper() -> NDArray[np.uint8]:
    """
    Make a blank map base.

    Returns
    -------
    NDArray[np.uint8]
        Map-sized paper.
    """
    return np.full((MAP_HEIGHT, MAP_WIDTH), PAPER, dtype=np.uint8)


def live(*, at: tuple[float, float], route: str = '606') -> Marker:
    """
    Make a live marker without a wedge.

    Parameters
    ----------
    at : tuple[float, float]
        Map pixels.
    route : str
        Route label.

    Returns
    -------
    Marker
        A pinned live marker.
    """
    return Marker(
        route=route,
        x=at[0],
        y=at[1],
        bearing=None,
        live=True,
        pinned=True,
    )


def stop_pixel() -> tuple[int, int]:
    """
    Find the frame pixel under the From stop.

    Returns
    -------
    tuple[int, int]
        Column and row.
    """
    x, y = screen_point(centre=CENTRE, mpp=MPP, point=CENTRE)
    return math.floor(x), math.floor(y + HEADER)


def threshold(*, canvas: Canvas) -> NDArray[np.uint8]:
    """
    Snap a reference canvas to ink and paper.

    Parameters
    ----------
    canvas : Canvas
        The drawn reference.

    Returns
    -------
    NDArray[np.uint8]
        Its pixels as ink or paper.
    """
    return np.where(canvas.grey() < 128, INK, PAPER).astype(np.uint8)


def test_map_frame_is_800_by_480_with_four_levels() -> None:
    """The map keeps four greys; everything else is ink or paper."""
    base = np.repeat(
        np.array(GREYS, dtype=np.uint8),
        MAP_WIDTH // len(GREYS),
    )
    frame = draw_map_view(view=VIEW, base=np.tile(base, (MAP_HEIGHT, 1)))
    assert frame.shape == (HEIGHT, WIDTH)
    assert frame.dtype == np.uint8
    assert set(np.unique(frame[HEADER:, :SIDEBAR_LEFT])) == set(GREYS)
    assert set(np.unique(frame[:HEADER])) <= {INK, PAPER}
    assert set(np.unique(frame[:, SIDEBAR_LEFT:])) <= {INK, PAPER}


def test_header_marks_the_map_tab() -> None:
    """The frame's header is the shared header with Map active."""
    canvas = Canvas()
    header(
        canvas,
        origin=VIEW.origin,
        destination=VIEW.destination,
        clock=VIEW.clock,
        active='Map',
        tag=VIEW.tag,
    )
    expected = threshold(canvas=canvas)
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.array_equal(frame[:HEADER], expected[:HEADER])


def test_rule_divides_map_and_sidebar() -> None:
    """A 3 px ink rule runs down from the header at x 520 to 523."""
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.all(frame[HEADER:, SIDEBAR_LEFT:SIDEBAR_LEFT + 3] == INK)
    assert np.all(frame[HEADER:, SIDEBAR_LEFT + 3] == PAPER)


def test_sidebar_draws_each_row() -> None:
    """Each row has a badge, a countdown, an arrival and a rule."""
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.any(frame[56:74, 538:788] == INK)
    for top in ROW_TOPS:
        assert frame[top, 540] == INK
        assert np.any(frame[top - 14 : top + 13, 740:785] == INK)
        assert not np.any(frame[top - 14 : top + 13, 785:800] == INK)
        assert np.any(frame[top + 15 : top + 27, 538:600] == INK)
        assert np.all(frame[top + 40, 534:788] == INK)


def test_destination_reads_to_the_destination() -> None:
    """The sidebar opens with "to" and the destination, fitted."""
    canvas = Canvas()
    face = Face(weight=700, px=16)
    canvas.text(
        at=(538, 70),
        text=canvas.fit(text='to Parramatta Station', face=face, width=250),
        face=face,
        grey=INK,
    )
    expected = threshold(canvas=canvas)
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.array_equal(frame[HEADER:90, 524:], expected[HEADER:90, 524:])


def test_cancelled_row_shows_a_block() -> None:
    """A cancelled row has an ink block where the countdown goes."""
    view = replace(VIEW, sidebar=(row(pill=Pill.CANCELLED),))
    frame = draw_map_view(view=view, base=paper())
    top = ROW_TOPS[0]
    assert frame[top - 12 : top + 12, 640:784].mean() < 128
    assert frame[top - 12 : top + 12, 785:800].min() == PAPER


def test_faster_row_has_a_pill() -> None:
    """A faster row has an ink pill right of its arrival time."""
    view = replace(VIEW, sidebar=(row(pill=Pill.FASTER),))
    frame = draw_map_view(view=view, base=paper())
    top = ROW_TOPS[0]
    assert frame[top + 17 : top + 32, 712:784].mean() < 128
    plain = draw_map_view(view=VIEW, base=paper())
    assert plain[top + 17 : top + 32, 712:784].min() == PAPER


def test_scheduled_row_shows_its_clock_time() -> None:
    """A row that isn't live shows when it leaves, not a countdown."""
    scheduled = replace(VIEW, sidebar=(row(live=False),))
    clock = replace(VIEW, sidebar=(row(countdown='8:09'),))
    assert np.array_equal(
        draw_map_view(view=scheduled, base=paper()),
        draw_map_view(view=clock, base=paper()),
    )


def test_sidebar_moves_below_the_banner() -> None:
    """With a banner the sidebar's content starts below it."""
    plain = draw_map_view(view=VIEW, base=paper())
    view = replace(VIEW, banner='Showing timetable only')
    frame = draw_map_view(view=view, base=paper())
    below = slice(SIDEBAR_LEFT + 3, WIDTH)
    assert np.array_equal(
        frame[HEADER + BANNER :, below],
        plain[HEADER : HEIGHT - BANNER, below],
    )
    assert not np.any(plain[HEIGHT - BANNER :, below] == INK)


def test_sidebar_stops_after_its_rows() -> None:
    """With two rows the rest of the sidebar is paper."""
    view = replace(VIEW, sidebar=(row(),) * 2)
    frame = draw_map_view(view=view, base=paper())
    assert np.all(frame[ROW_TOPS[2] - 20 :, SIDEBAR_LEFT + 3 :] == PAPER)


def test_markers_stay_inside_the_map() -> None:
    """A badge at the map's right edge never inks the sidebar."""
    plain = draw_map_view(view=VIEW, base=paper())
    marked = draw_map_view(
        view=replace(VIEW, markers=(live(at=(515.0, 200.0)),)),
        base=paper(),
    )
    changed = np.argwhere(plain != marked)
    assert changed.size
    assert changed[:, 1].max() < SIDEBAR_LEFT


def test_overlay_is_clipped_to_the_base_width() -> None:
    """At 480 px a badge at x 500 is clipped; at 520 px it is drawn."""
    badge = (live(at=(500.0, 200.0)),)
    narrow = replace(VIEW, base=replace(VIEW.base, width=STRIP_MAP_WIDTH))
    base = paper()[:, :STRIP_MAP_WIDTH]
    plain = draw_map_view(view=narrow, base=base)
    marked = draw_map_view(view=replace(narrow, markers=badge), base=base)
    assert np.array_equal(
        plain[:, STRIP_MAP_WIDTH:],
        marked[:, STRIP_MAP_WIDTH:],
    )
    wide_plain = draw_map_view(view=VIEW, base=paper())
    wide = draw_map_view(view=replace(VIEW, markers=badge), base=paper())
    assert np.any(wide_plain[:, STRIP_MAP_WIDTH:] != wide[:, STRIP_MAP_WIDTH:])


def test_narrow_stop_is_drawn_at_the_narrow_centre() -> None:
    """The stop is over the 480 px map's middle, not the 520 px one's."""
    narrow = replace(VIEW, base=replace(VIEW.base, width=STRIP_MAP_WIDTH))
    frame = draw_map_view(view=narrow, base=paper()[:, :STRIP_MAP_WIDTH])
    middle = HEADER + MAP_HEIGHT // 2
    assert frame[middle, STRIP_MAP_WIDTH // 2] == INK
    assert frame[middle, MAP_WIDTH // 2 + 8] == PAPER


def test_narrow_empty_text_is_centred_and_wrapped_within_the_width() -> None:
    """On a 480 px map the empty text is centred at 240 and fits."""
    narrow = replace(
        VIEW,
        base=replace(VIEW.base, width=STRIP_MAP_WIDTH),
        empty=LONG_EMPTY,
    )
    frame = draw_map_view(view=narrow, base=paper()[:, :STRIP_MAP_WIDTH])
    middle = HEADER + MAP_HEIGHT // 2
    band = frame[middle + 40 : middle + 100, :STRIP_MAP_WIDTH]
    ink = np.flatnonzero(np.any(band == INK, axis=0))
    assert ink.size
    assert ink.min() >= EMPTY_MARGIN - 4
    assert ink.max() <= STRIP_MAP_WIDTH - EMPTY_MARGIN + 4
    assert abs((ink.max() + ink.min() + 1) / 2 - STRIP_MAP_WIDTH / 2) <= 12
    lines, face = empty_lines(text=LONG_EMPTY, width=STRIP_MAP_WIDTH)
    room = STRIP_MAP_WIDTH - 2 * EMPTY_MARGIN
    assert all(text_width(text=line, face=face) <= room for line in lines)


def test_snap_follows_the_map_width() -> None:
    """On a 480 px map, columns from 480 on stay ink or paper."""
    grey = np.full((HEIGHT, WIDTH), 100, dtype=np.uint8)
    out = snap(grey=grey, width=STRIP_MAP_WIDTH)
    assert np.all(out[HEADER:, STRIP_MAP_WIDTH:] == INK)
    assert np.all(out[HEADER:, :STRIP_MAP_WIDTH] == GREYS[1])


def test_banner_is_drawn_over_the_map() -> None:
    """The banner's paper box covers a dark map."""
    dark = np.full((MAP_HEIGHT, MAP_WIDTH), INK, dtype=np.uint8)
    view = replace(VIEW, banner='Showing timetable only')
    assert draw_map_view(view=VIEW, base=dark)[HEADER + 6, 400] == INK
    frame = draw_map_view(view=view, base=dark)
    assert frame[HEADER + 6, 400] == PAPER
    assert frame[HEADER + 6, SIDEBAR_LEFT + 1] == PAPER


def test_stop_marker_is_drawn_over_badges() -> None:
    """A badge stuck on the stop is covered by the stop marker again."""
    x, y = screen_point(centre=CENTRE, mpp=MPP, point=CENTRE)
    view = replace(VIEW, markers=(live(at=(x, y)),) * 40)
    frame = draw_map_view(view=view, base=paper())
    sx, sy = stop_pixel()
    assert frame[sy, sx] == INK
    assert frame[sy, sx + 6] == PAPER
    assert frame[sy, sx + 9] == INK
    assert frame[sy, sx - 9] == INK
    assert frame[sy, sx + 11] == PAPER
    assert frame[sy, sx - 11] == PAPER


def empty_band(*, frame: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """
    Cut out the rows below the stop where the empty text goes.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A Map frame.

    Returns
    -------
    NDArray[np.uint8]
        The map's rows from 40 to 70 px below its centre.
    """
    middle = HEADER + MAP_HEIGHT // 2
    return frame[middle + 40 : middle + 70, :SIDEBAR_LEFT]


def test_empty_text_sits_below_the_stop() -> None:
    """The empty text is clear of the stop marker, which stays whole."""
    frame = draw_map_view(view=replace(VIEW, empty=EMPTY), base=paper())
    assert np.any(empty_band(frame=frame) == INK)
    sx, sy = stop_pixel()
    assert np.all(frame[sy - 20 : sy + 20, 60 : sx - 16] == PAPER)
    assert np.all(frame[sy - 20 : sy + 20, sx + 16 : 460] == PAPER)
    assert frame[sy, sx] == INK
    assert frame[sy, sx + 6] == PAPER
    assert frame[sy, sx + 9] == INK


def test_empty_text_has_a_halo() -> None:
    """Over a dark map the empty text is ringed with paper."""
    dark = np.full((MAP_HEIGHT, MAP_WIDTH), INK, dtype=np.uint8)
    frame = draw_map_view(view=replace(VIEW, empty=EMPTY), base=dark)
    assert np.any(empty_band(frame=frame) == PAPER)


def test_no_location_says_so_without_a_stop() -> None:
    """With no centre the empty text shows and no stop is drawn."""
    view = replace(
        VIEW,
        base=MapBase(centre=None, mpp=MPP, route=()),
        empty='No location for this stop',
    )
    frame = draw_map_view(view=view, base=paper())
    assert np.any(empty_band(frame=frame) == INK)
    plain = draw_map_view(view=replace(view, empty=None), base=paper())
    assert np.all(plain[HEADER:, :SIDEBAR_LEFT] == PAPER)


def test_snap_thresholds_outside_the_map() -> None:
    """Grey outside the map becomes ink or paper; inside, a level."""
    out = snap(grey=np.full((HEIGHT, WIDTH), 100, dtype=np.uint8))
    assert np.all(out[:HEADER] == INK)
    assert np.all(out[:, SIDEBAR_LEFT:] == INK)
    assert np.all(out[HEADER:, :SIDEBAR_LEFT] == GREYS[1])


def test_markers_and_tiles_share_one_transform(tmp_path: Path) -> None:
    """A tile pixel and a dot marker at one metre point coincide."""
    point = (CENTRE[0] + 4000.0, CENTRE[1] - 3000.0)
    gx, gy = to_pixels(point=point, mpp=CITY)
    col, tile_row = tile_of(pixel=(gx, gy))
    dot = np.full((TILE, TILE), PAPER, dtype=np.uint8)
    dot[math.floor(gy) - tile_row * TILE, math.floor(gx) - col * TILE] = INK
    with TileWriter(out=tmp_path / 'tiles.db') as writer:
        writer.add_tile(
            zoom=CITY,
            col=col,
            row=tile_row,
            grey=pack_grey(grey=dot),
            bits=pack_bits(paper=dot >= 128),
        )
        writer.finish(meta={})
    city = MapBase(centre=CENTRE, mpp=CITY, route=())
    with closing(SqliteTileRepository(path=tmp_path / 'tiles.db')) as tiles:
        base = draw_map_base(base=city, tiles=tiles, variant=TileVariant.GREY)
    view = replace(VIEW, base=city, zoom=4)
    tiled = draw_map_view(view=view, base=base)
    sx, sy = screen_point(centre=CENTRE, mpp=CITY, point=point)
    ty, tx = math.floor(sy) + HEADER, math.floor(sx)
    assert tiled[ty, tx] == INK
    marked = draw_map_view(
        view=replace(view, markers=(live(at=(sx, sy)),)),
        base=paper(),
    )
    near = marked[ty - 10 : ty + 11, tx - 10 : tx + 11]
    centre = np.argwhere(near == INK).mean(axis=0) + (ty - 10, tx - 10)
    assert np.allclose(centre, (ty + 0.5, tx + 0.5), atol=1.0)


def test_base_is_not_mutated() -> None:
    """Drawing the frame leaves the cached base untouched."""
    base = np.tile(
        np.array(GREYS, dtype=np.uint8),
        (MAP_HEIGHT, MAP_WIDTH // len(GREYS)),
    )
    kept = base.copy()
    view = replace(
        VIEW,
        markers=(live(at=(100.0, 100.0)),),
        banner='Showing timetable only',
    )
    draw_map_view(view=view, base=base)
    assert np.array_equal(base, kept)


def test_long_empty_text_wraps_to_two_whole_lines() -> None:
    """A long empty text splits into two lines that fit, never cut."""
    lines, face = empty_lines(text=LONG_EMPTY)
    assert len(lines) == 2
    assert ' '.join(lines) == LONG_EMPTY
    assert all(
        text_width(text=line, face=face) <= MAP_WIDTH - 2 * EMPTY_MARGIN
        for line in lines
    )


def test_long_empty_text_draws_a_second_line_inside_the_map() -> None:
    """The second line is drawn below the first and inside the map."""
    middle = HEADER + MAP_HEIGHT // 2
    second = (slice(middle + 72, middle + 96), slice(0, SIDEBAR_LEFT))
    short = draw_map_view(view=replace(VIEW, empty=EMPTY), base=paper())
    long = draw_map_view(view=replace(VIEW, empty=LONG_EMPTY), base=paper())
    assert not np.any(short[second] == INK)
    assert np.any(long[second] == INK)
    ink = np.flatnonzero(np.any(long[HEADER:, :SIDEBAR_LEFT] == INK, axis=0))
    assert ink.min() >= EMPTY_MARGIN - 4
    assert ink.max() <= MAP_WIDTH - EMPTY_MARGIN + 4


def test_empty_text_is_drawn_over_markers() -> None:
    """A badge under the empty text does not hide it."""
    x, y = stop_pixel()
    bus = live(at=(x - 120.0, y - HEADER + 52.0))
    view = replace(VIEW, markers=(bus,))
    badge = draw_map_view(view=view, base=paper())
    both = draw_map_view(view=replace(view, empty=EMPTY), base=paper())
    under = badge == INK
    under[:HEADER] = False
    under[:, SIDEBAR_LEFT:] = False
    assert np.any(under)
    assert np.any(both[under] == PAPER)


def test_empty_text_too_long_for_two_lines_uses_the_smallest_face() -> None:
    """Text that never fits two lines is wrapped whole at the smallest."""
    text = ' '.join(['Parramatta'] * 30)
    lines, face = empty_lines(text=text)
    assert face == Face(weight=700, px=15)
    assert ' '.join(lines) == text


def test_map_sidebar_omits_the_destination_line_without_one() -> None:
    """With no destination the sidebar opens straight on its rows."""
    frame = draw_map_view(
        view=replace(VIEW, destination=None, sidebar=()), base=paper())
    band = frame[HEADER:90, SIDEBAR_LEFT + 3:]
    assert np.all(band == band[0, 0])
    assert np.any(
        draw_map_view(view=replace(VIEW, sidebar=()), base=paper())[
            HEADER:90, SIDEBAR_LEFT + 3:
        ]
        != band
    )


def test_sidebar_minute_countdown_clears_a_wide_badge() -> None:
    """'99 min' fits right of a five-letter route badge."""
    route = badge(Canvas(), x=SIDEBAR_TEXT, y=0, route='SH100', px=BADGE_PX)
    wide = text_width(text='99 min', face=COUNTDOWN_FACE)
    assert SIDEBAR_TEXT + route + 8 < SIDEBAR_RIGHT - wide


def test_carry_on_sidebar_row_stays_inside_and_clear_of_the_time() -> None:
    """Both badges fit the sidebar and end before the countdown starts."""
    top = ROW_TOPS[0]
    count = Canvas().length(text='12 min', face=Face(weight=800, px=34))
    start = int(784 - count)
    band = (slice(top - 8, top - 4), slice(538, start - 2))
    frames = [
        draw_map_view(
            view=replace(
                VIEW, sidebar=(row(onto=onto, countdown='12'),) * 5,
            ),
            base=paper(),
        )
        for onto in (None, 'T2')
    ]
    ends = [
        np.flatnonzero((frame[band] == INK).any(axis=0))[-1]
        for frame in frames
    ]
    assert ends[1] > ends[0] + 30
    assert ends[1] + 538 < start - 4
    assert not np.any(frames[1][top - 14 : top + 13, 785:] == INK)
