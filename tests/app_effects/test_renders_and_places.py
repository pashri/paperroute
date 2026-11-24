"""Tests for app effects: renders and places."""

import errno
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

import numpy as np
import pytest

from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import Render, SaveState
from paperroute_device.runtime.outputs import Outputs
from paperroute_device.runtime.saver import StateSaver
from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import pin_of
from paperroute_testkit.core.factories import place as stop_at
from paperroute_testkit.fakes import EmptyPlacedTimetable, MemoryStates
from paperroute_testkit.render.factories import (
    STOP_VIEW,
    STRIP_VIEW,
    groups,
    view_of,
)
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.app_effects import VersionWatch
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.core.messages import (
    HolidaysLoaded,
    LoadHolidays,
    LoadServiceSpan,
    MatchPlaces,
    PlacesFailed,
    ServiceSpanLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import Pin, ServiceSpan, StopPlace
from paperroute_transit.core.saved import seeded
from paperroute_transit.render.chooser import draw_chooser_view
from paperroute_transit.render.stop_view import draw_stop_view
from paperroute_transit.render.strip_view import draw_strip_view
from tests.app_effects.helpers import (
    VersionedTimetable,
    kit_ports,
    rendered,
    sydney_app,
)
from tests.app_factories import (
    MAP_PIN,
    map_view,
    ports_of,
    posted_by,
    strip_map_view,
)


async def test_renders_each_view_type() -> None:
    """Every kind of view is drawn by its own renderer and shown."""
    display = FrameRecorder()
    ports = kit_ports(display=display)
    app = sydney_app(ports=ports_of())
    outputs = Outputs(saver=StateSaver(states=ports.states))
    chooser = view_of(groups(focused=True))
    views = (STOP_VIEW, map_view(), STRIP_VIEW, strip_map_view(), chooser)
    for view in views:
        await rendered(
            app=app, ports=ports, outputs=outputs, render=Render(view=view),
        )
    maps = MapRenderer(tiles=NullTiles())
    expected = [
        draw_stop_view(view=STOP_VIEW),
        maps.frame(view=map_view()),
        draw_strip_view(view=STRIP_VIEW),
        maps.strip_map_frame(view=strip_map_view()),
        draw_chooser_view(view=chooser),
    ]
    assert len(display.frames) == len(expected)
    for frame, want in zip(display.frames, expected, strict=True):
        assert np.array_equal(frame, want)


@dataclass
class TempNamingStates(MemoryStates):
    """A store whose every save fails naming a new temporary file."""

    tries: int = 0

    def save(self, *, saved: KitSaved) -> None:
        """
        Fail like a read-only directory, naming a fresh temporary file.

        Parameters
        ----------
        saved : KitSaved
            Ignored.

        Raises
        ------
        OSError
            Always, with a different file name each time.
        """
        self.tries += 1
        raise OSError(
            errno.EROFS,
            'Read-only file system',
            f'/var/lib/paperroute/.state.json.{self.tries}',
        )


async def test_save_failure_naming_new_temp_files_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same write error is logged once whatever temp file it names."""
    states = TempNamingStates()
    saved = seeded(pin=MAP_PIN, city=SYDNEY)
    save = SaveState(
        saved=KitSaved(device=saved.settings.device, app=saved),
    )
    saver = StateSaver(states=states)
    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            await saver.save(effect=save)
    failures = [
        record
        for record in caplog.records
        if record.message == 'saving state failed'
    ]
    assert states.tries == 3
    assert len(failures) == 1


@dataclass
class MatchFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose stop look-ups fail."""

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        place : StopPlace
            Ignored.

        Returns
        -------
        StopPlace | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_match_places_failure_posts_places_failed() -> None:
    """A failed look-up posts PlacesFailed with the timetable echoed."""
    posted = await posted_by(
        ports=ports_of(timetable=MatchFailingTimetable()),
        effects=[MatchPlaces(places=(stop_at('A'),), timetable='v1')],
    )
    assert posted == [PlacesFailed(timetable='v1')]


@dataclass
class SchemaTimetable(VersionedTimetable):
    """A timetable whose schema can change under the same build."""

    schema: str | None = '4'

    def schema_version(self) -> str | None:
        """
        Name the current schema.

        Returns
        -------
        str | None
            The schema.
        """
        return self.schema


async def test_version_watch_reports_schema() -> None:
    """The schema rides on the event, and a schema change alone is seen."""
    timetable = SchemaTimetable()
    watch = VersionWatch(timetable=timetable)
    first = await watch.check()
    timetable.schema = '5'
    second = await watch.check()
    assert (first, second, await watch.check()) == (
        TimetableSeen(version='v1', schema='4'),
        TimetableSeen(version='v1', schema='5'),
        None,
    )


@dataclass
class EndingTimetable(VersionedTimetable):
    """A timetable whose window can change under the same build."""

    ends: date | None = date(2026, 10, 29)

    def last_day(self) -> date | None:
        """
        Name the last day covered.

        Returns
        -------
        date | None
            The day.
        """
        return self.ends


async def test_version_watch_reports_the_last_day() -> None:
    """The last day rides on the event, and a change alone is seen."""
    timetable = EndingTimetable()
    watch = VersionWatch(timetable=timetable)
    first = await watch.check()
    timetable.ends = date(2026, 10, 30)
    second = await watch.check()
    assert (first, second, await watch.check()) == (
        TimetableSeen(version='v1', last_day=date(2026, 10, 29)),
        TimetableSeen(version='v1', last_day=date(2026, 10, 30)),
        None,
    )


@dataclass
class HolidaysFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose holidays can't be read."""

    def holidays(self) -> frozenset[date]:
        """
        Fail like a damaged database file.

        Returns
        -------
        frozenset[date]
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_holidays_read_failure_is_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed holidays read posts no holidays and logs one error."""
    with caplog.at_level(logging.ERROR):
        posted = await posted_by(
            ports=ports_of(timetable=HolidaysFailingTimetable()),
            effects=[LoadHolidays(timetable='v1')],
        )
    assert posted == [HolidaysLoaded(dates=frozenset(), timetable='v1')]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1


@dataclass
class SpanFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose service spans can't be read."""

    def service_span(self, *, pin: Pin, day: date) -> ServiceSpan | None:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        pin : Pin
            Ignored.
        day : date
            Ignored.

        Returns
        -------
        ServiceSpan | None
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_service_span_read() -> None:
    """A span read posts the timetable's answer for the pin and day."""
    pin = pin_of('2145615', None)
    posted = await posted_by(
        ports=ports_of(timetable=EmptyPlacedTimetable()),
        effects=[LoadServiceSpan(pin=pin, day=date(2026, 10, 16))],
    )
    assert posted == [
        ServiceSpanLoaded(pin=pin, day=date(2026, 10, 16), span=None),
    ]


async def test_service_span_failure_is_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed span read posts no span and logs one error."""
    pin = pin_of('2145615', None)
    with caplog.at_level(logging.ERROR):
        posted = await posted_by(
            ports=ports_of(timetable=SpanFailingTimetable()),
            effects=[
                LoadServiceSpan(
                    pin=pin,
                    day=date(2026, 10, 16),
                    timetable='v',
                ),
            ],
        )
    assert posted == [
        ServiceSpanLoaded(
            pin=pin, day=date(2026, 10, 16), span=None, timetable='v',
        ),
    ]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
