"""Tests for strip view: strip map."""

from dataclasses import replace
from datetime import timedelta

import numpy as np

from paperroute_device.render.canvas import INK, PAPER
from paperroute_device.render.frame import HEADER
from paperroute_testkit.core.factories import START, east_inputs
from paperroute_testkit.render.factories import STRIP_PANEL as PANEL
from paperroute_testkit.render.factories import STRIP_ROWS as ROWS
from paperroute_testkit.render.factories import STRIP_VIEW as VIEW
from paperroute_testkit.render.factories import panel_row
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    STRIP_MAP_WIDTH,
    Marker,
)
from paperroute_transit.core.model import CarryOn
from paperroute_transit.core.strip_badges import HALF_WIDE
from paperroute_transit.core.strip_model import (
    HINTS,
    PANE_TRACK_X,
    ROW_H,
    STRIP_TOP,
    TRACK_X,
    StripMapView,
    StripPane,
    StripRow,
    StripView,
)
from paperroute_transit.core.strip_view import build_strip_view
from paperroute_transit.render.markers import badge_box
from paperroute_transit.render.strip_view import (
    NAME_X,
    PANE_NAME_X,
    draw_strip_map_view,
    draw_strip_view,
)
from paperroute_transit.render.tile_codec import GREYS
from tests.render.strip_view.helpers import PANEL_STEP, PANEL_TOP, map_view


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
