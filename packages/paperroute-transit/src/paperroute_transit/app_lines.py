"""Reads of the day's lines at a stop."""

from dataclasses import replace
from itertools import pairwise

from paperroute_transit.app_connections import usable
from paperroute_transit.core.map_lines import first_per_line
from paperroute_transit.core.messages import (
    DayLinesLoaded,
    LoadDayLines,
    PatternsLoaded,
)
from paperroute_transit.core.modes import skippable_modes
from paperroute_transit.core.track import tracked_shape
from paperroute_transit.measures import StopMeasures
from paperroute_transit.ports import DayTimetable, TimetableRepository


def loaded_day_lines(
    *, timetable: DayTimetable, effect: LoadDayLines
) -> DayLinesLoaded:
    """
    Read the first departure of each line a pin has on a day.

    Parameters
    ----------
    timetable : DayTimetable
        Timetable.
    effect : LoadDayLines
        Request, echoed back.

    Returns
    -------
    DayLinesLoaded
        The day's departures with the pin's never routes and, when asked,
        school runs dropped, cut to one per line.
    """
    day = timetable.day_departures(pin=effect.pin, day=effect.day)
    kept = usable(
        items=day,
        pin=replace(effect.pin, never=()),
        skipped=(
            skippable_modes(modes=timetable.city.modes)
            if effect.skip_school
            else frozenset()
        ),
    )
    return DayLinesLoaded(
        request=effect, departures=first_per_line(departures=kept),
    )


def tracked_patterns(
    *,
    timetable: TimetableRepository,
    loaded: PatternsLoaded,
    measures: StopMeasures,
) -> PatternsLoaded:
    """
    Move the rail patterns onto the track between their calls.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    loaded : PatternsLoaded
        Patterns measured on their feed shapes.
    measures : StopMeasures
        Stop places already worked out.

    Returns
    -------
    PatternsLoaded
        The same, with each rail pattern that has track on a shape of its
        own, measured on it, and those shapes added.
    """
    rail = {
        trip_id: pattern
        for trip_id, pattern in loaded.patterns.items()
        if pattern is not None and not pattern.road
    }
    hops = timetable.rail_hops(
        pairs={
            (before.station, after.station)
            for pattern in rail.values()
            for before, after in pairwise(pattern.stops)
        },
    )
    version = timetable.version()
    patterns = dict(loaded.patterns)
    shapes = dict(loaded.shapes)
    for trip_id, pattern in rail.items():
        track = tracked_shape(
            pattern=pattern,
            shape=loaded.shapes.get(pattern.shape_id or ''),
            hops=hops,
            version=version,
        )
        if track is not None:
            shapes[track.shape_id] = track
            patterns[trip_id] = measures.measure(
                pattern=replace(pattern, shape_id=track.shape_id),
                shape=track,
            )
    return replace(loaded, patterns=patterns, shapes=shapes)
