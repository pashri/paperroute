"""Cut a small real slice of GTFS and realtime data for tests."""

import csv
import io
import os
import shutil
import sys
import zipfile
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Final, TextIO

from google.transit import gtfs_realtime_pb2

from paperroute_transit.build.gtfs_files import read_rows

BUS_ROUTES: Final[set[str]] = {'606'}
RAIL_ROUTE: Final[str] = 'T1'
STATIONS: Final[set[str]] = {'215020', '200060'}
REGIONAL_AGENCY: Final[str] = '711'
REGIONAL_COACHES: Final[int] = 3
XPT_LINES: Final[set[str]] = {'NRC', 'NRW', 'STH', 'WST'}
CARRY_ON_TRIPS: Final[set[str]] = {  # a T1 to Central and the trip it becomes
    '169C.947.173.64.A.8.91436348',
    '169D.947.173.64.A.8.91436347',
}


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
    tables = slice_tables(source=source, trips=trips)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, table in tables.items():
            if table:
                archive.writestr(name, csv_text(table=table))


def rows_where(*, archive: Path, member: str, key: str,
               keep: Collection[str]) -> list[dict[str, str]]:
    """
    Read the rows of a member whose column is in a set.

    Parameters
    ----------
    archive : Path
        GTFS zip.
    member : str
        File inside the zip.
    key : str
        Column compared.
    keep : Collection[str]
        Values kept.

    Returns
    -------
    list[dict[str, str]]
        The rows kept, in order.
    """
    return [row for row in read_rows(archive=archive, member=member)
            if row[key] in keep]


def slice_tables(*, source: Path,
                 trips: set[str]) -> dict[str, list[dict[str, str]]]:
    """
    Pick the rows of each table linked to the given trips.

    Parameters
    ----------
    source : Path
        Source zip.
    trips : set[str]
        Trips to keep.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Rows by member name; stops include their parent stations.
    """
    trip_rows = rows_where(archive=source, member='trips.txt',
                           key='trip_id', keep=trips)
    times = rows_where(archive=source, member='stop_times.txt',
                       key='trip_id', keep=trips)
    stop_ids = {s['stop_id'] for s in times}
    stop_ids |= {s['parent_station']
                 for s in rows_where(archive=source, member='stops.txt',
                                     key='stop_id', keep=stop_ids)
                 if s.get('parent_station')}
    services = {t['service_id'] for t in trip_rows}
    return {
        'stops.txt': rows_where(archive=source, member='stops.txt',
                                key='stop_id', keep=stop_ids),
        'routes.txt': rows_where(
            archive=source, member='routes.txt', key='route_id',
            keep={t['route_id'] for t in trip_rows}),
        'calendar.txt': rows_where(archive=source, member='calendar.txt',
                                   key='service_id', keep=services),
        'calendar_dates.txt': rows_where(
            archive=source, member='calendar_dates.txt', key='service_id',
            keep=services),
        'trips.txt': trip_rows, 'stop_times.txt': times}


def csv_text(*, table: list[dict[str, str]]) -> str:
    """
    Write rows as CSV with a header from the first row.

    Parameters
    ----------
    table : list[dict[str, str]]
        Rows; at least one.

    Returns
    -------
    str
        The CSV text.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    return buffer.getvalue()


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


def named_shapes(*, fixture: Path) -> set[str]:
    """
    List the shape IDs a fixture's trips name.

    Parameters
    ----------
    fixture : Path
        Slice zip.

    Returns
    -------
    set[str]
        Shape IDs.

    Raises
    ------
    ValueError
        When trips.txt has no shape_id column or no trip names a shape.
    """
    named: set[str] = set()
    for trip in read_rows(archive=fixture, member='trips.txt'):
        if 'shape_id' not in trip:
            raise ValueError(f'{fixture.name} trips.txt has no shape_id')
        named.add(trip['shape_id'])
    named.discard('')
    if not named:
        raise ValueError(f'{fixture.name} trips name no shapes')
    return named


def append_shapes(*, source: Path, target: Path, wanted: set[str]) -> int:
    """
    Stream the wanted shapes from a source zip into a zip's shapes.txt.

    Parameters
    ----------
    source : Path
        Full GTFS zip holding shapes.txt.
    target : Path
        Zip to append to.
    wanted : set[str]
        Shape IDs to copy.

    Returns
    -------
    int
        Shape points written.

    Raises
    ------
    ValueError
        When the source holds none of the wanted shapes.
    """
    rows = (row for row in read_rows(archive=source, member='shapes.txt')
            if row['shape_id'] in wanted)
    with zipfile.ZipFile(target, 'a', zipfile.ZIP_DEFLATED) as archive:
        with archive.open('shapes.txt', 'w') as raw:
            text = io.TextIOWrapper(raw, encoding='utf-8', newline='')
            count = write_rows(text=text, rows=rows)
            text.flush()
            text.detach()
    if not count:
        raise ValueError(f'{source.name} holds none of the shapes')
    return count


def write_rows(*, text: TextIO, rows: Iterable[dict[str, str]]) -> int:
    """
    Write rows as CSV, with a header from the first row.

    Parameters
    ----------
    text : TextIO
        Where to write.
    rows : Iterable[dict[str, str]]
        Rows.

    Returns
    -------
    int
        Rows written; nothing, not even a header, for no rows.
    """
    count = 0
    writer: csv.DictWriter[str] | None = None
    for row in rows:
        if writer is None:
            writer = csv.DictWriter(text, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)
        count += 1
    return count


def add_shapes(*, source: Path, fixture: Path,
               shape_ids: Collection[str] | None = None) -> int:
    """
    Append the shapes a fixture's trips name to the fixture zip.

    The fixture is rewritten through a copy, so a failure leaves it as it
    was.

    Parameters
    ----------
    source : Path
        Full GTFS zip holding shapes.txt, which is streamed.
    fixture : Path
        Existing slice zip; its other members are left as they are.
    shape_ids : Collection[str] | None
        When given, only these of the named shapes are copied.

    Returns
    -------
    int
        Shape points written.

    Raises
    ------
    ValueError
        When the fixture already has a shapes.txt, its trips name no
        shapes, or the source holds none of them.
    """
    wanted = wanted_shapes(fixture=fixture, shape_ids=shape_ids)
    staged = fixture.with_name(f'{fixture.name}.new')
    shutil.copyfile(fixture, staged)
    try:
        count = append_shapes(source=source, target=staged, wanted=wanted)
        os.replace(staged, fixture)
    finally:
        staged.unlink(missing_ok=True)
    return count


def wanted_shapes(*, fixture: Path,
                  shape_ids: Collection[str] | None) -> set[str]:
    """
    List the shapes to add to a fixture that has none yet.

    Parameters
    ----------
    fixture : Path
        Slice zip.
    shape_ids : Collection[str] | None
        When given, only these of the named shapes are wanted.

    Returns
    -------
    set[str]
        Shape IDs.

    Raises
    ------
    ValueError
        When the fixture already has a shapes.txt or its trips name no
        shapes.
    """
    with zipfile.ZipFile(fixture) as archive:
        if 'shapes.txt' in archive.namelist():
            raise ValueError(f'{fixture.name} already has shapes.txt')
    wanted = named_shapes(fixture=fixture)
    if shape_ids is not None:
        wanted &= set(shape_ids)
    return wanted


def sample_shapes(*, archive: Path) -> set[str]:
    """
    Pick the shapes the bundle fixture keeps.

    Parameters
    ----------
    archive : Path
        Bundle slice zip.

    Returns
    -------
    set[str]
        Every 606 shape, and the first T1 and regional shape by ID.
    """
    routes = {r['route_id']: (r['route_short_name'], r.get('agency_id', ''))
              for r in read_rows(archive=archive, member='routes.txt')}
    shaped = [(routes[t['route_id']], t['shape_id'])
              for t in read_rows(archive=archive, member='trips.txt')
              if t.get('shape_id')]
    kept = {shape for (name, _), shape in shaped if name in BUS_ROUTES}
    first: dict[str, str] = {}
    for (name, agency), shape in shaped:
        if name == RAIL_ROUTE or (
            agency == REGIONAL_AGENCY and name not in BUS_ROUTES
        ):
            key = RAIL_ROUTE if name == RAIL_ROUTE else agency
            first[key] = min(shape, first.get(key, shape))
    return kept | set(first.values())


def main() -> None:  # pylint: disable=too-many-statements
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
    write_slice(source=trains, out=out / 'carry_on.zip',
                trips=CARRY_ON_TRIPS)
    add_shapes(source=bundle, fixture=out / 'bundle.zip',
               shape_ids=sample_shapes(archive=out / 'bundle.zip'))
    add_shapes(source=trains, fixture=out / 'sydneytrains.zip')
    for name in ('v1_gtfs_vehiclepos_buses.pb',
                 'v2_gtfs_vehiclepos_sydneytrains.pb'):
        shutil.copyfile(root / 'data/rt' / name, out / name)
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
