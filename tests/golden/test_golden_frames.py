"""
Every kind of screen draws today's pixels.

Skia draws different pixels on each platform, so each keeps its own
baselines. ``GOLDEN_UPDATE=1`` redraws this platform's; on a Mac,
``scripts/golden-linux`` redraws the Linux ones in a container.
"""

import hashlib
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, get_args

import numpy as np
import pytest
import skia
from numpy.typing import NDArray

from paperroute_device.render.canvas import encode_png
from paperroute_hardware.render.panel_bits import grey_planes, oriented
from paperroute_transit.core.chooser import Level
from paperroute_transit.core.chooser_view import ChooserView
from paperroute_transit.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute_transit.core.connection_view import ConnectionView
from paperroute_transit.core.gauge_ends import RouteGauge
from paperroute_transit.core.gauge_line import MarkKind
from paperroute_transit.core.kerb_menu import MENU_HINT
from paperroute_transit.core.map_view import MapView, Marker
from paperroute_transit.core.messages import ScreenView
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.stop_under import UnderNumber
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.core.strip_model import (
    PANE_ROWS,
    STRIP_ROWS,
    StripMapView,
    StripRow,
    StripView,
)
from paperroute_transit.render import gauge_route
from paperroute_transit.render.gauge_plan import (
    GaugePlan,
    keep_out,
    mark_lines,
    slot_x,
)
from paperroute_transit.render.markers import STRIP_WEDGE, badge_box
from paperroute_transit.render.names import Naming
from tests.golden.scenes import (
    GOLDEN,
    SCENES,
    UPDATING,
    baseline_bytes,
    frame_of,
    view_of,
)

FRAMES: Final[Path] = GOLDEN / 'frames' / sys.platform
PLANES: Final[Path] = FRAMES / 'planes.json'
PLANE_SCENES: Final[tuple[str, ...]] = ('stop', 'map')
# CI runs Linux, so its baselines must exist.
PLATFORMS: Final[frozenset[str]] = frozenset({'darwin', 'linux'})


def decoded(*, data: bytes) -> NDArray[np.uint8]:
    """
    Read an 8-bit grey PNG.

    Parameters
    ----------
    data : bytes
        PNG file contents.

    Returns
    -------
    NDArray[np.uint8]
        Height by width grey levels.
    """
    image = skia.Image.MakeFromEncoded(data)
    pixels: NDArray[np.uint8] = image.toarray(
        colorType=skia.ColorType.kGray_8_ColorType,
    )
    return pixels


def mismatch(
    *, actual: NDArray[np.uint8], expected: NDArray[np.uint8], out: Path,
) -> str:
    """
    Save a frame that differs from its baseline, and the changed pixels.

    Parameters
    ----------
    actual : NDArray[np.uint8]
        The frame drawn today.
    expected : NDArray[np.uint8]
        The baseline.
    out : Path
        Directory for `actual.png` and `diff.png`.

    Returns
    -------
    str
        Where the files are and how many pixels changed; the two shapes
        when they differ.
    """
    if actual.shape != expected.shape:
        return f'shape {actual.shape} is not {expected.shape}'
    changed = actual != expected
    diff = np.where(changed, 0, 255).astype(np.uint8)
    (out / 'actual.png').write_bytes(encode_png(grey=actual))
    (out / 'diff.png').write_bytes(encode_png(grey=diff))
    return f'{int(changed.sum())} pixels changed; see {out}'


def plane_hash(*, frame: NDArray[np.uint8], turned: bool) -> str:
    """
    Hash the bit planes the panel is sent for a frame.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame, right way up.
    turned : bool
        Whether the panel is mounted upside down.

    Returns
    -------
    str
        The sha256 of every plane's bytes, in order.
    """
    digest = hashlib.sha256()
    for plane in grey_planes(frame=oriented(frame=frame, turned=turned)):
        digest.update(plane)
    return digest.hexdigest()


@pytest.fixture(name='_frames')
def _frames_fixture() -> None:
    """Skip on a platform with no baseline frames."""
    if not UPDATING and not FRAMES.exists():
        pytest.skip(f'no golden frames for {sys.platform}')


def test_scenes_cover_every_screen_view() -> None:
    """Some scene shows each kind of screen."""
    shown = {type(view_of(state=scene())) for scene in SCENES.values()}
    assert shown == set(get_args(ScreenView))


def test_strip_scenes_carry_pattern_data() -> None:
    """The Strip scenes draw train marks and real leg stops."""
    strip = view_of(state=SCENES['strip']())
    strip_map = view_of(state=SCENES['strip_map']())
    assert isinstance(strip, StripView) and strip.markers
    assert isinstance(strip_map, StripMapView) and strip_map.pane.markers
    legs = view_of(state=SCENES['connection_strip']())
    legs_map = view_of(state=SCENES['connection_strip_map']())
    assert isinstance(legs, ConnectionStripView)
    assert isinstance(legs_map, ConnectionStripMapView)
    for section in (*legs.sections, *legs_map.pane.sections):
        assert section.stops and section.empty is None


def test_train_strip_scenes_draw_the_new_row_kinds() -> None:
    """The train scenes show earlier, dashed, passed and exit rows."""
    for name in ('train_strip', 'train_strip_ends'):
        view = view_of(state=SCENES[name]())
        assert isinstance(view, StripView)
        kinds = {row.kind for row in view.rows}
        assert kinds >= {
            RowKind.EARLIER, RowKind.DASHED, RowKind.PASSED, RowKind.EXIT,
        }
        assert any(len(row.names) > 3 for row in view.rows)
    ends = view_of(state=SCENES['train_strip_ends']())
    assert isinstance(ends, StripView)
    assert [row.kind for row in ends.rows].count(RowKind.DASHED) == 2


def test_map_panes_scene_has_panes() -> None:
    """The two-change connection Map draws side panes."""
    view = view_of(state=SCENES['map_panes']())
    assert isinstance(view, MapView) and view.panes


def test_map_destination_scene_letters_the_ends() -> None:
    """The Destination scene draws A and B discs and its ride solid."""
    view = view_of(state=SCENES['map_destination']())
    assert isinstance(view, MapView)
    assert view.base.trip is not None and view.base.trip.lettered
    assert view.base.trip.end is not None and view.base.lines


def test_every_platform_has_every_baseline() -> None:
    """Each platform's baselines hold every scene's frame and the planes."""
    folders = {path.name: path for path in (GOLDEN / 'frames').iterdir()}
    assert set(folders) == PLATFORMS
    wanted = {f'{name}.png' for name in SCENES} | {'planes.json'}
    for folder in folders.values():
        assert {path.name for path in folder.iterdir()} == wanted, folder


def test_png_round_trip_is_exact() -> None:
    """Every grey survives encoding and decoding."""
    frame = np.tile(np.array([0, 85, 170, 255], np.uint8), (480, 200))
    assert np.array_equal(decoded(data=encode_png(grey=frame)), frame)


@pytest.mark.usefixtures('_frames')
@pytest.mark.parametrize('name', list(SCENES))
def test_frame_matches_baseline(name: str, tmp_path: Path) -> None:
    """The scene draws exactly its baseline frame."""
    actual = frame_of(state=SCENES[name]())
    path = FRAMES / f'{name}.png'
    expected = decoded(
        data=baseline_bytes(path=path, data=encode_png(grey=actual)),
    )
    assert np.array_equal(actual, expected), mismatch(
        actual=actual, expected=expected, out=tmp_path,
    )


def test_frame_is_repeatable() -> None:
    """Drawing a scene twice gives the same pixels."""
    for name, scene in SCENES.items():
        first, second = frame_of(state=scene()), frame_of(state=scene())
        assert np.array_equal(first, second), name


@pytest.mark.usefixtures('_frames')
def test_panel_planes_match_baseline() -> None:
    """The bit planes sent to the panel hash as they did, either way up."""
    hashes = {
        f'{name}/{"turned" if turned else "upright"}': plane_hash(
            frame=frame_of(state=SCENES[name]()), turned=turned,
        )
        for name in PLANE_SCENES
        for turned in (False, True)
    }
    text = json.dumps(hashes, indent=2, sort_keys=True) + '\n'
    stored = baseline_bytes(path=PLANES, data=text.encode())
    assert json.loads(stored) == hashes


def test_cambridge_trains_show_timetable_platforms() -> None:
    """Cambridge trains show badge and platform under the no-feed banner."""
    view = view_of(state=SCENES['stop_cambridge_trains']())
    assert isinstance(view, StopView)
    assert (view.origin, view.destination) == ('Cambridge', 'Ely')
    assert view.banner == 'Timetable times · no live feed for this service'
    assert view.hero is not None
    assert (
        view.hero.route,
        view.hero.vehicle,
        view.hero.where,
        view.hero.arrives_at,
    ) == ('XC', 'train', 'platform 4', '7:22')
    assert [(row.route, row.where) for row in view.rows] == [
        ('GN', 'platform 4'),
        ('GA', 'platform 5'),
        ('GN', 'platform 1'),
    ]
    assert [row.note for row in view.rows] == [
        'platform 4', 'platform 5', 'platform 1',
    ]


def test_a_pin_with_no_destination_shows_what_is_known_under_the_number(
) -> None:
    """The open Stop view says status, platform, place and the gauge."""
    view = view_of(state=SCENES['stop_no_destination']())
    assert isinstance(view, StopView)
    assert view.under == UnderNumber(
        status='2 min late',
        platform='stand B',
        then=('8:06', '8:09'),
        place='1 stop away',
    )
    assert view.delay == 120
    assert view.gauge is not None
    assert [bus.trip_id for bus in view.gauge.buses] == ['trip-0803']
    assert [row.leaves_at for row in view.rows] == ['8:06', '8:09']


def test_a_destination_pin_draws_its_gauge() -> None:
    """A long ride to the Destination fills the gauge, bus unseen."""
    view = view_of(state=SCENES['stop_gauge']())
    assert isinstance(view, StopView)
    gauge = view.gauge
    assert gauge is not None and gauge.end is not None
    assert gauge.pattern.stops[gauge.end].name == 'Pitt St At Barlow St'
    assert (gauge.buses, gauge.words) == ((), '')


def test_a_busy_route_puts_every_bus_on_the_gauge() -> None:
    """Twenty buses both ways reach the gauge, three inside its break."""
    view = view_of(state=SCENES['stop_gauge_busy']())
    assert isinstance(view, StopView)
    gauge = view.gauge
    assert gauge is not None and gauge.end is not None
    assert len(gauge.buses) == 20
    assert {bus.right for bus in gauge.buses} == {True, False}
    assert sum(7 < bus.spot < gauge.end for bus in gauge.buses) == 3


def drawn_gauge(
    *, scene: str, monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Marker], list[tuple[str, ...]], list[GaugePlan]]:
    """
    Draw a scene's frame, keeping the gauge's badges, name lines and plan.

    Parameters
    ----------
    scene : str
        Scene name.
    monkeypatch : pytest.MonkeyPatch
        Patches the drawers to record what they draw.

    Returns
    -------
    tuple[list[Marker], list[tuple[str, ...]], list[GaugePlan]]
        The badges drawn, each mark's name lines and the plan.
    """
    badges: list[Marker] = []
    lines: list[tuple[str, ...]] = []
    plans: list[GaugePlan] = []
    draw_badge = gauge_route.draw_badge
    route_plan = gauge_route.route_plan

    def kept_badge(
        canvas: skia.Canvas, *, badge: gauge_route.GaugeBadge,
    ) -> None:
        """Record a badge and draw it."""
        badges.append(badge.marker)
        draw_badge(canvas, badge=badge)

    def kept_lines(**inputs: object) -> tuple[tuple[str, ...], ...]:
        """Record the name lines and return them."""
        found = mark_lines(**inputs)  # type: ignore[arg-type]
        lines.extend(found)
        return found

    def kept_plan(**inputs: object) -> GaugePlan:
        """Record the plan and return it."""
        plan = route_plan(**inputs)  # type: ignore[arg-type]
        plans.append(plan)
        return plan

    monkeypatch.setattr(gauge_route, 'route_plan', kept_plan)
    monkeypatch.setattr(gauge_route, 'draw_badge', kept_badge)
    monkeypatch.setattr(gauge_route, 'mark_lines', kept_lines)
    frame_of(state=SCENES[scene]())
    return badges, lines, plans


def test_the_busy_break_counts_the_three_buses_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count inside the ahead break is the scene's three buses."""
    badges, _, plans = drawn_gauge(
        scene='stop_gauge_busy', monkeypatch=monkeypatch,
    )
    plan = plans[0]
    gap = plan.line.ahead
    assert gap is not None
    held = [
        badge.route
        for badge in badges
        if slot_x(plan=plan, slot=gap.first)
        < badge.x
        < slot_x(plan=plan, slot=gap.last)
    ]
    assert held == ['+3']
    assert sum(gauge_route.buses_in(marker=badge) for badge in badges) == 20


def test_the_busy_near_from_count_points_both_ways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count nearest the From mixes directions and points both ways."""
    badges: list[gauge_route.GaugeBadge] = []
    draw_badge = gauge_route.draw_badge

    def kept_badge(
        canvas: skia.Canvas, *, badge: gauge_route.GaugeBadge,
    ) -> None:
        """Record a badge and draw it."""
        badges.append(badge)
        draw_badge(canvas, badge=badge)

    monkeypatch.setattr(gauge_route, 'draw_badge', kept_badge)
    ink = frame_of(state=SCENES['stop_gauge_busy']()) < 128
    plan = gauge_route.route_plan(
        gauge=view_gauge(scene='stop_gauge_busy'),
        keep=keep_out(ink=np.zeros((480, 800), dtype=bool)),
        naming=Naming(rules=NameRules()),
    )
    origin = next(
        slot_x(plan=plan, slot=mark.slot)
        for mark in plan.line.marks
        if mark.kind is MarkKind.FROM
    )
    count = min(badges, key=lambda badge: abs(badge.marker.x - origin))
    assert count.mixed
    marker = count.marker
    y = round(marker.y)
    left, _, right, _ = badge_box(route=marker.route, centre=(marker.x, y))
    tip = STRIP_WEDGE.tip
    for apex in (math.floor(left - tip + 3), math.ceil(right + tip - 3)):
        assert ink[y, apex]


def test_the_gauge_names_its_destination_in_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end name is drawn whole, not shortened."""
    _, lines, _ = drawn_gauge(scene='stop_gauge', monkeypatch=monkeypatch)
    assert ' '.join(lines[-1]) == 'Pitt St At Barlow St'


def view_gauge(*, scene: str) -> RouteGauge:
    """
    Give a Stop scene's gauge.

    Parameters
    ----------
    scene : str
        Scene name.

    Returns
    -------
    RouteGauge
        The gauge of the scene's Stop view.
    """
    view = view_of(state=SCENES[scene]())
    assert isinstance(view, StopView) and view.gauge is not None
    return view.gauge


def test_a_gauge_with_no_bus_behind_starts_at_the_first_stop() -> None:
    """With no bus behind, the line starts at the trip's first stop."""
    plan = gauge_route.route_plan(
        gauge=view_gauge(scene='stop_gauge'),
        keep=keep_out(ink=np.zeros((480, 800), dtype=bool)),
        naming=Naming(rules=NameRules()),
    )
    assert plan.line.starts and not plan.line.cut
    assert plan.line.marks[0].name == 'Johnston St'


def test_both_sides_scenes_build_their_views() -> None:
    """The both-sides scenes show time order and the next bus each way."""
    time_order = view_of(state=SCENES['stop_both_sides']())
    each = view_of(state=SCENES['stop_each_way']())
    assert isinstance(time_order, StopView) and isinstance(each, StopView)
    assert time_order.origin == 'King St At Missenden Rd, both sides'
    assert time_order.each_way is None and time_order.hero is not None
    assert time_order.hero.countdown == '2'
    assert each.each_way is not None and each.each_way.here.hero is not None
    assert each.each_way.here.hero.destination == 'Leichhardt'
    assert each.each_way.across.end == 'Newtown'


def badge_rows(
    *, rows: Sequence[StripRow], markers: Sequence[Marker],
) -> list[tuple[str, float | None]]:
    """
    Name the row each badge sits on, with its wedge's bearing.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Visible rows.
    markers : Sequence[Marker]
        Their badges, each level with a row.

    Returns
    -------
    list[tuple[str, float | None]]
        Each badge's row name and bearing.
    """
    at = {row.y: row.name for row in rows}
    return [(at[marker.y], marker.bearing) for marker in markers]


def test_both_sides_live_scenes_build_their_views() -> None:
    """The Map joins the kerbs; both strips put the From in the middle."""
    shown = view_of(state=SCENES['map_both_sides']())
    assert isinstance(shown, MapView) and shown.base.trip is not None
    assert shown.base.trip.across is not None
    assert len(shown.base.lines) == 2
    assert all(line.solid is not None for line in shown.base.lines)
    strip = view_of(state=SCENES['strip_both_sides']())
    assert isinstance(strip, StripView)
    rows = STRIP_ROWS - (strip.banner is not None)
    names = [row.name for row in strip.rows]
    assert names.index('King St At Missenden Rd') == (rows - 1) // 2
    assert names[0] == 'Erskineville Rd'
    assert strip.rows[4].time == '8:03'  # the 8:02 from across
    assert badge_rows(rows=strip.rows, markers=strip.markers) == [
        ('Egan St', 180.0), ('Cardigan St', 0.0),
    ]
    pane = view_of(state=SCENES['strip_map_both_sides']())
    assert isinstance(pane, StripMapView)
    rows = PANE_ROWS - (pane.map.banner is not None)
    kinds = [row.kind for row in pane.pane.rows]
    assert kinds.index(RowKind.ORIGIN) == (rows - 1) // 2
    assert badge_rows(rows=pane.pane.rows, markers=pane.pane.markers) == [
        ('Egan St', 180.0), ('Cardigan St', 0.0),
    ]


def test_chooser_routes_scene_keeps_the_preview_in_place() -> None:
    """The preview sits right of the focused ROUTE column."""
    view = view_of(state=SCENES['chooser_routes']())
    assert isinstance(view, ChooserView)
    assert [c.level for c in view.columns] == [
        Level.GROUP, Level.ROUTE, Level.TOWARDS,
    ]
    assert [c.focused for c in view.columns] == [False, True, False]
    assert view.columns[-1].total == 14


def test_chooser_kerbs_scenes_mark_the_pair_and_open_the_box() -> None:
    """Mark the paired row in the list scene and open the box in the other."""
    listed = view_of(state=SCENES['chooser_kerbs']())
    boxed = view_of(state=SCENES['chooser_kerbs_menu']())
    assert isinstance(listed, StripView) and isinstance(boxed, StripView)
    assert [row.paired for row in listed.rows] == [
        False, False, True, False, False,
    ]
    assert listed.menu is None
    assert boxed.menu is not None and boxed.menu.labels == (
        'Towards Emmanuel Street', 'Towards Oakington', 'Both directions',
    )
    assert boxed.panel.hint == MENU_HINT


def test_late_connection_scenes_build_their_views() -> None:
    """Marks on each bus; tight and missed changes say so."""
    easy = view_of(state=SCENES['connection_easy']())
    assert isinstance(easy, ConnectionView) and easy.hero is not None
    assert [line.mark for line in easy.hero.lines] == ['', '', '', '+2']
    assert easy.hero.lines[1].text == (
        'change at Constitution Hill, North West Twy 7:48'
    )
    assert [row.mark for row in easy.rows] == ['', '', '−2', '']
    tight = view_of(state=SCENES['connection_tight']())
    assert isinstance(tight, ConnectionView) and tight.hero is not None
    assert tight.hero.lines[1].bold
    assert tight.hero.lines[1].text.startswith('tight change at ')
    assert [row.tight for row in tight.rows] == [True, False, False, False]
    missed = view_of(state=SCENES['connection_missed']())
    assert isinstance(missed, ConnectionView) and missed.hero is not None
    assert missed.hero.lines[1].text == 'change missed · next 664 at 8:01'
    assert missed.hero.arrives_at == '8:12'
    assert [(row.was, row.arrives_at) for row in missed.rows] == [
        ('8:12', '8:27'), ('', '8:27'), ('', '8:45'), ('9:04', ''),
    ]
    strip = view_of(state=SCENES['connection_strip_tight']())
    assert isinstance(strip, ConnectionStripView)
    assert [stop.late for stop in strip.sections[0].stops] == [
        '+4', '', '+4',
    ]
    assert [stop.late for stop in strip.sections[1].stops] == ['', '', '']
    assert [pair.tight for pair in strip.pairs[:2]] == [True, True]
