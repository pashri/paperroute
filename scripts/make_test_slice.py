"""Cut a small real slice of GTFS and realtime data for tests."""

import csv
import io
import sys
import zipfile
from pathlib import Path
from typing import Final

from google.transit import gtfs_realtime_pb2

from paperroute.build.gtfs_files import read_rows

BUS_ROUTES: Final[set[str]] = {'606'}
RAIL_ROUTE: Final[str] = 'T1'
STATIONS: Final[set[str]] = {'215020', '200060'}
REGIONAL_AGENCY: Final[str] = '711'
REGIONAL_COACHES: Final[int] = 3
XPT_LINES: Final[set[str]] = {'NRC', 'NRW', 'STH', 'WST'}


def kept_trips(*, archive: Path) -> set[str]:
    """
    Pick the slice's trips: buses, XPTs, and T1 trips calling at both stations.

    Parameters
    ----------
    archive : Path
        Source zip.

    Returns
    -------
    set[str]
        Trip IDs to keep.
    """
    names = {r['route_id']: r['route_short_name']
             for r in read_rows(archive=archive, member='routes.txt')}
    parents = {s['stop_id']: s.get('parent_station', '')
               for s in read_rows(archive=archive, member='stops.txt')}
    trips = {t['trip_id']
             for t in read_rows(archive=archive, member='trips.txt')
             if names[t['route_id']] in BUS_ROUTES | {RAIL_ROUTE}}
    seen: dict[str, set[str]] = {}
    for stop_time in read_rows(archive=archive, member='stop_times.txt'):
        if stop_time['trip_id'] in trips:
            stop = parents.get(stop_time['stop_id'], '')
            seen.setdefault(stop_time['trip_id'], set()).add(stop)
    whole = {t['trip_id']
             for t in read_rows(archive=archive, member='trips.txt')
             if names[t['route_id']] in BUS_ROUTES | XPT_LINES}
    return whole | {trip for trip, stations in seen.items()
                    if STATIONS <= stations}


def regional_trips(*, archive: Path, recording: Path) -> set[str]:
    """
    Pick NSW TrainLink trips a recording mentions: every XPT, a few coaches.

    Parameters
    ----------
    archive : Path
        Bundle zip.
    recording : Path
        Recorded nswtrains trip updates.

    Returns
    -------
    set[str]
        Trip IDs to keep.
    """
    routes = {r['route_id']: (r.get('agency_id', ''), r['route_type'])
              for r in read_rows(archive=archive, member='routes.txt')}
    kinds = {t['trip_id']: routes[t['route_id']][1]
             for t in read_rows(archive=archive, member='trips.txt')
             if routes[t['route_id']][0] == REGIONAL_AGENCY}
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(recording.read_bytes())
    live = sorted({entity.trip_update.trip.trip_id
                   for entity in feed.entity} & set(kinds))
    trains = {trip for trip in live if kinds[trip] == '106'}
    coaches = [trip for trip in live if kinds[trip] != '106']
    return trains | set(coaches[:REGIONAL_COACHES])


def write_slice(*, source: Path, out: Path, trips: set[str]) -> None:
    """
    Copy a zip keeping only rows linked to the given trips.

    Parameters
    ----------
    source : Path
        Source zip.
    out : Path
        Slice zip to write.
    trips : set[str]
        Trips to keep.
    """
    trip_rows = [t for t in read_rows(archive=source, member='trips.txt')
                 if t['trip_id'] in trips]
    times = [s for s in read_rows(archive=source, member='stop_times.txt')
             if s['trip_id'] in trips]
    stop_ids = {s['stop_id'] for s in times}
    stops = [s for s in read_rows(archive=source, member='stops.txt')
             if s['stop_id'] in stop_ids]
    stop_ids |= {s['parent_station'] for s in stops
                 if s.get('parent_station')}
    services = {t['service_id'] for t in trip_rows}
    route_ids = {t['route_id'] for t in trip_rows}
    tables = {
        'stops.txt': [s for s in read_rows(archive=source,
                                           member='stops.txt')
                      if s['stop_id'] in stop_ids],
        'routes.txt': [r for r in read_rows(archive=source,
                                            member='routes.txt')
                       if r['route_id'] in route_ids],
        'calendar.txt': [c for c in read_rows(archive=source,
                                              member='calendar.txt')
                         if c['service_id'] in services],
        'calendar_dates.txt': [
            c for c in read_rows(archive=source,
                                 member='calendar_dates.txt')
            if c['service_id'] in services],
        'trips.txt': trip_rows, 'stop_times.txt': times}
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, table in tables.items():
            if table:
                buffer = io.StringIO()
                writer = csv.DictWriter(buffer, fieldnames=list(table[0]))
                writer.writeheader()
                writer.writerows(table)
                archive.writestr(name, buffer.getvalue())


def write_feed_slice(*, source: Path, out: Path, trips: set[str]) -> None:
    """
    Copy a realtime feed keeping only entities for the given trips.

    Parameters
    ----------
    source : Path
        Recorded .pb file.
    out : Path
        Slice file to write.
    trips : set[str]
        Trip IDs to keep.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(source.read_bytes())
    kept = gtfs_realtime_pb2.FeedMessage()
    kept.header.CopyFrom(feed.header)
    for entity in feed.entity:
        if entity.trip_update.trip.trip_id in trips:
            kept.entity.add().CopyFrom(entity)
    out.write_bytes(kept.SerializeToString())
    print(f'{out.name}: {len(kept.entity)} entities')


def add_shapes(*, source: Path, fixture: Path) -> int:
    """
    Append the shapes a fixture's trips name to the fixture zip.

    Parameters
    ----------
    source : Path
        Full GTFS zip holding shapes.txt, which is streamed.
    fixture : Path
        Existing slice zip; its other members are left as they are.

    Returns
    -------
    int
        Shape points written.

    Raises
    ------
    ValueError
        When the fixture already has a shapes.txt or its trips.txt has no
        shape_id column.
    """
    with zipfile.ZipFile(fixture) as archive:
        if 'shapes.txt' in archive.namelist():
            raise ValueError(f'{fixture.name} already has shapes.txt')
    wanted = {t.get('shape_id', '')
              for t in read_rows(archive=fixture, member='trips.txt')}
    if not any(wanted) and not any(
            'shape_id' in t
            for t in read_rows(archive=fixture, member='trips.txt')):
        raise ValueError(f'{fixture.name} trips.txt has no shape_id')
    wanted.discard('')
    rows = (r for r in read_rows(archive=source, member='shapes.txt')
            if r['shape_id'] in wanted)
    first = next(rows)
    count = 1
    with zipfile.ZipFile(fixture, 'a', zipfile.ZIP_DEFLATED) as archive:
        with archive.open('shapes.txt', 'w') as raw:
            text = io.TextIOWrapper(raw, encoding='utf-8', newline='')
            writer = csv.DictWriter(text, fieldnames=list(first))
            writer.writeheader()
            writer.writerow(first)
            for row in rows:
                writer.writerow(row)
                count += 1
            text.flush()
            text.detach()
    return count


def main() -> None:
    """Slice data/raw and data/rt (argv[1] repo root) into the fixtures."""
    root = Path(sys.argv[1])
    out = root / 'tests' / 'fixtures' / 'real'
    out.mkdir(parents=True, exist_ok=True)
    bundle = root / 'data/raw/complete.zip'
    trains = root / 'data/raw/st/sydneytrains.zip'
    nswtrains = root / 'data/rt/v1_gtfs_realtime_nswtrains.pb'
    regional = regional_trips(archive=bundle, recording=nswtrains)
    bundle_trips = kept_trips(archive=bundle)
    train_trips = kept_trips(archive=trains)
    write_slice(source=bundle, out=out / 'bundle.zip',
                trips=bundle_trips | regional)
    write_slice(source=trains, out=out / 'sydneytrains.zip',
                trips=train_trips)
    write_feed_slice(source=root / 'data/rt/v1_gtfs_realtime_buses.pb',
                     out=out / 'v1_gtfs_realtime_buses.pb',
                     trips=bundle_trips)
    write_feed_slice(
        source=root / 'data/rt/v2_gtfs_realtime_sydneytrains.pb',
        out=out / 'v2_gtfs_realtime_sydneytrains.pb', trips=train_trips)
    write_feed_slice(source=nswtrains,
                     out=out / 'v1_gtfs_realtime_nswtrains.pb',
                     trips=regional)


if __name__ == '__main__':
    main()
