"""Tests for fitting a map to the points it should hold."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Final

from paperroute_sydney.city import SYDNEY_PROJECTION, SYDNEY_ZONE
from paperroute_testkit.core.factories import (
    EIGHT,
    STALE_LIMIT,
    START,
    calls,
    departure,
    pattern,
    snapshot,
    vehicle,
)
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_fit import (
    BANNER_ROOM,
    CREDIT_ROOM,
    KEEP_IN,
    FrameKey,
    approach_frame,
    centred_frame,
    fitted,
    frame_key,
    framed_map,
    resting_zoom,
    steady_frame,
)
from paperroute_transit.core.map_view import (
    DEFAULT_ZOOM,
    MAP_HEIGHT,
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
    MapInputs,
)
from paperroute_transit.core.model import Departure
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.saved import MapFraming
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.core.tiles import ZOOMS

FROM: Point = (0.0, 800.0)
OTHER: Point = (30.0, 800.0)
TO: Point = (0.0, -800.0)
LINE = {
    'A': FROM,
    'B': (100.0, 400.0),
    'C': (-100.0, 0.0),
    'D': (50.0, -400.0),
    'E': TO,
}


def stop_view(
    *, destination: str | None = 'Parramatta', banner: str | None = None,
) -> StopView:
    """
    A Stop view for the map's header.

    Parameters
    ----------
    destination : str | None
        The To's name, or None with no To.
    banner : str | None
        The banner.

    Returns
    -------
    StopView
        The view.
    """
    return StopView(
        origin='Town Hall',
        destination=destination,
        clock='8:00',
        hero=None,
        rows=(),
        banner=banner,
        empty=None,
        scroll=0,
    )


def plain(
    *,
    departures: tuple[Departure, ...] = (),
    patterns: dict[str, TripPattern | None] | None = None,
    ends: tuple[Point | None, Point | None] = (FROM, TO),
    now: datetime = START,
) -> MapInputs:
    """
    Map inputs for a plain pin, centred on its From.

    Parameters
    ----------
    departures : tuple[Departure, ...]
        The pin's departures.
    patterns : dict[str, TripPattern | None] | None
        Loaded patterns.
    ends : tuple[Point | None, Point | None]
        The From and To points.
    now : datetime
        Current time.

    Returns
    -------
    MapInputs
        The inputs at the resting zoom.
    """
    return MapInputs(
        stop_view=stop_view(),
        departures=departures,
        centre=ends[0],
        zoom=DEFAULT_ZOOM,
        positions={},
        patterns=patterns or {},
        shapes={},
        now=now,
        stale_after=STALE_LIMIT,
        ends=ends,
        zone=SYDNEY_ZONE,
        projection=SYDNEY_PROJECTION,
    )


def on_screen(*, centre: Point, zoom: int, point: Point) -> Point:
    """
    Find a point's map pixel.

    Parameters
    ----------
    centre : Point
        Map centre.
    zoom : int
        `ZOOMS` index.
    point : Point
        The point.

    Returns
    -------
    Point
        Map pixels from the left and top.
    """
    x, y = centre
    mpp = ZOOMS[zoom]
    px, py = point
    return (MAP_WIDTH / 2 + (px - x) / mpp, MAP_HEIGHT / 2 - (py - y) / mpp)


def test_fitted_picks_25_for_a_9_km_trip() -> None:
    """Points 9 km apart east-west fit at 25 m/px on the Map."""
    found = fitted(
        points=((0.0, 0.0), (9000.0, 0.0)),
        width=MAP_WIDTH,
        top=0,
        step=0,
        keep=None,
    )
    assert found is not None
    centre, zoom = found
    assert zoom == ZOOMS.index(25)
    assert centre == (4500.0, -CREDIT_ROOM * 25 / 2)


def test_fitted_step_moves_and_clamps() -> None:
    """The knob's step moves the fitted zoom and stops at the ends."""
    points = ((0.0, 0.0), (9000.0, 0.0))
    zooms = [
        fitted(points=points, width=MAP_WIDTH, top=0, step=step, keep=None)
        for step in (-1, -9, 9)
    ]
    assert [found[1] for found in zooms if found] == [
        ZOOMS.index(14),
        0,
        len(ZOOMS) - 1,
    ]


def test_fitted_empty_is_none() -> None:
    """No points, no fit."""
    assert fitted(
        points=(), width=MAP_WIDTH, top=0, step=0, keep=None,
    ) is None


def test_fitted_keeps_from_on_a_too_long_trip() -> None:
    """A trip too long for 45 m/px keeps the From 24 px inside the map."""
    for keep, top in (((0.0, 0.0), BANNER_ROOM), ((60_000.0, 0.0), 0)):
        found = fitted(
            points=((0.0, 0.0), (60_000.0, 0.0)),
            width=MAP_WIDTH,
            top=top,
            step=0,
            keep=keep,
        )
        assert found is not None
        centre, zoom = found
        assert ZOOMS[zoom] == 45
        x, y = on_screen(centre=centre, zoom=zoom, point=keep)
        assert KEEP_IN <= x <= MAP_WIDTH - KEEP_IN
        assert abs(min(x, MAP_WIDTH - x) - KEEP_IN) < 1e-9
        assert top + KEEP_IN <= y <= MAP_HEIGHT - CREDIT_ROOM - KEEP_IN


def test_fitted_keeps_the_same_margin_top_and_bottom() -> None:
    """A tall fit leaves every point 24 px clear of the top and the credit."""
    points = ((0.0, 0.0), (0.0, -1_900.0))
    found = fitted(
        points=points, width=MAP_WIDTH, top=0, step=0, keep=None,
    )
    assert found is not None
    centre, zoom = found
    for point in points:
        _, y = on_screen(centre=centre, zoom=zoom, point=point)
        assert KEEP_IN <= y <= MAP_HEIGHT - CREDIT_ROOM - KEEP_IN


def test_fitted_keeps_both_ends_inside_a_too_wide_line() -> None:
    """A line too wide for 45 m/px still frames From and To evenly inside."""
    start, end = (0.0, 0.0), (20_700.0, -2_000.0)
    found = fitted(
        points=(start, end, (-1_000.0, 0.0), (21_000.0, -2_000.0)),
        width=MAP_WIDTH,
        top=BANNER_ROOM,
        step=0,
        keep=start,
        ends=(start, end),
    )
    assert found is not None
    centre, zoom = found
    assert ZOOMS[zoom] == 45
    left, _ = on_screen(centre=centre, zoom=zoom, point=start)
    right, _ = on_screen(centre=centre, zoom=zoom, point=end)
    assert left >= KEEP_IN
    assert abs(left - (MAP_WIDTH - right)) < 1e-9


def test_fitted_keeps_from_below_the_banner() -> None:
    """A From too near the top moves down clear of the banner."""
    found = fitted(
        points=((0.0, 0.0), (0.0, 30_000.0)),
        width=MAP_WIDTH,
        top=BANNER_ROOM,
        step=0,
        keep=(0.0, 30_000.0),
    )
    assert found is not None
    centre, zoom = found
    _, y = on_screen(centre=centre, zoom=zoom, point=(0.0, 30_000.0))
    assert abs(y - (BANNER_ROOM + KEEP_IN)) < 1e-9


CALLS: Final[str] = 'UVWZAXY'
THROUGH: Final[dict[str, Point]] = {
    'U': (-1_600.0, 800.0),
    'V': (-1_200.0, 800.0),
    'W': (-800.0, 800.0),
    'Z': (-400.0, 800.0),
    'A': FROM,
    'X': (400.0, 800.0),
    'Y': (800.0, 800.0),
}
BACK: Final[dict[str, Point]] = {
    name: (-x, y) for name, (x, y) in THROUGH.items()
}


def through(
    *,
    trip_id: str,
    minutes: int = 0,
    first: str = 'U',
    line: dict[str, Point] = THROUGH,
    apart: int = 3,
) -> tuple[Departure, TripPattern]:
    """
    A departure through the From, calling at even gaps along a line.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    minutes : int
        Minutes after 8:00 it leaves the From.
    first : str
        The first call; U is four gaps before the From.
    line : dict[str, Point]
        Where each call is.
    apart : int
        Minutes between calls.

    Returns
    -------
    tuple[Departure, TripPattern]
        The departure and its pattern.
    """
    ride = departure(
        departs=START + timedelta(minutes=minutes), ride=30, trip_id=trip_id,
    )
    skipped = CALLS.index(first)
    stops = calls(
        stations=CALLS[skipped:],
        start=EIGHT + (minutes - apart * (4 - skipped)) * 60,
        step=apart * 60,
        points=line,
    )
    return ride, pattern(trip_id=trip_id, stops=stops)


def riding(
    *, rides: tuple[tuple[Departure, TripPattern], ...], **changes: Any,
) -> MapInputs:
    """
    Map inputs for some departures and their patterns.

    Parameters
    ----------
    rides : tuple[tuple[Departure, TripPattern], ...]
        Departures and their patterns.
    **changes : Any
        Fields to replace on the inputs.

    Returns
    -------
    MapInputs
        The inputs.
    """
    return replace(
        plain(
            departures=tuple(ride for ride, _ in rides),
            patterns={ride.trip_id: found for ride, found in rides},
        ),
        **changes,
    )


def test_approach_frame_fits_ten_minutes_before_and_three_after() -> None:
    """V to X, 1.6 km, fit at 5 m/px round their middle, moved for credit."""
    found = approach_frame(inputs=riding(rides=(through(trip_id='t1'),)))
    assert (found.centre, found.zoom) == ((-400.0, 730.0), ZOOMS.index(5))


def test_approach_frame_ignores_the_to() -> None:
    """A To far off the stretch does not move the frame."""
    rides = (through(trip_id='t1'),)
    far = approach_frame(inputs=riding(rides=rides, ends=(FROM, (0.0, -5e3))))
    none = approach_frame(inputs=riding(rides=rides, ends=(FROM, None)))
    assert (far.centre, far.zoom) == (none.centre, none.zoom)
    assert far.centre == (-400.0, 730.0)


def test_approach_frame_holds_still_as_buses_come_and_go() -> None:
    """The next bus leaving and a new one listed keep the frame."""
    first, second, third = (
        through(trip_id=f't{minutes}', minutes=minutes)
        for minutes in (0, 10, 20)
    )
    before = approach_frame(inputs=riding(rides=(first, second)))
    after = approach_frame(
        inputs=riding(
            rides=(second, third), now=START + timedelta(minutes=1),
        ),
    )
    assert (after.centre, after.zoom) == (before.centre, before.zoom)


def test_approach_frame_takes_both_directions() -> None:
    """Trips arriving from each side fit together, 2.4 km at 8 m/px."""
    found = approach_frame(
        inputs=riding(
            rides=(
                through(trip_id='east'),
                through(trip_id='west', minutes=4, line=BACK),
            ),
        ),
    )
    assert (found.centre, found.zoom) == ((0.0, 688.0), ZOOMS.index(8))


def test_approach_frame_of_a_trip_that_starts_close_by() -> None:
    """A trip starting 6 minutes before the From fits W to X at 3 m/px."""
    found = approach_frame(
        inputs=riding(rides=(through(trip_id='t1', first='W'),)),
    )
    assert (found.centre, found.zoom) == ((-200.0, 758.0), ZOOMS.index(3))


def test_approach_frame_leaves_vehicles_out() -> None:
    """A vehicle 30 km away does not widen the frame."""
    rides = (through(trip_id='t1'),)
    bus = vehicle(timestamp=int(START.timestamp()), point=(30_000.0, 800.0))
    positions = {'buses': snapshot(fetched_at=START, vehicles=(bus,))}
    seen = approach_frame(inputs=riding(rides=rides, positions=positions))
    assert (seen.centre, seen.zoom) == ((-400.0, 730.0), ZOOMS.index(5))


def test_approach_frame_keeps_the_band_clear_asleep() -> None:
    """Asleep, the frame moves up by half the band's room."""
    found = approach_frame(
        inputs=riding(rides=(through(trip_id='t1'),), asleep=True),
    )
    assert found.centre == (-400.0, 660.0)


def test_approach_frame_rests_with_nothing_to_fit() -> None:
    """No departures, or no pattern yet: the From at the stepped rest."""
    ride, _ = through(trip_id='t1')
    for inputs in (plain(), plain(departures=(ride,))):
        found = approach_frame(inputs=replace(inputs, step=-1))
        assert (found.centre, found.zoom) == (FROM, resting_zoom(step=-1))


def test_approach_frame_keeps_the_from_clear_of_the_banner_on_a_long_line(
) -> None:
    """A stretch too tall for 45 m/px keeps the From in, under the banner."""
    long = {
        name: (0.0, 800.0 + 12_000.0 * (4 - index))
        for index, name in enumerate(CALLS)
    }
    found = approach_frame(
        inputs=riding(
            rides=(through(trip_id='t1', line=long),),
            stop_view=stop_view(banner='Live times unavailable'),
        ),
    )
    assert found.centre is not None
    assert ZOOMS[found.zoom] == 45
    x, y = on_screen(centre=found.centre, zoom=found.zoom, point=FROM)
    assert KEEP_IN <= x <= MAP_WIDTH - KEEP_IN
    assert BANNER_ROOM + KEEP_IN <= y <= MAP_HEIGHT - CREDIT_ROOM - KEEP_IN


def test_approach_frame_without_from_location() -> None:
    """With no From point, the inputs are unchanged."""
    inputs = riding(rides=(through(trip_id='t1'),), ends=(None, TO))
    assert approach_frame(inputs=inputs) == inputs


def test_centred_frame_puts_the_from_in_the_middle_at_8_m() -> None:
    """The From is the centre at 8 m/px, banner or asleep alike."""
    inputs = riding(
        rides=(through(trip_id='t1'),),
        stop_view=stop_view(banner='Live times unavailable'),
    )
    for each in (inputs, replace(inputs, asleep=True)):
        found = centred_frame(inputs=each)
        assert (found.centre, ZOOMS[found.zoom]) == (FROM, 8)


def test_centred_frame_follows_the_step() -> None:
    """The knob steps the zoom from 8 m/px and clamps."""
    inputs = plain()
    assert ZOOMS[centred_frame(inputs=replace(inputs, step=-1)).zoom] == 5
    assert ZOOMS[centred_frame(inputs=replace(inputs, step=9)).zoom] == 45


def test_centred_frame_without_from_location() -> None:
    """With no From point, the inputs are unchanged."""
    inputs = plain(ends=(None, TO))
    assert centred_frame(inputs=inputs) == inputs


def test_framed_map_follows_the_setting() -> None:
    """Each framing gives its own rule's frame."""
    inputs = riding(rides=(through(trip_id='t1'),))
    assert framed_map(
        inputs=inputs, framing=MapFraming.APPROACH,
    ) == approach_frame(inputs=inputs)
    assert framed_map(
        inputs=inputs, framing=MapFraming.CENTRED,
    ) == centred_frame(inputs=inputs)


FAR: Final[dict[str, Point]] = {**THROUGH, 'X': (1_400.0, 800.0)}
SOUTHWARD: Final[dict[str, Point]] = {
    'U': (0.0, -1_300.0),
    'V': (0.0, -900.0),
    'W': (0.0, -500.0),
    'Z': (0.0, 200.0),
    'A': FROM,
    'X': (400.0, 800.0),
    'Y': (800.0, 800.0),
}
BANNER: Final[str] = 'Live times unavailable'


def held_over(*, before: MapInputs, after: MapInputs) -> tuple[Point, int]:
    """
    Settle an Approach frame on some inputs, then draw others.

    Parameters
    ----------
    before : MapInputs
        The inputs the frame is fitted to.
    after : MapInputs
        The inputs drawn next.

    Returns
    -------
    tuple[Point, int]
        The centre and zoom `steady_frame` draws the second inputs at.
    """
    fit = approach_frame(inputs=before)
    assert fit.centre is not None
    found = steady_frame(
        inputs=after,
        framing=MapFraming.APPROACH,
        held=(fit.centre, fit.zoom),
        key=frame_key(inputs=before, framing=MapFraming.APPROACH),
    )
    assert found.centre is not None
    return found.centre, found.zoom


def fresh(*, inputs: MapInputs) -> tuple[Point, int]:
    """
    Fit an Approach frame with nothing held.

    Parameters
    ----------
    inputs : MapInputs
        The inputs.

    Returns
    -------
    tuple[Point, int]
        The centre and zoom.
    """
    fit = approach_frame(inputs=inputs)
    assert fit.centre is not None
    return fit.centre, fit.zoom


def test_frame_key_of_a_settled_approach_frame() -> None:
    """A fit with a pattern loaded is keyed by ends, step, width and sleep."""
    inputs = riding(rides=(through(trip_id='t1'),))
    assert frame_key(inputs=inputs, framing=MapFraming.APPROACH) == FrameKey(
        ends=(FROM, TO), step=0, width=MAP_WIDTH, asleep=False,
    )


def test_frame_key_is_none_unsettled_centred_or_without_a_from() -> None:
    """No pattern yet, the Centred setting or no From point: no key."""
    ride, _ = through(trip_id='t1')
    inputs = riding(rides=(through(trip_id='t1'),))
    assert frame_key(
        inputs=plain(departures=(ride,)), framing=MapFraming.APPROACH,
    ) is None
    assert frame_key(inputs=inputs, framing=MapFraming.CENTRED) is None
    assert frame_key(
        inputs=replace(inputs, ends=(None, TO)), framing=MapFraming.APPROACH,
    ) is None


def test_steady_frame_holds_when_another_pattern_leaves() -> None:
    """The westbound leaving keeps the 8 m/px frame; alone east fits 5."""
    east = through(trip_id='east')
    west = through(trip_id='west', minutes=4, line=BACK)
    after = riding(rides=(east,))
    assert fresh(inputs=after) == ((-400.0, 730.0), ZOOMS.index(5))
    assert held_over(before=riding(rides=(east, west)), after=after) == (
        (0.0, 688.0),
        ZOOMS.index(8),
    )


def test_steady_frame_holds_when_faster_trips_leave() -> None:
    """A slower trip left alone keeps the 5 m/px frame; alone it fits 3."""
    fast = through(trip_id='fast')
    slow = through(trip_id='slow', minutes=2, apart=4)
    after = riding(rides=(slow,))
    assert fresh(inputs=after) == ((-400.0, 758.0), ZOOMS.index(3))
    assert held_over(before=riding(rides=(fast, slow)), after=after) == (
        (-400.0, 730.0),
        ZOOMS.index(5),
    )


def test_steady_frame_holds_under_a_banner_clear_of_the_from() -> None:
    """A banner over the top leaves the frame while the From is clear."""
    rides = (through(trip_id='t1'),)
    after = riding(rides=rides, stop_view=stop_view(banner=BANNER))
    assert fresh(inputs=after) == ((-400.0, 815.0), ZOOMS.index(5))
    assert held_over(before=riding(rides=rides), after=after) == (
        (-400.0, 730.0),
        ZOOMS.index(5),
    )


def test_steady_frame_refits_when_a_banner_covers_the_from() -> None:
    """A From 36 px down goes under the banner, so the map refits."""
    rides = (through(trip_id='t1', line=SOUTHWARD),)
    before = riding(rides=rides)
    centre, zoom = fresh(inputs=before)
    assert on_screen(centre=centre, zoom=zoom, point=FROM)[1] == 36.0
    after = riding(rides=rides, stop_view=stop_view(banner=BANNER))
    centre, zoom = held_over(before=before, after=after)
    assert (centre, zoom) == fresh(inputs=after)
    assert ZOOMS[zoom] == 8
    _, y = on_screen(centre=centre, zoom=zoom, point=FROM)
    assert y >= BANNER_ROOM + KEEP_IN


def test_steady_frame_refits_for_a_stop_off_the_frame() -> None:
    """A new trip calling past the held frame's edge refits at 8 m/px."""
    first = through(trip_id='t1')
    after = riding(rides=(first, through(trip_id='far', minutes=10, line=FAR)))
    found = held_over(before=riding(rides=(first,)), after=after)
    assert found == fresh(inputs=after)
    assert ZOOMS[found[1]] == 8


def test_steady_frame_refits_for_a_new_step_width_sleep_or_pin() -> None:
    """A knob step, the strip's map, sleep or another From refits."""
    before = riding(
        rides=(
            through(trip_id='east'),
            through(trip_id='west', minutes=4, line=BACK),
        ),
    )
    east = riding(rides=(through(trip_id='east'),))
    for changes in (
        {'step': -1},
        {'width': STRIP_MAP_WIDTH},
        {'asleep': True},
        {'ends': (OTHER, TO), 'centre': OTHER},
    ):
        after = replace(east, **changes)
        found = held_over(before=before, after=after)
        assert found == fresh(inputs=after)
        assert found != fresh(inputs=before)


def test_steady_frame_centred_takes_no_hold() -> None:
    """With Centred, a held Approach frame gives way to the From."""
    before = riding(rides=(through(trip_id='t1'),))
    fit = approach_frame(inputs=before)
    assert fit.centre is not None
    found = steady_frame(
        inputs=before,
        framing=MapFraming.CENTRED,
        held=(fit.centre, fit.zoom),
        key=frame_key(inputs=before, framing=MapFraming.APPROACH),
    )
    assert found == centred_frame(inputs=before)


def test_steady_frame_holds_zoomed_in_as_a_bus_leaves() -> None:
    """One detent finer, the fast bus leaving moves nothing."""
    fast = through(trip_id='fast')
    slow = through(trip_id='slow', minutes=2, apart=4)
    before = riding(rides=(fast, slow), step=-1)
    after = riding(rides=(slow,), step=-1)
    found = held_over(before=before, after=after)
    assert found == fresh(inputs=before)


def test_approach_frame_asleep_ignores_the_banner_it_hides() -> None:
    """Asleep, no banner is drawn, so none of its room is kept."""
    asleep = riding(rides=(through(trip_id='t1'),), asleep=True)
    bannered = replace(asleep, stop_view=stop_view(banner=BANNER))
    assert approach_frame(inputs=bannered) == replace(
        approach_frame(inputs=asleep), stop_view=bannered.stop_view,
    )
