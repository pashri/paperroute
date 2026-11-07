"""Tests for fitting a map to the points it should hold."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute.core.geo import Point
from paperroute.core.map_fit import (
    BANNER_ROOM,
    CREDIT_ROOM,
    KEEP_IN,
    fitted,
    plain_frame,
    resting_zoom,
    steady_frame,
)
from paperroute.core.map_view import (
    DEFAULT_ZOOM,
    MAP_HEIGHT,
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
    MapInputs,
)
from paperroute.core.model import Departure
from paperroute.core.patterns import TripPattern
from paperroute.core.stop_view import StopView
from paperroute.core.tiles import ZOOMS
from tests.core.factories import STALE_LIMIT, START, calls, departure, pattern

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
    )


def next_trip() -> tuple[Departure, dict[str, TripPattern | None]]:
    """
    A 606 from the From to the To, and its pattern.

    Returns
    -------
    tuple[Departure, dict[str, TripPattern | None]]
        The departure and the pattern by trip ID.
    """
    ride = departure(departs=START, ride=20, trip_id='t1')
    stops = calls(stations='ABCDE', points=LINE)
    return ride, {'t1': pattern(trip_id='t1', stops=stops)}


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


def test_plain_map_fits_the_next_trip() -> None:
    """From and To 1.6 km apart fit at 5 m/px, the box moved for credit."""
    ride, patterns = next_trip()
    found = plain_frame(
        inputs=plain(departures=(ride,), patterns=patterns),
    )
    assert found.zoom == ZOOMS.index(5)
    assert found.centre == (0.0, -CREDIT_ROOM * 5 / 2)
    assert found.centre == (0.0, -70.0)
    _, y = on_screen(centre=found.centre, zoom=found.zoom, point=FROM)
    assert y >= KEEP_IN


def test_plain_fit_moves_down_for_the_banner() -> None:
    """Under a banner, the fit takes the banner's room off the top."""
    ride, patterns = next_trip()
    inputs = replace(
        plain(departures=(ride,), patterns=patterns),
        stop_view=stop_view(banner='Live times unavailable'),
    )
    found = plain_frame(inputs=inputs)
    assert found.centre is not None
    mpp = ZOOMS[found.zoom]
    assert found.centre[1] == (BANNER_ROOM - CREDIT_ROOM) * mpp / 2


def test_sleep_fit_ignores_the_banner_it_hides() -> None:
    """Asleep, no banner is drawn, so its room is not kept at the top."""
    ride, patterns = next_trip()
    asleep = replace(
        plain(departures=(ride,), patterns=patterns), asleep=True,
    )
    bannered = replace(
        asleep, stop_view=stop_view(banner='Live times unavailable'),
    )
    assert plain_frame(inputs=bannered) == replace(
        plain_frame(inputs=asleep), stop_view=bannered.stop_view,
    )


def test_sleep_fit_keeps_only_the_band_clear() -> None:
    """Asleep, a trip that fits above the band and its margins keeps zoom."""
    inputs = replace(plain(ends=(FROM, (0.0, 800.0 - 1_680.0))), asleep=True)
    assert plain_frame(inputs=inputs).zoom == ZOOMS.index(5)


def test_plain_fit_without_from_location() -> None:
    """With no From point, the inputs are unchanged."""
    ride, patterns = next_trip()
    inputs = plain(departures=(ride,), patterns=patterns, ends=(None, TO))
    assert plain_frame(inputs=inputs) == inputs


def test_plain_fit_skips_a_departed_trip() -> None:
    """The fit is of the next departure, not one already gone."""
    ride, patterns = next_trip()
    gone = departure(departs=START - timedelta(minutes=5), trip_id='t0')
    far = calls(
        stations='AZ',
        start=8 * 3600 - 300,
        points={'A': FROM, 'Z': (40_000.0, 800.0)},
    )
    found = plain_frame(
        inputs=plain(
            departures=(gone, ride),
            patterns={**patterns, 't0': pattern(trip_id='t0', stops=far)},
        ),
    )
    assert found.zoom == ZOOMS.index(5)


def test_plain_fit_takes_the_first_when_all_are_gone() -> None:
    """With every departure gone, the first one is fitted."""
    ride, patterns = next_trip()
    found = plain_frame(
        inputs=plain(
            departures=(ride,),
            patterns=patterns,
            now=START + timedelta(hours=1),
        ),
    )
    assert found.zoom == ZOOMS.index(5)


def test_plain_map_without_to_fits_20_minutes() -> None:
    """With no To, the fit holds the stops of the first 20 minutes."""
    ride = departure(departs=START, ride=60, trip_id='t1')
    stops = calls(
        stations='ABCDEFG',
        step=600,
        points={
            name: (index * 1000.0, 800.0)
            for index, name in enumerate('ABCDEFG')
        },
    )
    inputs = replace(
        plain(
            departures=(ride,),
            patterns={'t1': pattern(trip_id='t1', stops=stops)},
            ends=(FROM, None),
        ),
        stop_view=stop_view(destination=None),
    )
    found = plain_frame(inputs=inputs)
    assert found.centre is not None
    assert found.centre[0] == 1000.0
    assert found.zoom == ZOOMS.index(5)


def test_plain_fit_without_pattern_keeps_from() -> None:
    """No To and no pattern yet: the From at the resting zoom."""
    ride, _ = next_trip()
    inputs = replace(
        plain(departures=(ride,), ends=(FROM, None)),
        stop_view=stop_view(destination=None),
        step=-1,
    )
    found = plain_frame(inputs=inputs)
    assert found.centre == FROM
    assert found.zoom == resting_zoom(step=-1)


def test_plain_fit_without_departures() -> None:
    """No departures: the From alone rests, and a To is still held."""
    alone = replace(
        plain(ends=(FROM, None)),
        stop_view=stop_view(destination=None),
    )
    found = plain_frame(inputs=alone)
    assert (found.centre, found.zoom) == (FROM, DEFAULT_ZOOM)
    both = plain_frame(inputs=plain())
    assert both.zoom == ZOOMS.index(5)


def test_plain_fit_follows_the_step() -> None:
    """The knob's step moves the fitted zoom."""
    ride, patterns = next_trip()
    found = plain_frame(
        inputs=replace(
            plain(departures=(ride,), patterns=patterns), step=1,
        ),
    )
    assert found.zoom == ZOOMS.index(8)


def test_strip_map_fits_at_its_own_width() -> None:
    """A box that just fits 520 px at 5 m/px needs 8 m/px at 480 px."""
    ends = (FROM, (2300.0, 800.0))
    wide = plain_frame(inputs=plain(ends=ends))
    narrow = plain_frame(
        inputs=replace(plain(ends=ends), width=STRIP_MAP_WIDTH),
    )
    assert (wide.zoom, narrow.zoom) == (ZOOMS.index(5), ZOOMS.index(8))



def eastward(
    *, trip_id: str, minutes: int, lean: float,
) -> tuple[Departure, TripPattern]:
    """
    A no-To departure running east from the From, and its pattern.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    minutes : int
        Minutes after 8:00 it leaves.
    lean : float
        Metres east per stop; negative runs west.

    Returns
    -------
    tuple[Departure, TripPattern]
        The departure and its pattern.
    """
    ride = departure(
        departs=START + timedelta(minutes=minutes), ride=60, trip_id=trip_id,
    )
    stops = calls(
        stations='ABCDE',
        start=8 * 3600 + minutes * 60,
        points={
            name: (index * lean, 800.0 + index * 40.0)
            for index, name in enumerate('ABCDE')
        },
    )
    return ride, pattern(trip_id=trip_id, stops=stops)


def no_to(
    *, rides: tuple[tuple[Departure, TripPattern], ...], now: datetime,
) -> MapInputs:
    """
    Map inputs for a pin with no To.

    Parameters
    ----------
    rides : tuple[tuple[Departure, TripPattern], ...]
        Departures and their patterns.
    now : datetime
        Current time.

    Returns
    -------
    MapInputs
        The inputs, centred on the From.
    """
    return replace(
        plain(
            departures=tuple(ride for ride, _ in rides),
            patterns={ride.trip_id: found for ride, found in rides},
            ends=(FROM, None),
            now=now,
        ),
        stop_view=stop_view(destination=None),
    )


def test_steady_frame_holds_while_the_next_trip_fits() -> None:
    """A new next departure that fits the frame on screen moves nothing."""
    rides = (
        eastward(trip_id='t1', minutes=0, lean=500.0),
        eastward(trip_id='t2', minutes=2, lean=450.0),
    )
    first = plain_frame(inputs=no_to(rides=rides, now=START))
    assert first.centre is not None
    held = (first.centre, first.zoom)
    later = no_to(rides=rides, now=START + timedelta(minutes=1))
    assert plain_frame(inputs=later).centre != first.centre
    found = steady_frame(inputs=later, held=held, held_ends=later.ends)
    assert (found.centre, found.zoom) == held


def test_steady_frame_refits_when_the_next_trip_leaves_it() -> None:
    """A next departure the frame no longer holds is fitted afresh."""
    rides = (
        eastward(trip_id='t1', minutes=0, lean=500.0),
        eastward(trip_id='t2', minutes=2, lean=-500.0),
    )
    first = plain_frame(inputs=no_to(rides=rides, now=START))
    assert first.centre is not None
    later = no_to(rides=rides, now=START + timedelta(minutes=1))
    found = steady_frame(
        inputs=later,
        held=(first.centre, first.zoom),
        held_ends=later.ends,
    )
    assert found == plain_frame(inputs=later)
    assert found.centre != first.centre


def test_steady_frame_refits_on_a_new_zoom() -> None:
    """A knob step or a new fitted zoom always refits."""
    rides = (eastward(trip_id='t1', minutes=0, lean=500.0),)
    inputs = no_to(rides=rides, now=START)
    first = plain_frame(inputs=inputs)
    assert first.centre is not None
    stepped_in = replace(inputs, step=-1)
    found = steady_frame(
        inputs=stepped_in,
        held=(first.centre, first.zoom),
        held_ends=stepped_in.ends,
    )
    assert found == plain_frame(inputs=stepped_in)


def test_steady_frame_refits_on_a_new_pin() -> None:
    """A frame drawn for another pin is fitted afresh, even if it holds."""
    rides = (
        eastward(trip_id='t1', minutes=0, lean=500.0),
        eastward(trip_id='t2', minutes=2, lean=450.0),
    )
    first = plain_frame(inputs=no_to(rides=rides, now=START))
    assert first.centre is not None
    held = (first.centre, first.zoom)
    later = no_to(rides=rides, now=START + timedelta(minutes=1))
    found = steady_frame(
        inputs=later, held=held, held_ends=(OTHER, None),
    )
    assert found.centre != held[0]
    assert found == plain_frame(inputs=later)


def test_steady_frame_without_a_held_frame_fits() -> None:
    """With nothing on screen yet, the trip is fitted."""
    rides = (eastward(trip_id='t1', minutes=0, lean=500.0),)
    inputs = no_to(rides=rides, now=START)
    assert steady_frame(inputs=inputs, held=None) == plain_frame(
        inputs=inputs,
    )
