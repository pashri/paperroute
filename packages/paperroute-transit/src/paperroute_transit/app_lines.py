"""Reads of the day's lines at a stop."""

from dataclasses import replace

from paperroute_transit.app_connections import usable
from paperroute_transit.core.map_lines import first_per_line
from paperroute_transit.core.messages import DayLinesLoaded, LoadDayLines
from paperroute_transit.core.modes import skippable_modes
from paperroute_transit.ports import DayTimetable


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
