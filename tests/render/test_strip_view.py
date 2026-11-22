"""Facts about the drawn Strip and Strip + map frames (no golden images)."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.core.text import DOT, HINT_DOT
from paperroute_device.render.canvas import (
    INK,
    PAPER,
    Canvas,
    Face,
    text_width,
)
from paperroute_device.render.frame import HEADER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_testkit.core.factories import START, east_inputs
from paperroute_testkit.render.factories import STRIP_PANEL as PANEL
from paperroute_testkit.render.factories import STRIP_ROWS as ROWS
from paperroute_testkit.render.factories import STRIP_VIEW as VIEW
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.geo import Point, to_metres
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    STRIP_MAP_WIDTH,
    MapBase,
    MapView,
    Marker,
)
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.stop_view import (
    UNCERTAIN,
    Estimate,
    Pill,
    PinTag,
    Row,
)
from paperroute_transit.core.strip_badges import HALF_WIDE
from paperroute_transit.core.strip_model import (
    HINTS,
    OPEN_HINT,
    PANE_LIST_ROWS,
    PANE_TOP,
    PANE_TRACK_X,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    Band,
    StripMapView,
    StripPane,
    StripPanel,
    StripRow,
    StripView,
)
from paperroute_transit.core.strip_view import build_strip_view
from paperroute_transit.render.chrome import (
    ARROW_WIDTH,
    BANNER,
    NAME_GAP,
    NOTE_GAP,
    badge,
    badge_width,
    note_room,
    route_pair,
)
from paperroute_transit.render.late_marks import mark_room
from paperroute_transit.render.markers import badge_box
from paperroute_transit.render.names import Naming, fit_headsign
from paperroute_transit.render.stop_view import TAG_FACE, WIDEST_COUNT
from paperroute_transit.render.strip_view import (
    BADGE_PX,
    CANCELLED_LEFT,
    CARRY_GAP,
    COUNTDOWN_FACE,
    FASTER_LEFT,
    HEADSIGN_FACE,
    HINT_BOX,
    HINT_FACE,
    HINT_PAD,
    MARK_FACE,
    MIDDLE_X,
    NAME_X,
    PANE_NAME_X,
    RULE_RIGHT,
    SECOND_DROP,
    SHORT_RULE,
    SHORT_RULE_DROP,
    SHORT_TOP,
    TEXT_LEFT,
    TEXT_RIGHT,
    TIME_DROP,
    draw_short_list,
    draw_strip_map_view,
    draw_strip_view,
    name_room,
    note_width,
    paint_strip_view,
    panel_heading,
)
from paperroute_transit.render.strip_view import panel_row as paint_panel_row
from paperroute_transit.render.strip_view import short_rows, short_text
from paperroute_transit.render.tile_codec import GREYS
from tests.render.test_chrome import Recording

CENTRE: Point = to_metres(
    lon=151.0036,
    lat=-33.8170,
    projection=SYDNEY_PROJECTION,
)
PANEL_TOP = 128
NAMING: Final[Naming] = Naming(rules=NameRules())
LONG_STOP: Final[str] = (
    'Macquarie University Station, Herring Road Interchange'
)
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


def test_timetable_departure_leaves_the_countdown_slot_empty() -> None:
    """A departure without live data doesn't repeat its time as a count."""
    timetabled = replace(panel_row(), live=False)
    view = replace(VIEW, panel=replace(PANEL, rows=(timetabled,)))
    slot = draw_strip_view(view=view)[PANEL_TOP + 20:PANEL_TOP + 48, 700:786]
    assert (slot == PAPER).all()
    near = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(),)),
    ))
    assert (near[PANEL_TOP + 20:PANEL_TOP + 48, 700:786] == INK).any()


def test_live_departure_counts_down_past_100() -> None:
    """A live departure 130 minutes away still fills the slot."""
    later = replace(panel_row(), countdown='130')
    view = replace(VIEW, panel=replace(PANEL, rows=(later,)))
    slot = draw_strip_view(view=view)[PANEL_TOP + 20:PANEL_TOP + 48, 690:786]
    assert (slot == INK).any()


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
        base=MapBase(
            centre=CENTRE,
            mpp=8,
            route=(),
            width=STRIP_MAP_WIDTH,
            projection=SYDNEY_PROJECTION,
        ),
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
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=whole, naming=NAMING,
    )
    return canvas.grey()


def test_destination_row_has_badge_and_destination() -> None:
    """With a destination the middle shows the badge and the name."""
    row = drawn_row()
    bare = drawn_row(destination='')
    badge_part = (FIRST_LINE, slice(556, 590))
    assert (row[badge_part] == bare[badge_part]).all()
    assert (row[badge_part] == INK).any()
    assert (row[FIRST_LINE, 610:700] == INK).any()
    assert (bare[FIRST_LINE, 610:700] == PAPER).all()


def test_destination_row_arrival_on_second_line() -> None:
    """The arrival sits on the second line, not in the first."""
    row = drawn_row()
    assert (row[SECOND_LINE, MIDDLE] == INK).any()
    whole = drawn_row(whole=True)
    assert (whole[SECOND_LINE, MIDDLE] == row[SECOND_LINE, MIDDLE]).all()
    assert (row[FIRST_LINE, 556:680] == whole[FIRST_LINE, 556:680]).all()


def test_destination_row_carry_on_after_arrival() -> None:
    """A carry-on extends the second line's ink to the right."""
    then = CarryOn(route='T9', headsign='Hornsby')

    def right_edge(frame: NDArray[np.uint8]) -> int:
        ink = (frame[SECOND_LINE, MIDDLE] == INK).any(0)
        return int(np.nonzero(ink)[0].max())

    assert right_edge(drawn_row(then=then)) > right_edge(drawn_row()) + 10


def test_whole_stop_row_adds_the_arrival() -> None:
    """A whole-stop row is the badge and headsign, with the arrival."""
    canvas = Canvas()
    width = badge(canvas, x=556, y=PANEL_TOP + 34, route='T1', px=14)
    face = Face(weight=400, px=14)
    canvas.text(at=(556 + width + 8, PANEL_TOP + 40), text='Emu Plains',
                face=face, grey=INK)
    expected = canvas.grey()[FIRST_LINE, 556:680]
    row = drawn_row(whole=True)
    assert (row[FIRST_LINE, 556:680] == expected).all()
    assert (
        row[SECOND_LINE, MIDDLE] == drawn_row()[SECOND_LINE, MIDDLE]
    ).all()


WHOLE = replace(PANEL, destination=None, no_destination=True)


def panel_frame(
    *, panel: StripPanel, row: Row,
) -> NDArray[np.uint8]:
    """
    Draw the Strip with one row in its panel.

    Parameters
    ----------
    panel : StripPanel
        The panel the row goes in.
    row : Row
        The departure.

    Returns
    -------
    NDArray[np.uint8]
        The frame.
    """
    return draw_strip_view(
        view=replace(VIEW, panel=replace(panel, rows=(row,))),
    )


def central(*, onto: str | None, then: CarryOn | None = None) -> Row:
    """
    Make a T1 departure to Central.

    Parameters
    ----------
    onto : str | None
        Route the train becomes.
    then : CarryOn | None
        Where the train runs on to.

    Returns
    -------
    Row
        The departure.
    """
    return replace(
        panel_row(onto=onto, then=then), destination='Central',
    )


def test_continuing_row_drops_the_onto_tag_beside_an_arrival() -> None:
    """With a To, a continuing row draws as if it did not continue."""
    area = np.s_[PANEL_TOP - 2:PANEL_TOP + 68, 456:786]
    tagged = panel_frame(panel=PANEL, row=central(onto='T8'))
    plain = panel_frame(panel=PANEL, row=central(onto=None))
    assert (tagged[area] == plain[area]).all()


def test_whole_board_continuing_row_puts_the_destination_second() -> None:
    """On the whole-stop board the pair is alone on line 1."""
    frame = panel_frame(panel=WHOLE, row=central(onto='T8'))
    assert (frame[SECOND_LINE, 556:600] == INK).any()
    pair_end = 556 + route_pair(
        Canvas(), x=0, y=0, route='T1', onto='T8', px=14,
    )
    assert (frame[FIRST_LINE, int(pair_end) + 4:700] == PAPER).all()
    plain = panel_frame(panel=WHOLE, row=central(onto=None))
    expected = panel_frame(panel=PANEL, row=central(onto=None))
    assert (
        plain[SECOND_LINE, MIDDLE] == expected[SECOND_LINE, MIDDLE]
    ).all()


def test_whole_board_carry_on_follows_the_destination() -> None:
    """A carry-on starts CARRY_GAP after the destination on line 2."""
    then = CarryOn(route='T9', headsign='Hornsby')
    frame = panel_frame(panel=WHOLE, row=central(onto='T8', then=then))
    width = text_width(
        text=f'Central{DOT}arr 8:47', face=Face(weight=400, px=14),
    )
    x = 556 + round(width) + CARRY_GAP
    assert (frame[SECOND_LINE, x:x + int(ARROW_WIDTH)] == INK).any()
    assert (frame[SECOND_LINE, x - 6:x - 1] == PAPER).all()


def test_whole_board_carry_on_after_long_destination_fits() -> None:
    """A long line-2 destination leaves room for the carry-on's label."""
    then = CarryOn(route='T9', headsign='Hornsby')
    row = replace(
        central(onto='T8', then=then),
        destination='Macarthur Airport Sydenham Hornsby Parramatta',
    )
    frame = panel_frame(panel=WHOLE, row=row)
    assert (frame[SECOND_LINE, TEXT_RIGHT + 1:] == PAPER).all()
    assert (frame[SECOND_LINE, TEXT_RIGHT - 36:TEXT_RIGHT] == INK).any()


def test_whole_board_panel_row_draws_both_badges() -> None:
    """On the whole-stop board, a continuing row's pair stands alone."""
    rows = slice(PANEL_TOP + 30, PANEL_TOP + 43)
    pair_width = route_pair(
        Canvas(), x=556, y=0, route='T1', onto='T2', px=14,
    )
    plain_width = badge(Canvas(), x=556, y=0, route='T1', px=14)
    plain, pair = (
        panel_frame(panel=WHOLE, row=panel_row(onto=onto))
        for onto in (None, 'T2')
    )
    edge = int(556 + pair_width)
    assert (pair[rows, edge + 1:edge + 8] == PAPER).all()
    plain_edge = int(556 + plain_width)
    assert (plain[rows, plain_edge + 8:plain_edge + 12] == INK).any()
    count = text_width(text='6 min', face=Face(weight=700, px=22))
    end = min(edge + 80, int(784 - count) - 2)
    assert (pair[rows, edge + 8:end] == PAPER).all()


def assert_pair_clears_the_count(*, row: Row, count_text: str) -> None:
    """
    Check a whole-stop row's first line ends 8 px before its count.

    Parameters
    ----------
    row : Row
        The departure.
    count_text : str
        The countdown the row shows.
    """
    frame = panel_frame(panel=WHOLE, row=row)
    count = text_width(text=count_text, face=Face(weight=700, px=22))
    band = frame[PANEL_TOP + 22:PANEL_TOP + 46, 556:int(784 - count) - 2]
    ink = np.flatnonzero((band == INK).any(axis=0))
    assert ink.size
    assert 556 + ink.max() <= 784 - count - 8


def test_long_route_pair_leaves_the_countdown_clear() -> None:
    """A BMT to CCN pair stops before the time."""
    long = replace(
        panel_row(onto='CCN'), route='BMT', destination='Somewhere ' * 9,
    )
    assert_pair_clears_the_count(row=long, count_text='6 min')


def test_strip_widest_count_clears_a_long_pair() -> None:
    """A BMT to CCN pair stops before a '100 min' count."""
    long = replace(
        panel_row(onto='CCN'),
        route='BMT',
        destination='Somewhere ' * 9,
        countdown='100',
    )
    assert_pair_clears_the_count(row=long, count_text=WIDEST_COUNT)


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
            (None, 4, HINTS['TO']),
            ('No live data yet', 3, HINTS['TO']),
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


def test_strip_hints_fit_the_pill() -> None:
    """Every Strip hint fits its pill without a cut."""
    room = HINT_BOX[2] - HINT_BOX[0] - 2 * HINT_PAD
    for hint in (*HINTS.values(), OPEN_HINT):
        assert text_width(text=hint, face=HINT_FACE) <= room, hint


def test_hint_separator_is_heavier() -> None:
    """In hint type the hint bullet inks at least twice the middle dot."""
    inks = []
    for mark in (HINT_DOT.strip(), DOT.strip()):
        canvas = Canvas()
        canvas.text(at=(100, 100), text=mark, face=HINT_FACE, grey=INK)
        inks.append(int((canvas.grey() == INK).sum()))
    bullet, dot = inks
    assert bullet >= 2 * dot > 0


UNSURE: Final[Estimate] = Estimate(due='8:05', late=1, uncertain=True)
LATE: Final[Estimate] = Estimate(due='8:07', late=4, uncertain=False)


def painted(*, row: Row) -> NDArray[np.uint8]:
    """
    Paint one panel row on the destination board and read it back.

    Parameters
    ----------
    row : Row
        The departure.

    Returns
    -------
    NDArray[np.uint8]
        The grey frame.
    """
    canvas = Canvas()
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=False, naming=NAMING,
    )
    return canvas.grey()


def test_uncertain_panel_row_says_so_under_its_countdown() -> None:
    """'uncertain' sits right-aligned under the countdown."""
    row = replace(panel_row(), destination='Emu Plains')
    found = np.argwhere(
        painted(row=row) != painted(row=replace(row, estimate=UNSURE)),
    )
    assert found.size
    assert found[:, 0].min() > PANEL_TOP + TIME_DROP
    assert found[:, 1].min() >= TEXT_RIGHT - note_room(face=TAG_FACE)
    assert found[:, 1].max() <= TEXT_RIGHT


def test_uncertain_carry_on_gives_way_to_the_note() -> None:
    """A long carry-on stops short of 'uncertain' by the gap."""
    then = CarryOn(route='T2', headsign='Leppington via Granville Junction')
    row = replace(
        panel_row(then=then), destination='Emu Plains', estimate=UNSURE,
    )
    start = TEXT_RIGHT - int(note_room(face=TAG_FACE))
    gap = painted(row=row)[SECOND_LINE, start : start + NOTE_GAP - 1]
    assert (gap == PAPER).all()


FASTER_BOX: Final[tuple[slice, slice]] = (
    slice(PANEL_TOP + 51, PANEL_TOP + 66), slice(712, RULE_RIGHT),
)


def test_note_width_adds_the_word_to_faster() -> None:
    """A FASTER row's second line loses the block, a gap and the word."""
    row = replace(panel_row(pill=Pill.FASTER), estimate=UNSURE)
    assert note_width(row=row) == pytest.approx(
        RULE_RIGHT - FASTER_LEFT + NOTE_GAP + note_room(face=TAG_FACE),
    )


def test_row_note_sits_left_of_faster() -> None:
    """'uncertain' ends a gap before the FASTER block, which is unchanged."""
    row = replace(
        panel_row(pill=Pill.FASTER), destination='Emu Plains',
        estimate=UNSURE,
    )
    drawn = painted(row=row)
    plain = painted(row=replace(row, estimate=None))
    found = np.argwhere(drawn != plain)
    assert found.size
    assert found[:, 1].max() <= FASTER_LEFT - NOTE_GAP
    assert found[:, 1].min() >= FASTER_LEFT - NOTE_GAP - note_room(
        face=TAG_FACE,
    )
    assert (drawn[FASTER_BOX] == plain[FASTER_BOX]).all()


def test_row_middle_stops_a_gap_before_uncertain() -> None:
    """An arrival with stops, or a carry-on, ends a gap before the word."""
    then = CarryOn(route='T2', headsign='Leppington via Granville Junction')
    word = int(
        FASTER_LEFT - NOTE_GAP - text_width(text=UNCERTAIN, face=TAG_FACE),
    )
    for row in (
        replace(panel_row(pill=Pill.FASTER), estimate=UNSURE, stops=12),
        replace(panel_row(pill=Pill.FASTER, then=then), estimate=UNSURE),
    ):
        line = painted(row=row)[SECOND_LINE]
        assert (line[:, word - NOTE_GAP + 1:word - 1] == PAPER).all()
        assert (line[:, word - 30:word - NOTE_GAP] == INK).any()


def test_a_carry_on_stops_short_of_faster() -> None:
    """A long carry-on leaves the FASTER box clear by the gap."""
    then = CarryOn(route='T2', headsign='Leppington via Granville Junction')
    row = replace(
        panel_row(then=then, pill=Pill.FASTER), destination='Emu Plains',
    )
    gap = painted(row=row)[SECOND_LINE, 712 - NOTE_GAP + 1 : 712]
    assert (gap == PAPER).all()


LONGEST = (
    'Our Lady Of Mercy College Via Old Windsor Rd And Every Other Street'
)


def test_name_room_ends_at_the_countdowns_left_edge() -> None:
    """Room runs to the count less the gap, to the block, or to the edge."""
    count = text_width(text='6 min', face=COUNTDOWN_FACE)
    assert name_room(row=panel_row(), left=600.0) == (
        TEXT_RIGHT - count - NAME_GAP - 600
    )
    timetabled = replace(panel_row(), live=False)
    assert name_room(row=timetabled, left=600.0) == TEXT_RIGHT - 600
    cancelled = panel_row(pill=Pill.CANCELLED)
    assert name_room(row=cancelled, left=600.0) == (
        CANCELLED_LEFT - NAME_GAP - 600
    )


def test_a_long_name_takes_the_room_a_timetable_row_leaves() -> None:
    """With no countdown a name runs on past the old limit."""
    row = replace(
        panel_row(), live=False, destination='London Liverpool Street',
    )
    frame = panel_frame(panel=WHOLE, row=row)
    assert (frame[PANEL_TOP + 28:PANEL_TOP + 44, 702:784] == INK).any()
    band = (slice(PANEL_TOP + 45, PANEL_TOP + 68), slice(556, 784))
    expected = panel_frame(panel=PANEL, row=row)
    assert (frame[band] == expected[band]).all()


def test_a_name_too_long_for_the_room_wraps_beside_a_centred_badge() -> None:
    """Two lines sit beside a badge 4 px higher than a one-line badge."""
    row = replace(panel_row(), destination='London Liverpool Street')
    two = panel_frame(panel=PANEL, row=row)
    one = panel_frame(panel=PANEL, row=panel_row())
    assert (two[PANEL_TOP + 22:PANEL_TOP + 38, 557] == INK).all()
    assert (two[PANEL_TOP + 41:PANEL_TOP + 44, 557] == PAPER).all()
    assert (one[PANEL_TOP + 26:PANEL_TOP + 43, 557] == INK).all()
    assert (two[SECOND_LINE, MIDDLE] == one[SECOND_LINE, MIDDLE]).all()


def test_a_two_line_name_ends_before_the_countdown() -> None:
    """Both lines stop a gap short of the count's left edge."""
    row = replace(panel_row(), destination=LONGEST)
    frame = panel_frame(panel=PANEL, row=row)
    count = text_width(text='6 min', face=COUNTDOWN_FACE)
    edge = int(TEXT_RIGHT - count)
    band = frame[PANEL_TOP + 14:PANEL_TOP + 46, edge - NAME_GAP + 2:edge - 1]
    assert (band == PAPER).all()


def test_a_name_still_too_long_never_takes_a_third_line() -> None:
    """Past two lines the name is cut, and the pill slot stays clear."""
    row = replace(panel_row(), destination=LONGEST)
    whole = panel_frame(panel=WHOLE, row=row)
    frame = panel_frame(panel=PANEL, row=row)
    band = (slice(PANEL_TOP + 48, PANEL_TOP + 68), slice(556, 784))
    assert (whole[band] == frame[band]).all()
    slot = frame[PANEL_TOP + 47:PANEL_TOP + 68, RULE_RIGHT - 74:RULE_RIGHT]
    assert (slot == PAPER).all()


def test_panel_heading_wraps_a_title_without_a_destination() -> None:
    """Without a destination the title takes both lines."""
    canvas = Recording()
    panel_heading(
        canvas, panel=replace(PANEL, title=LONG_STOP, destination=None),
        drop=0, rules=NameRules(),
    )
    first, second = canvas.drawn[-2:]
    assert f'{first} {second}' == LONG_STOP


def test_panel_heading_keeps_one_title_line_beside_a_destination() -> None:
    """With a destination the title is one line."""
    canvas = Recording()
    panel_heading(
        canvas, panel=replace(PANEL, title=LONG_STOP, destination='Wx'),
        drop=0, rules=NameRules(),
    )
    assert canvas.drawn[-1] == 'Wx'
    assert canvas.drawn[-2] != LONG_STOP


def test_panel_row_fits_its_headsign_by_the_rules() -> None:
    """A panel row draws the lines the headsign fitter gives."""
    row = replace(panel_row(), destination='Heathrow Airport Terminal 5')
    canvas = Recording()
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=False, naming=NAMING,
    )
    left = MIDDLE_X + badge_width(px=BADGE_PX) + 8
    expected = fit_headsign(
        text=row.destination, face=HEADSIGN_FACE,
        width=name_room(row=row, left=left), naming=NAMING, two_lines=True,
    )
    assert all(line in canvas.drawn for line in expected)


def test_panel_row_marks_lateness_after_the_countdown() -> None:
    """A panel row four minutes late reads +4."""
    canvas = Recording()
    paint_panel_row(
        canvas, row=replace(panel_row(), estimate=LATE), top=PANEL_TOP,
        whole=False, naming=NAMING,
    )
    assert '+4' in canvas.drawn


def test_name_room_leaves_room_for_the_mark() -> None:
    """The name ends the mark's room further left."""
    marked = replace(panel_row(), estimate=LATE)
    count = text_width(text='6 min', face=COUNTDOWN_FACE)
    assert name_room(row=marked, left=600.0) == pytest.approx(
        TEXT_RIGHT - count - mark_room(row=marked, face=MARK_FACE)
        - NAME_GAP - 600,
    )


def test_panel_row_shows_the_arrival_on_the_whole_board() -> None:
    """A whole-stop row reads its arrival on the second line."""
    canvas = Recording()
    paint_panel_row(
        canvas, row=panel_row(), top=PANEL_TOP, whole=True, naming=NAMING,
    )
    assert 'arr 8:47' in canvas.drawn


def test_continuing_middle_adds_the_arrival_when_it_fits() -> None:
    """A continuing whole-stop row reads 'Central · arr 8:47'."""
    canvas = Recording()
    paint_panel_row(
        canvas, row=central(onto='T8'), top=PANEL_TOP, whole=True,
        naming=NAMING,
    )
    assert f'Central{DOT}arr 8:47' in canvas.drawn


def test_continuing_middle_drops_the_arrival_for_a_long_name() -> None:
    """A long destination beside a carry-on keeps no arrival."""
    row = replace(
        central(onto='T8', then=CarryOn(route='T9', headsign='Hornsby')),
        destination='Macarthur via Airport and Sydenham',
    )
    canvas = Recording()
    paint_panel_row(
        canvas, row=row, top=PANEL_TOP, whole=True, naming=NAMING,
    )
    assert not any('arr ' in text for text in canvas.drawn)


def test_leave_note_sits_under_the_leave_time() -> None:
    """'platform 4' is drawn left of the middle, on the second line."""
    row = replace(panel_row(), note='platform 4')
    found = np.argwhere(painted(row=row) != painted(row=panel_row()))
    assert found.size
    assert found[:, 0].min() >= PANEL_TOP + TIME_DROP + 4
    assert found[:, 0].max() <= PANEL_TOP + SECOND_DROP + 4
    assert found[:, 1].min() >= TEXT_LEFT
    assert found[:, 1].max() < MIDDLE_X - NAME_GAP


def test_panel_row_shows_the_platform_beside_uncertain() -> None:
    """The platform and 'uncertain' each keep their own place."""
    canvas = Recording()
    paint_panel_row(
        canvas,
        row=replace(panel_row(), note='platform 4', estimate=UNSURE),
        top=PANEL_TOP, whole=False, naming=NAMING,
    )
    assert {'platform 4', UNCERTAIN} <= set(canvas.drawn)


PANE_LEFT: Final[int] = STRIP_MAP_WIDTH + 3


def short_view(
    *,
    rows: tuple[Row, ...],
    destination: str | None = 'Parramatta',
    banner: str | None = None,
) -> StripMapView:
    """Make a Strip + map with these departures and an empty pane."""
    return StripMapView(
        map=replace(
            map_view(), destination=destination, sidebar=rows, banner=banner,
        ),
        pane=StripPane(rows=(), markers=()),
    )


def short_texts(view: StripMapView) -> list[str]:
    """Draw a short list and list its texts."""
    canvas = Recording()
    draw_short_list(canvas, view=view)
    return canvas.drawn


def test_short_rows_skip_cancelled_and_keep_three() -> None:
    """Cancelled rows are left out, and at most three are kept."""
    rows = tuple(
        replace(panel_row(), countdown=str(minutes)) for minutes in range(5)
    )
    gone = replace(rows[1], pill=Pill.CANCELLED)
    assert short_rows(rows=(rows[0], gone, *rows[2:])) == (
        rows[0], rows[2], rows[3],
    )
    assert short_rows(rows=rows[:2]) == rows[:2]


def test_draw_short_list_reads_headsigns_without_a_destination() -> None:
    """Three rows, each its headsign and countdown."""
    drawn = short_texts(short_view(rows=(panel_row(),) * 5, destination=None))
    assert drawn.count('Emu Plains') == 3
    assert drawn.count('6 min') == 3


def test_short_text_leads_with_the_arrival_beside_a_destination() -> None:
    """With a destination the arrival comes first."""
    drawn = short_texts(short_view(rows=(panel_row(),)))
    assert f'arr 8:47{DOT}Emu Plains' in drawn


def test_short_text_with_no_headsign_is_the_arrival() -> None:
    """No headsign leaves the arrival alone, with no dot."""
    assert short_text(
        row=replace(panel_row(), destination=''), width=200.0,
        naming=NAMING, destination=True,
    ) == 'arr 8:47'


def test_draw_short_list_marks_lateness() -> None:
    """A row four minutes late reads +4."""
    assert '+4' in short_texts(
        short_view(rows=(replace(panel_row(), estimate=LATE),)),
    )


def test_short_text_fits_the_headsign_by_the_rules() -> None:
    """A long headsign loses its via tail on the one line."""
    long = 'Penrith via Parramatta Blacktown Mount Druitt and St Marys'
    drawn = short_texts(short_view(
        rows=(replace(panel_row(), destination=long),), destination=None,
    ))
    assert 'Penrith' in drawn


def test_draw_strip_map_view_puts_the_list_above_a_rule() -> None:
    """The rows sit above a 2 px rule; below it the empty pane is paper."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    frame = draw_strip_map_view(
        view=short_view(rows=(panel_row(),) * 3), base=base,
    )
    rule = SHORT_TOP + PANE_LIST_ROWS * ROW_H + SHORT_RULE_DROP
    assert (frame[rule:rule + SHORT_RULE, PANE_LEFT:] == INK).all()
    assert (frame[HEADER:rule, PANE_LEFT:] == INK).any()
    assert (frame[rule + SHORT_RULE:, PANE_LEFT + 1:] == PAPER).all()


def test_draw_short_list_moves_below_the_banner() -> None:
    """Under a banner the list and its rule drop one slot."""
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    frame = draw_strip_map_view(
        view=short_view(rows=(panel_row(),), banner='No live data'),
        base=base,
    )
    rule = SHORT_TOP + (PANE_LIST_ROWS + 1) * ROW_H + SHORT_RULE_DROP
    assert (frame[rule:rule + SHORT_RULE, PANE_LEFT:] == INK).all()


def test_draw_short_list_draws_nothing_without_departures() -> None:
    """No rows, or only cancelled ones: no text and no rule."""
    for rows in ((), (panel_row(pill=Pill.CANCELLED),)):
        canvas = Recording()
        draw_short_list(canvas, view=short_view(rows=rows))
        assert canvas.drawn == []
        assert (canvas.grey() == PAPER).all()


def test_draw_pane_keeps_the_track_out_of_the_list() -> None:
    """A first row's line up to a parent off the top stops at the pane."""
    first = replace(
        ROWS[1], y=PANE_TOP + ROW_H / 2, parent_y=PANE_TOP - ROW_H / 2,
    )
    base = np.full((MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8)
    frame = draw_strip_map_view(
        view=StripMapView(
            map=map_view(), pane=StripPane(rows=(first,), markers=()),
        ),
        base=base,
    )
    assert (frame[HEADER:PANE_TOP - 2, PANE_LEFT + 1:] == PAPER).all()
    assert (frame[PANE_TOP:PANE_TOP + ROW_H, PANE_LEFT:] == INK).any()
