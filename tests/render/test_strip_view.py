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
from paperroute.core.model import CarryOn, Mode
from paperroute.core.reach import RowKind
from paperroute.core.stop_view import Pill, PinTag, Row
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
    StripPanel,
    StripRow,
    StripView,
)
from paperroute.core.strip_view import build_strip_view
from paperroute.render.canvas import INK, PAPER, Canvas
from paperroute.render.chrome import BANNER, HEADER
from paperroute.render.markers import badge_box
from paperroute.render.strip_view import (
    NAME_X,
    PANE_NAME_X,
    draw_strip_map_view,
    draw_strip_view,
    paint_strip_view,
)
from paperroute.render.tile_codec import GREYS
from tests.core.factories import START, east_inputs

CENTRE: Point = to_metres(lon=151.0036, lat=-33.8170)
PANEL_TOP = 160
PANEL_STEP = 70


def panel_row(*, then: CarryOn | None = None,
              pill: Pill | None = None) -> Row:
    """
    Make a panel departure.

    Parameters
    ----------
    then : CarryOn | None
        Where the vehicle runs on to.
    pill : Pill | None
        Marker.

    Returns
    -------
    Row
        The departure.
    """
    return Row(
        route='T1', mode=Mode.TRAIN, platform='3', destination='Emu Plains',
        countdown='6', leaves_at='8:11', arrives_at='8:47', ride_minutes=36,
        live=True, pill=pill, then=then)


def strip_row(*, index: int, tag: str = '', parent: bool = True,
              cursor: bool = False) -> StripRow:
    """
    Make a main-line row in a slot.

    Parameters
    ----------
    index : int
        Slot from the top.
    tag : str
        Tag text.
    parent : bool
        Join it to the slot above.
    cursor : bool
        The row being picked.

    Returns
    -------
    StripRow
        The row.
    """
    y = STRIP_TOP + index * ROW_H + ROW_H / 2
    return StripRow(
        name=f'Station {index}', depth=0, kind=RowKind.MAIN, y=y,
        parent_y=y - ROW_H if parent else None, parent_depth=0, tag=tag,
        time='' if tag else f'8:{10 + index}', ride=False, cursor=cursor,
        bold=index == 2, station=f'S{index}')


ROWS = tuple(
    strip_row(index=index, parent=index > 0)
    for index in range(12)
)
PANEL = StripPanel(
    title='Town Hall', destination='Parramatta', note='201 stations from here',
    rows=(panel_row(),) * 4, no_destination=False, hint=None)
VIEW = StripView(
    origin='Town Hall', destination='Parramatta', clock='8:05',
    tag=PinTag(text='pin 2/3', pinned=True), picking=None, rows=ROWS,
    band=None, markers=(), panel=PANEL, empty=None, banner=None)


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
    plain = draw_strip_view(view=VIEW)
    carried = draw_strip_view(view=replace(
        VIEW, panel=replace(PANEL, rows=(panel_row(then=then),)),
    ))
    line = (slice(PANEL_TOP + 52, PANEL_TOP + 66), slice(460, 786))
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


def test_trains_at_the_from_leave_its_disc_and_name_whole() -> None:
    """Live and stale trains at C draw on its row without touching either."""
    for stale in (False, True):
        view = at_the_from(stale=stale)
        home = next(row for row in view.rows if row.bold)
        (marker,) = view.markers
        assert (marker.live, marker.age is not None) == (not stale, stale)
        drawn = draw_strip_view(view=view)
        bare = draw_strip_view(view=replace(view, markers=()))
        y = int(home.y)
        disc = (slice(y - 12, y + 13), slice(TRACK_X - 12, TRACK_X + 13))
        assert np.array_equal(drawn[disc], bare[disc])
        assert home.name_after is not None
        name = (slice(y - 9, y + 10), slice(int(home.name_after), 300))
        assert np.array_equal(drawn[name], bare[name])
        assert (bare[name] == INK).any()
