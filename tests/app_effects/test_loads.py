"""Tests for app effects: loads."""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest

from paperroute_device.runtime.loop import run, ticker
from paperroute_sydney.city import SYDNEY, SydneyFeed
from paperroute_testkit.core.factories import place as stop_at
from paperroute_testkit.fakes import (
    STOP_POINT,
    BrokenTimetable,
    EmptyPlacedTimetable,
    FakeNetwork,
)
from paperroute_transit.core.chooser import Column, Level
from paperroute_transit.core.geo import MetreBox
from paperroute_transit.core.map_state import area_box
from paperroute_transit.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadLine,
    LoadModes,
    LoadStrip,
    MatchPlaces,
    ModesLoaded,
    PlacesMatched,
    StripFailed,
    StripLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import build_reach
from paperroute_transit.core.sides import KerbPair
from tests.app_effects.helpers import (
    STRIP_KEY,
    VersionedTimetable,
    VersionFailingTimetable,
    kit_ports,
    reach_data,
    sydney_app,
)
from tests.app_factories import AT, MAP_PIN, ports_of, posted_by
from tests.core.chooser_walks import KERB_LINE, PARENT_KERBS, PARENT_PAIR
from tests.helpers import version_failures


async def test_version_error_does_not_quit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing version read is logged once and the app keeps running."""
    app = sydney_app(
        ports=ports_of(timetable=VersionFailingTimetable()), seed=MAP_PIN,
    )
    with caplog.at_level(logging.ERROR):
        kit = await run(
            app=app,
            ports=kit_ports(),
            zone=SYDNEY.zone,
            tick_every=0.01,
            max_events=15,
        )
    assert kit.running
    failures = version_failures(caplog=caplog)
    assert len(failures) == 1


async def test_timetable_version_change_posts_seen() -> None:
    """The ticker posts the version at start and again when it changes."""
    timetable = VersionedTimetable()
    queue: asyncio.Queue[object] = asyncio.Queue()
    task = asyncio.create_task(
        ticker(
            app=sydney_app(ports=ports_of(timetable=timetable)),
            ports=kit_ports(),
            queue=queue,
            every=0.01,
            pump=None,
        )
    )
    await asyncio.sleep(0.1)
    timetable.built = 'v2'
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    posted = [queue.get_nowait() for _ in range(queue.qsize())]
    seen = [event for event in posted if isinstance(event, TimetableSeen)]
    assert seen == [TimetableSeen(version='v1'), TimetableSeen(version='v2')]
    assert isinstance(posted[0], TimetableSeen)


async def test_load_strip_posts_reach(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LoadStrip builds the reach and echoes the key and timetable."""
    data = reach_data()
    load = LoadStrip(key=STRIP_KEY, origin=data.origin, timetable='v1')
    with caplog.at_level(logging.INFO):
        posted = await posted_by(
            ports=ports_of(network=FakeNetwork(data=data)), effects=[load]
        )
    assert posted == [
        StripLoaded(
            key=STRIP_KEY, reach=build_reach(data=data), timetable='v1'
        )
    ]
    built = [r for r in caplog.records if r.message == 'strip built']
    assert len(built) == 1
    assert built[0].__dict__['ms'] >= 0


async def test_strip_built_logs_when_no_pairs_are_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A strip fed by both kerbs logs once when the pairs table is empty."""
    data = reach_data()
    key = replace(STRIP_KEY, across='x2')
    load = LoadStrip(key=key, origin=data.origin, timetable='v1')
    with caplog.at_level(logging.INFO):
        await posted_by(
            ports=ports_of(network=FakeNetwork(data=data)), effects=[load]
        )
    found = [
        r
        for r in caplog.records
        if r.message == 'no kerb pairs for the strip'
    ]
    assert [r.__dict__['station'] for r in found] == ['C']


async def test_load_strip_without_runs_posts_no_reach() -> None:
    """A timetable with no run tables loads a strip with no reach."""
    load = LoadStrip(key=STRIP_KEY, origin=stop_at('C'), timetable='v1')
    posted = await posted_by(ports=ports_of(), effects=[load])
    assert posted == [StripLoaded(key=STRIP_KEY, reach=None, timetable='v1')]


async def test_modes_load_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each LoadModes logs the modes it found."""
    box = area_box(centre=STOP_POINT, mpp=8)
    with caplog.at_level(logging.INFO):
        posted = await posted_by(
            ports=ports_of(), effects=[LoadModes(mpp=8, box=box)],
        )
    assert posted == [ModesLoaded(mpp=8, box=box, modes=frozenset())]
    [record] = [r for r in caplog.records if r.message == 'modes near']
    assert record.__dict__['mpp'] == 8
    assert record.__dict__['modes'] == []


@dataclass
class MixedModesTimetable(EmptyPlacedTimetable):
    """A placed timetable with trains and buses near everywhere."""

    def modes_near(self, *, box: MetreBox) -> frozenset[SydneyFeed]:
        """
        Find a train and a bus.

        Parameters
        ----------
        box : MetreBox
            Ignored.

        Returns
        -------
        frozenset[SydneyFeed]
            Trains and buses.
        """
        return frozenset({SydneyFeed.TRAIN, SydneyFeed.BUS})


async def test_modes_load_logs_modes_sorted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-empty modes set is logged as a sorted list."""
    box = area_box(centre=STOP_POINT, mpp=8)
    with caplog.at_level(logging.INFO):
        posted = await posted_by(
            ports=ports_of(timetable=MixedModesTimetable()),
            effects=[LoadModes(mpp=8, box=box)],
        )
    assert posted == [
        ModesLoaded(
            mpp=8,
            box=box,
            modes=frozenset({SydneyFeed.TRAIN, SydneyFeed.BUS}),
        ),
    ]
    [record] = [r for r in caplog.records if r.message == 'modes near']
    assert record.__dict__['modes'] == ['bus', 'train']


async def test_load_strip_failure_posts_strip_failed() -> None:
    """A failed runs read posts StripFailed with the timetable echoed."""
    network = FakeNetwork(error=sqlite3.OperationalError('locked'))
    load = LoadStrip(key=STRIP_KEY, origin=stop_at('C'), timetable='v1')
    posted = await posted_by(ports=ports_of(network=network), effects=[load])
    assert posted == [StripFailed(key=STRIP_KEY, timetable='v1')]


async def test_load_chooser_departures_posts_result() -> None:
    """The chooser's departures come back up to the end asked for."""
    end = AT + timedelta(hours=1)
    load = LoadChooserDepartures(
        pin=MAP_PIN, start=AT, end=end, timetable='v1'
    )
    posted = await posted_by(ports=ports_of(), effects=[load])
    assert posted == [
        ChooserDeparturesLoaded(
            pin=MAP_PIN, departures=(), until=end, timetable='v1'
        )
    ]


async def test_load_chooser_departures_failure_posts_failed() -> None:
    """A failed departures read posts ChooserDeparturesFailed."""
    load = LoadChooserDepartures(
        pin=MAP_PIN, start=AT, end=AT, timetable='v1'
    )
    posted = await posted_by(
        ports=ports_of(timetable=BrokenTimetable()), effects=[load]
    )
    assert posted == [ChooserDeparturesFailed(pin=MAP_PIN, timetable='v1')]


async def test_load_column_posts_column() -> None:
    """LoadColumn posts the network's column with the timetable echoed."""
    column = Column(level=Level.KIND, items=())
    network = FakeNetwork(column=column)
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadColumn(path=(), timetable='v1')],
    )
    assert posted == [ColumnLoaded(path=(), column=column, timetable='v1')]


async def test_load_column_failure_posts_column_failed() -> None:
    """A failed column read posts ColumnFailed with the timetable echoed."""
    network = FakeNetwork(error=sqlite3.OperationalError('locked'))
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadColumn(path=(), timetable='v1')],
    )
    assert posted == [ColumnFailed(path=(), timetable='v1')]


async def test_load_line_posts_line_and_failure() -> None:
    """LoadLine posts the stations, or LineFailed when the read fails."""
    line = (stop_at('A'), stop_at('B'))
    load = LoadLine(towards=7, timetable='v1')
    posted = await posted_by(
        ports=ports_of(network=FakeNetwork(line=line)), effects=[load]
    )
    assert posted == [LineLoaded(towards=7, line=line, timetable='v1')]
    broken = FakeNetwork(error=sqlite3.OperationalError('locked'))
    posted = await posted_by(ports=ports_of(network=broken), effects=[load])
    assert posted == [LineFailed(towards=7, timetable='v1')]


async def test_load_line_posts_the_pairs_of_its_stations() -> None:
    """LoadLine carries the pairs of the line's stations, none off a line."""
    line = (stop_at('A'), stop_at('B'))
    pair = KerbPair(station='B', other=stop_at('X'), busy=False)
    network = FakeNetwork(line=line, pairs={'B': pair, 'Z': pair})
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadLine(towards=7, timetable='v1')],
    )
    assert posted == [
        LineLoaded(towards=7, line=line, timetable='v1', pairs=(pair,)),
    ]
    posted = await posted_by(
        ports=ports_of(network=FakeNetwork(pairs={'B': pair})),
        effects=[LoadLine(towards=7, timetable='v1')],
    )
    assert posted == [LineLoaded(towards=7, line=None, timetable='v1')]


async def test_load_line_posts_a_parent_station_pair() -> None:
    """LoadLine offers a parent station as both of its kerbs."""
    network = FakeNetwork(line=KERB_LINE, parents={'s2': PARENT_KERBS})
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadLine(towards=7, timetable='v1')],
    )
    assert posted == [
        LineLoaded(
            towards=7, line=KERB_LINE, timetable='v1', pairs=(PARENT_PAIR,),
        ),
    ]


@dataclass
class MatchingTimetable(EmptyPlacedTimetable):
    """A timetable that finds stop A again and loses every other stop."""

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Find stop A at the test point.

        Parameters
        ----------
        place : StopPlace
            Place to find.

        Returns
        -------
        StopPlace | None
            Stop A placed; None for any other.
        """
        return self.stop(stop_id='A') if place.stop_id == 'A' else None


async def test_match_places_posts_found() -> None:
    """MatchPlaces posts each place's match by its old stop ID."""
    posted = await posted_by(
        ports=ports_of(timetable=MatchingTimetable()),
        effects=[MatchPlaces(places=(stop_at('A'), stop_at('B')))],
    )
    assert posted == [
        PlacesMatched(
            found={
                'A': StopPlace(stop_id='A', name='A', point=STOP_POINT),
                'B': None,
            }
        )
    ]


async def test_match_places_posts_the_parents_of_the_matches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Matches carry the found parents' kerbs; a failed read gives none."""
    kerbs = replace(PARENT_KERBS, station='A')
    found = {
        'A': StopPlace(stop_id='A', name='A', point=STOP_POINT), 'B': None,
    }
    match = MatchPlaces(places=(stop_at('A'), stop_at('B')))
    posted = await posted_by(
        ports=ports_of(
            timetable=MatchingTimetable(),
            network=FakeNetwork(parents={'A': kerbs, 'Z': kerbs}),
        ),
        effects=[match],
    )
    assert posted == [PlacesMatched(found=found, parents={'A': kerbs})]
    with caplog.at_level(logging.WARNING):
        posted = await posted_by(
            ports=ports_of(
                timetable=MatchingTimetable(),
                network=FakeNetwork(
                    parents={'A': kerbs},
                    pairs_error=sqlite3.OperationalError('locked'),
                ),
            ),
            effects=[match],
        )
    assert posted == [PlacesMatched(found=found)]
    assert 'reading the parent kerbs failed' in caplog.text
