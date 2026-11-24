"""Tests for timetable db: kinds."""

import dataclasses
from datetime import date, datetime, timedelta
from pathlib import Path

from paperroute_sydney.city import SYDNEY_ZONE, SydneyMode
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.factories import pin_of, place
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.model import Pin
from tests.adapters.timetable_db.helpers import (
    REAL_DB,
    at,
    opened,
    require_real_monday,
)
from tests.build.feeds import DAILY, build_db, stop_times


def test_train_pin_to_blacktown_parramatta_lists_only_trains() -> None:
    """The 8:09 706 bus between the stations' stands is not listed."""
    require_real_monday()
    with opened(path=REAL_DB) as repo:
        pin = Pin(
            origin=repo.stop(stop_id='214810'),
            destination=repo.stop(stop_id='215020'),
            kind=SydneyMode.TRAIN,
            routes=('T1',),
        )
        found = repo.departures(
            pin=pin,
            start=datetime(2026, 10, 19, 8, 0, tzinfo=SYDNEY_ZONE),
            end=datetime(2026, 10, 19, 8, 30, tzinfo=SYDNEY_ZONE),
        )
    assert found
    assert {d.facts.kind for d in found} == {SydneyMode.TRAIN}
    assert all(d.arrives - d.departs < timedelta(minutes=30) for d in found)


def stand_feed(*, path: Path) -> Path:
    """
    Write stations P and Q, each with a platform and a bus stand.

    Parameters
    ----------
    path : Path
        Feed zip to write.

    Returns
    -------
    Path
        The feed: train `a` P_1 8:00 to Q_1 8:10, 706 bus `b` P_B 8:02 to
        Q_B 8:50 and 606 bus `c` P_B 8:04 to Q_B 8:30.
    """
    stops = [
        {'stop_id': key, 'stop_name': f'{key} Station',
         'location_type': '1', 'parent_station': '',
         'stop_lat': lat, 'stop_lon': '151.0'}
        for key, lat in (('P', '-33.8'), ('Q', '-33.9'))
    ] + [
        {'stop_id': f'{key}_{kind}', 'stop_name': f'{key} Station {kind}',
         'location_type': '0', 'parent_station': key,
         'stop_lat': lat, 'stop_lon': '151.0'}
        for key, lat in (('P', '-33.8'), ('Q', '-33.9'))
        for kind in ('1', 'B')
    ]
    return write_feed(
        path=path,
        files={
            'stops.txt': stops,
            'routes.txt': [
                {'route_id': route, 'agency_id': 'SydneyTrains',
                 'route_short_name': route, 'route_long_name': route,
                 'route_type': kind}
                for route, kind in (
                    ('T1', '2'), ('706', '700'), ('606', '700'),
                )
            ],
            'calendar.txt': [{'service_id': 'daily', **DAILY}],
            'trips.txt': [
                {'route_id': route, 'service_id': 'daily', 'trip_id': trip,
                 'trip_headsign': 'Q', 'direction_id': '0',
                 'block_id': ''}
                for trip, route in (('a', 'T1'), ('b', '706'), ('c', '606'))
            ],
            'stop_times.txt': [
                *stop_times(trip='a', times=[('P_1', '08:00:00'),
                                             ('Q_1', '08:10:00')]),
                *stop_times(trip='b', times=[('P_B', '08:02:00'),
                                             ('Q_B', '08:50:00')]),
                *stop_times(trip='c', times=[('P_B', '08:04:00'),
                                             ('Q_B', '08:30:00')]),
            ],
        },
    )


def test_destination_keeps_the_pins_kind(tmp_path: Path) -> None:
    """A Train pin to Q lists the train, not the bus between the stands."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(
            name='sydneytrains', archive=stand_feed(path=tmp_path / 'p.zip'),
        )],
    )
    window = {'start': at(hour=8, minute=0), 'end': at(hour=9, minute=0)}
    train = Pin(
        origin=place('P'), destination=place('Q'), kind=SydneyMode.TRAIN,
    )
    with opened(path=db) as repo:
        trains = repo.departures(pin=train, **window)
        every = repo.departures(pin=pin_of('P', 'Q'), **window)
    assert [d.trip_id for d in trains] == ['a']
    assert [d.trip_id for d in every] == ['a', 'b', 'c']


def test_whole_stop_board_only_pin_kind(tmp_path: Path) -> None:
    """A Train pin's board leaves out the stand's buses; no kind keeps all."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(
            name='sydneytrains', archive=stand_feed(path=tmp_path / 'p.zip'),
        )],
    )
    window = {'start': at(hour=8, minute=0), 'end': at(hour=9, minute=0)}
    train = Pin(origin=place('P'), kind=SydneyMode.TRAIN, routes=('T1',))
    with opened(path=db) as repo:
        trains = repo.departures(pin=train, **window)
        every = repo.departures(
            pin=dataclasses.replace(train, kind=None, routes=()), **window,
        )
    assert [d.trip_id for d in trains] == ['a']
    assert [d.trip_id for d in every] == ['a', 'b', 'c']


def test_destination_keeps_the_pins_bus_routes(tmp_path: Path) -> None:
    """A 606 Bus pin to Q lists the 606, not the 706 or the train."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(
            name='sydneytrains', archive=stand_feed(path=tmp_path / 'p.zip'),
        )],
    )
    window = {'start': at(hour=8, minute=0), 'end': at(hour=9, minute=0)}
    bus = Pin(
        origin=place('P'),
        destination=place('Q'),
        kind=SydneyMode.BUS,
        routes=('606',),
    )
    with opened(path=db) as repo:
        found = repo.departures(pin=bus, **window)
    assert [d.trip_id for d in found] == ['c']
