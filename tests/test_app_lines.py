"""Tests for the day's lines read at a stop."""

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final

from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.strip_factories import PIN
from paperroute_testkit.fakes import EmptyPlacedTimetable
from paperroute_transit.app_lines import loaded_day_lines
from paperroute_transit.core.messages import LoadDayLines
from paperroute_transit.core.model import Departure, Pin

DAY: Final[date] = START.date()
PIN_NO_TO: Final[Pin] = replace(PIN, destination=None)


@dataclass
class DayTimetable(EmptyPlacedTimetable):
    """Gives a fixed day of departures and records each read."""

    reads: list[tuple[Pin, date]] = field(default_factory=list)

    def day_departures(self, *, pin: Pin, day: date) -> list[Departure]:
        """
        Give the 606 twice, the 610, the 620 and a school run.

        Parameters
        ----------
        pin : Pin
            Recorded.
        day : date
            Recorded.

        Returns
        -------
        list[Departure]
            The day.
        """
        self.reads.append((pin, day))
        return [
            departure(departs=START, route='606', trip_id='a'),
            departure(departs=START, route='610', trip_id='b'),
            departure(departs=START, route='606', trip_id='c'),
            departure(departs=START, route='620', trip_id='d'),
            departure(
                departs=START, route='S1', trip_id='e', kind='school',
            ),
        ]


def routes(*, skip_school: bool) -> list[str]:
    """
    List the routes of the day lines read with a school setting.

    Parameters
    ----------
    skip_school : bool
        Whether school runs are dropped.

    Returns
    -------
    list[str]
        The routes, in order.
    """
    loaded = loaded_day_lines(
        timetable=DayTimetable(),
        effect=LoadDayLines(
            pin=PIN_NO_TO, day=DAY, timetable=None, skip_school=skip_school,
        ),
    )
    return [item.route for item in loaded.departures]


def test_day_lines_keep_the_first_departure_of_each_line() -> None:
    """The day is read once and kept one departure per line."""
    timetable = DayTimetable()
    loaded_day_lines(
        timetable=timetable,
        effect=LoadDayLines(
            pin=PIN_NO_TO, day=DAY, timetable=None, skip_school=False,
        ),
    )
    assert timetable.reads == [(PIN_NO_TO, DAY)]
    assert routes(skip_school=False) == ['606', '610', '620', 'S1']


def test_day_lines_drop_school_runs_when_school_buses_are_off() -> None:
    """A school run gets no line while the school buses setting is on."""
    assert routes(skip_school=True) == ['606', '610', '620']
