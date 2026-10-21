"""Facts about the drawn Strip and Strip + map frames (no golden images)."""

from dataclasses import replace
from datetime import timedelta

import numpy as np
from numpy.typing import NDArray

from paperroute.core.geo import Point, to_metres
from paperroute.core.map_view import (
    MAP_HEIGHT,
    STRIP_MAP_WIDTH,
    MapBase,
    MapView,
    Marker,
)
from paperroute.core.model import CarryOn
from paperroute.core.stop_view import Pill, PinTag
from paperroute.core.strip_badges import HALF_WIDE
from paperroute.core.strip_model import (
    HINTS,
    OPEN_HINT,
    PANE_TRACK_X,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    Band,
    StripMapView,
    StripPane,
    StripRow,
    StripView,
)
from paperroute.core.strip_view import build_strip_view
from paperroute.render.canvas import INK, PAPER, Canvas, Face
from paperroute.render.chrome import BANNER, HEADER, badge, route_pair
from paperroute.render.markers import badge_box
from paperroute.render.strip_view import (
    NAME_X,
    PANE_NAME_X,
    draw_strip_map_view,
    draw_strip_view,
    paint_strip_view,
)
from paperroute.render.strip_view import panel_row as paint_panel_row
from paperroute.render.tile_codec import GREYS
from tests.core.factories import START, east_inputs
from tests.render.factories import STRIP_PANEL as PANEL
from tests.render.factories import STRIP_ROWS as ROWS
from tests.render.factories import STRIP_VIEW as VIEW
from tests.render.factories import panel_row

CENTRE: Point = to_metres(lon=151.0036, lat=-33.8170)
PANEL_TOP = 128
PANEL_STEP = 70


def test_strip_frame_is_two_level() -> None:
    """Drawing makes only ink and paper, before any threshold."""
    live = Marker(route='T1', x=TRACK_X, y=150.0, bearing=180.0, live=True,
                  pinned=True)
    stale = replace(live, y=300.0, bearing=None, live=False, age=240.0)
    rows = tuple(
        replace(row, cursor=index == 6, tag='TO?' if index == 6 else row.tag)
        for index, row in enumerate(ROWS[1:], start=1)
    )
    view = replace(
        VIEW,
        picking='TO',
        rows=rows,
        band=Band(
            text='FROM Town Hall · 3 stops away', top=True, y=STRIP_TOP,
        ),
        markers=(live, stale),
        panel=replace(PANEL, rows=(panel_row(),) * 3, hint=HINTS['TO']),
        banner='Live data unavailable',
    )
    canvas = Canvas()
    paint_strip_view(canvas, view=view)
    drawn = canvas.grey()
    assert set(np.unique(drawn).tolist()) == {INK, PAPER}
    assert (draw_strip_view(view=view) == drawn).all()


def test_strip_frame_has_rule_and_header() -> None:
    """The header is ink and a 3 px rule splits the strip from the panel."""
    frame = draw_strip_view(view=VIEW)
    assert frame[2:38, :400].mean() < 80
    assert (frame[HEADER:, 440:443] == INK).all()
    assert (frame[HEADER + 2:, 443:447] == PAPER).all()


def test_strip_frame_draws_rows_on_the_track() -> None:
    """The track runs down x 30 between the rows."""
    frame = draw_strip_view(view=VIEW)
    between = int(STRIP_TOP + 4 * ROW_H)
    assert frame[between, TRACK_X] == INK


def test_panel_draws_carry_on_second_line() -> None:
    """A departure that runs on gets a second line under it."""
    then = CarryOn(route='T9', headsign='Hornsby')
    plain = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),)),
    ))
    carried = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(then=then),)),
    ))
    line = (slice(PANEL_TOP + 52, PANEL_TOP + 66), slice(660, 786))
    assert (plain[line] == PAPER).all()
    assert (carried[line] == INK).any()


def test_no_destination_panel_has_no_destination_line() -> None:
    """With no destination the line under the title stays empty."""
    view = replace(
        VIEW,
        destination=None,
        panel=replace(PANEL, destination=None, no_destination=True),
    )
    line = (slice(88, 106), slice(456, 786))
    assert (draw_strip_view(view=VIEW)[line] == INK).any()
    assert (draw_strip_view(view=view)[line] == PAPER).all()


def test_no_destination_panel_rows_show_badges() -> None:
    """A whole-stop panel row has an ink route badge after the time."""
    view = replace(
        VIEW,
        destination=None,
        panel=replace(PANEL, destination=None, no_destination=True),
    )
    frame = draw_strip_view(view=view)
    badge = frame[PANEL_TOP + 26:PANEL_TOP + 42, 556:590]
    assert badge.mean() < 128


def test_panel_rows_are_ruled_and_spaced() -> None:
    """Each departure starts under a rule, 70 px apart."""
    frame = draw_strip_view(view=VIEW)
    for index in range(4):
        y = PANEL_TOP + index * PANEL_STEP
        assert (frame[y - 2, 460:780] == INK).all()


def test_hint_box_replaces_the_fourth_departure() -> None:
    """The hint is an ink box at the panel's foot with paper text."""
    view = replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),) * 3, hint=HINTS['TO']),
    )
    box = draw_strip_view(view=view)[420:464, 456:786]
    assert box.mean() < 128
    assert (box == PAPER).any()


def test_open_hint_draws_in_the_hint_box() -> None:
    """The fold hint fills the same box with its own paper text."""
    def box(hint: str) -> NDArray[np.uint8]:
        view = replace(
            VIEW, panel=replace(PANEL, rows=(panel_row(),) * 3, hint=hint),
        )
        return draw_strip_view(view=view)[420:464, 456:786]
    opens = box(OPEN_HINT)
    assert opens.mean() < 128
    assert (opens == PAPER).any()
    assert (opens != box(HINTS['TO'])).any()


def test_panel_draws_empty_text() -> None:
    """With no departures, the panel says why."""
    empty = replace(VIEW, panel=replace(PANEL, rows=()))
    said = replace(
        VIEW, panel=replace(PANEL, rows=(), empty='No more services today'),
    )
    area = (slice(PANEL_TOP, PANEL_TOP + 60), slice(456, 786))
    assert (draw_strip_view(view=empty)[area] == PAPER).all()
    assert (draw_strip_view(view=said)[area] == INK).any()


def test_cancelled_panel_row_has_block() -> None:
    """A cancelled departure shows a block where the countdown goes."""
    view = replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(pill=Pill.CANCELLED),)),
    )
    block = draw_strip_view(view=view)[PANEL_TOP + 22:PANEL_TOP + 42, 700:780]
    assert block.mean() < 128


def test_far_departure_leaves_the_countdown_slot_empty() -> None:
    """A departure 100+ minutes away doesn't repeat its time as a count."""
    far = replace(panel_row(), countdown='9:51', leaves_at='9:51', far=True)
    view = replace(VIEW, panel=replace(PANEL, rows=(far,)))
    slot = draw_strip_view(view=view)[PANEL_TOP + 20:PANEL_TOP + 48, 700:786]
    assert (slot == PAPER).all()
    near = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),)),
    ))
    assert (near[PANEL_TOP + 20:PANEL_TOP + 48, 700:786] == INK).any()


def test_empty_strip_says_why() -> None:
    """With no strip, its column shows the reason and no track."""
    view = replace(VIEW, rows=(), empty='Loading the line…')
    frame = draw_strip_view(view=view)
    assert (frame[200:300, 40:400] == INK).any()
    assert (frame[HEADER:, TRACK_X - 2:TRACK_X + 2] == PAPER).all()


def test_banner_is_drawn_over_the_strip() -> None:
    """A live data banner sits under the header."""
    frame = draw_strip_view(view=replace(VIEW, banner='Live data unavailable'))
    assert (frame[HEADER:HEADER + 3, :] == INK).all()


def test_panel_moves_below_the_banner() -> None:
    """Under a banner the whole panel is drawn a banner's height lower."""
    plain = draw_strip_view(view=VIEW)
    bannered = draw_strip_view(view=replace(VIEW, banner='No live data'))
    panel = slice(447, 800)
    assert (
        bannered[HEADER + BANNER:, panel] == plain[HEADER:-BANNER, panel]
    ).all()


def test_band_shows_below_the_banner() -> None:
    """A top band in the first slot under the banner stays in view."""
    y = STRIP_TOP + ROW_H
    view = replace(
        VIEW,
        rows=(),
        band=Band(text='FROM Town Hall · 3 stops away', top=True, y=y),
        banner='No live data',
    )
    frame = draw_strip_view(view=view)
    box = frame[y + 4:y + ROW_H - 4, 12:400]
    assert box.mean() < 128
    assert (box[:, 40:] == PAPER).any()


def map_view() -> MapView:
    """
    Make the Strip + map's map.

    Returns
    -------
    MapView
        A 480 px wide map.
    """
    return MapView(
        origin='Town Hall', destination='Parramatta', clock='8:05',
        base=MapBase(centre=CENTRE, mpp=8, route=(), width=STRIP_MAP_WIDTH),
        zoom=2, markers=(), sidebar=(), banner=None, empty=None,
        tag=PinTag(text='pin 2/3', pinned=True))


def pane_rows() -> tuple[StripRow, ...]:
    """
    Make the pane's rows.

    Returns
    -------
    tuple[StripRow, ...]
        Twelve rows with FROM in the third.
    """
    return tuple(
        replace(row, name='A Very Long Station Name Indeed That Goes On')
        for row in ROWS
    )


def test_strip_map_frame_keeps_greys_only_in_map() -> None:
    """Four greys inside the map; ink and paper everywhere else."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), GREYS[2], dtype=np.uint8)
    marker = Marker(route='T1', x=PANE_TRACK_X, y=150.0, bearing=0.0,
                    live=True, pinned=True)
    view = StripMapView(
        map=map_view(), pane=StripPane(rows=pane_rows(), markers=(marker,)),
    )
    frame = draw_strip_map_view(view=view, base=base)
    assert frame.shape == (480, 800)
    assert GREYS[2] in frame[HEADER:, :STRIP_MAP_WIDTH]
    outside = np.concatenate(
        [frame[:HEADER].ravel(), frame[HEADER:, STRIP_MAP_WIDTH:].ravel()],
    )
    assert set(np.unique(outside).tolist()) <= {INK, PAPER}


def test_strip_map_frame_has_rule_and_pane() -> None:
    """A 3 px rule at 480, the pane's track at 500 and names inside."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    view = StripMapView(
        map=map_view(), pane=StripPane(rows=pane_rows(), markers=()),
    )
    frame = draw_strip_map_view(view=view, base=base)
    assert (frame[HEADER:, 480:483] == INK).all()
    assert frame[int(STRIP_TOP + 4 * ROW_H), PANE_TRACK_X] == INK
    names = frame[STRIP_TOP:, 518:760]
    assert (names == INK).any()
    assert (frame[HEADER:, 796:] == PAPER).all()


def test_strip_map_header_marks_strip_map_tab() -> None:
    """The header is drawn, with the Strip + map tab lit in paper."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    view = StripMapView(map=map_view(), pane=StripPane(rows=(), markers=()))
    frame = draw_strip_map_view(view=view, base=base)
    assert frame[2:38, :200].mean() < 80
    assert (frame[15:27, 660:720] == PAPER).mean() > 0.5


def test_strip_map_draws_the_banner() -> None:
    """A live data banner sits under the header across the map and pane."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    view = StripMapView(
        map=replace(map_view(), banner='Live data unavailable'),
        pane=StripPane(rows=(), markers=()),
    )
    frame = draw_strip_map_view(view=view, base=base)
    assert (frame[HEADER:HEADER + 3, :] == INK).all()


def test_hint_box_clears_three_panel_rows() -> None:
    """Three rows, the last running on, end above the hint box."""
    then = CarryOn(route='T9', headsign='Hornsby')
    view = replace(
        VIEW,
        panel=replace(
            PANEL, rows=(panel_row(then=then),) * 3, hint=HINTS['TO'],
        ),
    )
    frame = draw_strip_view(view=view)
    third = PANEL_TOP + 2 * PANEL_STEP
    assert (frame[third + 52:third + 66, 556:786] == INK).any()
    assert (frame[third + 70:420, 452:790] == PAPER).all()
    assert (frame[420:423, 456:786] == INK).all()


def test_line_badges_on_a_track_clear_its_names() -> None:
    """On the strip and the pane, a badge on a track ends before names."""
    for track_x, name_x in ((TRACK_X, NAME_X), (PANE_TRACK_X, PANE_NAME_X)):
        for route in ('HUN', 'BMT', 'SCO', 'CCN', '+10', '610X', 'SH100'):
            right = badge_box(route=route, centre=(track_x, 0))[2]
            assert right < track_x + HALF_WIDE <= name_x, (route, track_x)


def at_the_from(*, stale: bool) -> StripView:
    """
    Build the example strip with one train standing at its From, C.

    Parameters
    ----------
    stale : bool
        True for a vehicle last heard ten minutes ago.

    Returns
    -------
    StripView
        The view one minute after the train's 8:00 departure.
    """
    now = START + timedelta(minutes=1)
    return build_strip_view(inputs=east_inputs(
        at=(0.0, 0.0),
        reported=now - timedelta(minutes=10 if stale else 0),
        now=now,
    ))


def test_trains_at_the_from_sit_on_its_disc_and_leave_its_name_whole(
) -> None:
    """Live and stale trains at C cover its disc and leave its name."""
    for stale in (False, True):
        view = at_the_from(stale=stale)
        home = next(row for row in view.rows if row.bold)
        (marker,) = view.markers
        assert (marker.live, marker.age is not None) == (not stale, stale)
        drawn = draw_strip_view(view=view)
        bare = draw_strip_view(view=replace(view, markers=()))
        y = int(home.y)
        disc = (slice(y - 12, y + 13), slice(TRACK_X - 12, TRACK_X + 13))
        assert not np.array_equal(drawn[disc], bare[disc])
        assert home.name_after is not None
        name = (slice(y - 9, y + 10), slice(int(home.name_after), 300))
        assert np.array_equal(drawn[name], bare[name])
        assert (bare[name] == INK).any()


MIDDLE = slice(556, 786)
FIRST_LINE = slice(PANEL_TOP + 24, PANEL_TOP + 48)
SECOND_LINE = slice(PANEL_TOP + 50, PANEL_TOP + 68)


def drawn_row(
    *, destination: str = 'Emu Plains', then: CarryOn | None = None,
    whole: bool = False,
) -> NDArray[np.uint8]:
    """
    Paint one panel row and read it back.

    Parameters
    ----------
    destination : str
        The row's destination.
    then : CarryOn | None
        Where the vehicle runs on to.
    whole : bool
        True for a whole-stop row.

    Returns
    -------
    NDArray[np.uint8]
        The grey frame.
    """
    canvas = Canvas()
    row = replace(panel_row(then=then), destination=destination)
    paint_panel_row(canvas, row=row, top=PANEL_TOP, whole=whole)
    return canvas.grey()


def test_destination_row_has_badge_and_destination() -> None:
    """With a destination the middle shows the badge and the name."""
    row = drawn_row()
    bare = drawn_row(destination='')
    badge_part = (FIRST_LINE, slice(556, 590))
    assert (row[badge_part] == bare[badge_part]).all()
    assert (row[badge_part] == INK).any()
    assert (row[FIRST_LINE, 600:700] == INK).any()
    assert (bare[FIRST_LINE, 600:700] == PAPER).all()


def test_destination_row_arrival_on_second_line() -> None:
    """The arrival sits on the second line, not in the first."""
    row = drawn_row()
    assert (row[SECOND_LINE, MIDDLE] == INK).any()
    whole = drawn_row(whole=True)
    assert (whole[SECOND_LINE, MIDDLE] == PAPER).all()
    assert (row[FIRST_LINE, 556:680] == whole[FIRST_LINE, 556:680]).all()


def test_destination_row_carry_on_after_arrival() -> None:
    """A carry-on extends the second line's ink to the right."""
    then = CarryOn(route='T9', headsign='Hornsby')

    def right_edge(frame: NDArray[np.uint8]) -> int:
        ink = (frame[SECOND_LINE, MIDDLE] == INK).any(0)
        return int(np.nonzero(ink)[0].max())

    assert right_edge(drawn_row(then=then)) > right_edge(drawn_row()) + 10


def test_whole_stop_row_unchanged() -> None:
    """A whole-stop row is the badge and headsign, with no arrival."""
    canvas = Canvas()
    width = badge(canvas, x=556, y=PANEL_TOP + 34, route='T1', px=14)
    face = Face(weight=400, px=14)
    canvas.text(at=(556 + width + 8, PANEL_TOP + 40), text='Emu Plains',
                face=face, grey=INK)
    expected = canvas.grey()[FIRST_LINE, 556:680]
    row = drawn_row(whole=True)
    assert (row[FIRST_LINE, 556:680] == expected).all()
    assert (row[SECOND_LINE, MIDDLE] == PAPER).all()


def test_strip_panel_row_draws_both_badges() -> None:
    """A carry-on panel row's destination starts after the pair."""
    rows = slice(PANEL_TOP + 30, PANEL_TOP + 43)
    pair_width = route_pair(
        Canvas(), x=556, y=0, route='T1', onto='T2', px=14,
    )
    plain_width = badge(Canvas(), x=556, y=0, route='T1', px=14)
    frames = [
        draw_strip_view(view=replace(
            VIEW, panel=replace(PANEL, rows=(panel_row(onto=onto),)),
        ))
        for onto in (None, 'T2')
    ]
    edge = int(556 + pair_width)
    gap = np.s_[rows, edge + 1:edge + 8]
    assert (frames[1][gap] == PAPER).all()
    plain_edge = int(556 + plain_width)
    assert (frames[0][rows, plain_edge + 8:plain_edge + 12] == INK).any()
    assert (frames[1][rows, edge + 8:edge + 80] == INK).any()


def test_long_route_pair_leaves_the_countdown_clear() -> None:
    """A BMT to CCN pair and a long destination stop before the time."""
    long = replace(
        panel_row(onto='CCN'), route='BMT', destination='Somewhere ' * 9,
    )
    frame = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(long,)),
    ))
    count = Canvas().length(
        text='6 min', face=Face(weight=700, px=22),
    )
    rows = slice(PANEL_TOP + 30, PANEL_TOP + 43)
    assert (frame[rows, 701:int(784 - count) - 2] == PAPER).all()


def test_panel_arrival_with_stops_leaves_the_countdown_clear() -> None:
    """'arr 8:47 · 12 stops' is drawn whole and ends before the time."""
    frames = [
        draw_strip_view(view=replace(
            VIEW,
            panel=replace(
                PANEL, rows=(replace(panel_row(), stops=stops),),
            ),
        ))
        for stops in (None, 12)
    ]
    count = Canvas().length(text='99 min', face=Face(weight=700, px=22))
    band = (slice(PANEL_TOP + 48, PANEL_TOP + 64), slice(556, 784))
    ends = [
        np.flatnonzero((frame[band] == INK).any(axis=0))[-1]
        for frame in frames
    ]
    assert ends[1] > ends[0] + 40
    assert ends[1] + 556 < 784 - count - 4


def test_panel_heading_has_no_station_count() -> None:
    """Nothing is drawn between the Destination line and the first row."""
    frame = draw_strip_view(view=VIEW)
    assert (frame[112:PANEL_TOP - 4, 460:784] == PAPER).all()


def test_fullest_panels_end_on_screen_and_above_the_hint() -> None:
    """Five rows, or four under a banner or above the hint, all fit."""
    full = [
        draw_strip_view(view=replace(
            VIEW,
            banner=banner,
            panel=replace(PANEL, rows=(panel_row(),) * rows, hint=hint),
        ))
        for banner, rows, hint in (
            (None, 5, None),
            ('No live data yet', 4, None),
            (None, 4, 'Press: set TO'),
            ('No live data yet', 3, 'Press: set TO'),
        )
    ]
    for frame in full:
        assert (frame[PANEL_TOP:476, 456:786] == INK).any()
        assert (frame[476:480, 456:786] == PAPER).all()
    for frame in full[2:]:
        assert (frame[412:420, 456:786] == PAPER).all()


def test_the_picked_panel_row_is_inverted_in_ink_and_paper() -> None:
    """The picked row is paper on ink; the frame stays two-level."""
    canvas = Canvas()
    paint_strip_view(canvas, view=VIEW)
    plain = canvas.grey()
    canvas = Canvas()
    paint_strip_view(
        canvas, view=replace(VIEW, panel=replace(PANEL, selected=1)),
    )
    chosen = canvas.grey()
    assert set(np.unique(chosen).tolist()) == {INK, PAPER}
    top = PANEL_TOP + PANEL_STEP
    box = (slice(top, top + PANEL_STEP - 4), slice(456, 786))
    assert np.array_equal(chosen[box], PAPER - plain[box])
    others = np.ones(chosen.shape, dtype=bool)
    others[box] = False
    assert np.array_equal(chosen[others], plain[others])
