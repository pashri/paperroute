"""Cut the national rail timetable down to trains calling at Cambridge."""

import csv
import io
import logging
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

RAIL_PREFIX: Final[str] = 'rail:'
CAMBRIDGE_CRS: Final[frozenset[str]] = frozenset({'CBG', 'CMB', 'CMS'})
BADGES: Final[dict[str, str]] = {
    'LE': 'GA',
}  # timetable agency code to the badge shown
ZIP_TIME: Final[tuple[int, int, int, int, int, int]] = (
    1980, 1, 1, 0, 0, 0,
)  # every member's time
COPIED: Final[tuple[str, ...]] = ('feed_info.txt', 'attributions.txt')
STATION_ID: Final[re.Pattern[str]] = re.compile(r'rail:([A-Z]{3})')

Row = dict[str, str]


@dataclass(frozen=True)
class CutReport:
    """What a cut kept."""

    trips: int
    stops: int
    routes: int
    services: int
    shapes: int
    duplicates: int


@dataclass
class Kept:
    """The ids a cut has found it needs, and what it has written."""

    trips: set[str] = field(default_factory=set)
    stops: set[str] = field(default_factory=set)
    routes: set[str] = field(default_factory=set)
    services: set[str] = field(default_factory=set)
    shapes: set[str] = field(default_factory=set)
    agencies: set[str] = field(default_factory=set)
    stops_written: int = 0
    duplicates: int = 0


def station_id(*, crs: str) -> str:
    """
    Give the id a station has in the cut.

    Parameters
    ----------
    crs : str
        The station's three-letter code.

    Returns
    -------
    str
        The id, the code after the rail prefix.
    """
    return RAIL_PREFIX + crs


def crs_of(*, stop_id: str) -> str | None:
    """
    Give the station code in a stop id.

    Parameters
    ----------
    stop_id : str
        A stop id from any timetable.

    Returns
    -------
    str | None
        The three capital letters of a rail station's id, else None.
    """
    found = STATION_ID.fullmatch(stop_id)
    return found.group(1) if found else None


def trip_runs(*, rows: Iterable[Row]) -> Iterator[list[Row]]:
    """
    Group the rows of a stop times file by trip.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows, with each trip's rows together and trip ids rising.

    Yields
    ------
    list[dict[str, str]]
        The consecutive rows of one trip.

    Raises
    ------
    ValueError
        When a trip id is not greater than the one before.
    """
    run: list[Row] = []
    for row in rows:
        if run and row['trip_id'] != run[0]['trip_id']:
            if row['trip_id'] < run[0]['trip_id']:
                raise ValueError('stop_times.txt is not in trip order')
            yield run
            run = []
        run.append(row)
    if run:
        yield run


@contextmanager
def _open_rows(
    *, feed: zipfile.ZipFile, name: str,
) -> Iterator[csv.DictReader[str]]:
    """
    Open a member as a CSV reader.

    Parameters
    ----------
    feed : zipfile.ZipFile
        The archive.
    name : str
        The member's name.

    Yields
    ------
    csv.DictReader[str]
        The reader, whose field names are the member's header.
    """
    with feed.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding='utf-8-sig', newline='')
        yield csv.DictReader(text)


def _write_member(
    *, out: zipfile.ZipFile, name: str, fields: list[str],
    rows: Iterable[Row],
) -> None:
    """
    Write a member at the fixed time.

    Parameters
    ----------
    out : zipfile.ZipFile
        The archive being written.
    name : str
        The member's name.
    fields : list[str]
        The header.
    rows : Iterable[dict[str, str]]
        The rows.
    """
    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    with io.TextIOWrapper(
        out.open(info, 'w'), encoding='utf-8', newline='',
    ) as text:
        writer = csv.DictWriter(text, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _new_id(*, row: Row) -> str:
    """
    Give a stop the id it has in the cut.

    Parameters
    ----------
    row : dict[str, str]
        The stop's row.

    Returns
    -------
    str
        The station id for a station with a code, else the prefixed id.
    """
    if row.get('location_type') == '1' and row.get('stop_code'):
        return station_id(crs=row['stop_code'])
    return RAIL_PREFIX + row['stop_id']


def _load_stops(
    *, feed: zipfile.ZipFile, kept: Kept,
) -> tuple[list[str], dict[str, Row]]:
    """
    Read every stop, the first row of each id.

    Parameters
    ----------
    feed : zipfile.ZipFile
        The archive.
    kept : Kept
        Counts the repeated ids.

    Returns
    -------
    tuple[list[str], dict[str, dict[str, str]]]
        The header, and the rows by stop id in file order.
    """
    stops: dict[str, Row] = {}
    with _open_rows(feed=feed, name='stops.txt') as reader:
        for row in reader:
            kept.duplicates += row['stop_id'] in stops
            stops.setdefault(row['stop_id'], row)
        return list(reader.fieldnames or ()), stops


def _write_stop_times(
    *, feed: zipfile.ZipFile, out: zipfile.ZipFile,
    stops: dict[str, Row], kept: Kept, crs: frozenset[str],
) -> None:
    """
    Write the calls of every trip that calls at a watched stop.

    Parameters
    ----------
    feed : zipfile.ZipFile
        The archive.
    out : zipfile.ZipFile
        The archive being written.
    stops : dict[str, dict[str, str]]
        Every stop by id.
    kept : Kept
        Takes the trip and stop ids kept.
    crs : frozenset[str]
        The station codes to keep.
    """
    watched = {i for i, row in stops.items() if row.get('stop_code') in crs}
    new_ids = {stop_id: _new_id(row=row) for stop_id, row in stops.items()}
    with _open_rows(feed=feed, name='stop_times.txt') as reader:
        fields = list(reader.fieldnames or ())
        _write_member(
            out=out,
            name='stop_times.txt',
            fields=fields,
            rows=_calls(
                runs=trip_runs(rows=reader), watched=watched,
                new_ids=new_ids, kept=kept,
            ),
        )


def _calls(
    *, runs: Iterable[list[Row]], watched: set[str],
    new_ids: dict[str, str], kept: Kept,
) -> Iterator[Row]:
    """
    Yield the renamed calls of each run that touches a watched stop.

    Parameters
    ----------
    runs : Iterable[list[dict[str, str]]]
        Each trip's rows.
    watched : set[str]
        The stop ids to look for.
    new_ids : dict[str, str]
        Each stop's id in the cut.
    kept : Kept
        Takes the trip and stop ids kept.

    Yields
    ------
    dict[str, str]
        One call, its trip and stop ids renamed.
    """
    for run in runs:
        if not any(row['stop_id'] in watched for row in run):
            continue
        kept.trips.add(run[0]['trip_id'])
        for row in run:
            kept.stops.add(row['stop_id'])
            yield {
                **row,
                'trip_id': RAIL_PREFIX + row['trip_id'],
                'stop_id': new_ids.get(
                    row['stop_id'], RAIL_PREFIX + row['stop_id'],
                ),
            }


def _first_rows(
    *, rows: Iterable[Row], column: str, wanted: set[str], kept: Kept,
) -> Iterator[Row]:
    """
    Yield the first row of each wanted id.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows.
    column : str
        The column holding the id.
    wanted : set[str]
        The ids to keep.
    kept : Kept
        Counts the repeated ids.

    Yields
    ------
    dict[str, str]
        A row whose id is wanted and not seen before.
    """
    seen: set[str] = set()
    for row in rows:
        if row[column] not in wanted:
            continue
        kept.duplicates += row[column] in seen
        if row[column] not in seen:
            seen.add(row[column])
            yield row


def _matching(
    *, rows: Iterable[Row], column: str, wanted: set[str],
) -> Iterator[Row]:
    """
    Yield the rows whose id is wanted.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows.
    column : str
        The column holding the id.
    wanted : set[str]
        The ids to keep.

    Yields
    ------
    dict[str, str]
        A row whose id is wanted.
    """
    for row in rows:
        if row[column] in wanted:
            yield row


def _trips(*, rows: Iterable[Row], kept: Kept) -> Iterator[Row]:
    """
    Yield the kept trips, renamed, noting the routes, services and shapes.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows of trips.txt.
    kept : Kept
        Holds the trip ids; takes the others.

    Yields
    ------
    dict[str, str]
        One trip with its id prefixed.
    """
    firsts = _first_rows(
        rows=rows, column='trip_id', wanted=kept.trips, kept=kept,
    )
    for row in firsts:
        kept.routes.add(row['route_id'])
        kept.services.add(row['service_id'])
        kept.shapes.add(row.get('shape_id', ''))
        yield {**row, 'trip_id': RAIL_PREFIX + row['trip_id']}


def _routes(*, rows: Iterable[Row], kept: Kept) -> Iterator[Row]:
    """
    Yield the kept routes with the operator's badge as the short name.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows of routes.txt.
    kept : Kept
        Holds the route ids; takes the agency ids.

    Yields
    ------
    dict[str, str]
        One route, its agency id without the leading equals sign.
    """
    firsts = _first_rows(
        rows=rows, column='route_id', wanted=kept.routes, kept=kept,
    )
    for row in firsts:
        code = row['agency_id'].removeprefix('=')
        kept.agencies.add(code)
        yield {
            **row,
            'agency_id': code,
            'route_short_name': BADGES.get(code, code),
        }


def _agencies(*, rows: Iterable[Row], kept: Kept) -> Iterator[Row]:
    """
    Yield the agencies of the kept routes.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows of agency.txt.
    kept : Kept
        Holds the agency ids.

    Yields
    ------
    dict[str, str]
        One agency, its id without the leading equals sign.
    """
    for row in rows:
        code = row['agency_id'].removeprefix('=')
        if code in kept.agencies:
            yield {**row, 'agency_id': code}


def _stop_rows(
    *, stops: dict[str, Row], kept: Kept,
) -> Iterator[Row]:
    """
    Yield the used stops and their parents, renamed, in file order.

    Parameters
    ----------
    stops : dict[str, dict[str, str]]
        Every stop by id.
    kept : Kept
        Holds the stop ids used.

    Yields
    ------
    dict[str, str]
        One stop, its id and parent renamed.
    """
    new_ids = {stop_id: _new_id(row=row) for stop_id, row in stops.items()}
    parents = {stops[i].get('parent_station', '') for i in kept.stops}
    for stop_id, row in stops.items():
        if stop_id in kept.stops or stop_id in parents:
            parent = row.get('parent_station', '')
            kept.stops_written += 1
            yield {
                **row,
                'stop_id': new_ids[stop_id],
                'parent_station': new_ids.get(parent, parent),
            }


def _services(*, rows: Iterable[Row], kept: Kept) -> Iterator[Row]:
    """
    Yield the rows of the kept services.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows of calendar.txt or calendar_dates.txt.
    kept : Kept
        Holds the service ids.

    Returns
    -------
    Iterator[dict[str, str]]
        The rows of the kept services.
    """
    return _matching(rows=rows, column='service_id', wanted=kept.services)


def _shapes(*, rows: Iterable[Row], kept: Kept) -> Iterator[Row]:
    """
    Yield the rows of the kept shapes.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        The rows of shapes.txt.
    kept : Kept
        Holds the shape ids.

    Returns
    -------
    Iterator[dict[str, str]]
        The rows of the kept shapes.
    """
    return _matching(rows=rows, column='shape_id', wanted=kept.shapes)


MEMBERS: Final[
    tuple[tuple[str, Callable[..., Iterable[Row]]], ...]
] = (
    ('trips.txt', _trips),
    ('routes.txt', _routes),
    ('agency.txt', _agencies),
    ('calendar.txt', _services),
    ('calendar_dates.txt', _services),
    ('shapes.txt', _shapes),
)  # members cut by the ids kept, in the order written


def _write_cut_member(
    *, feed: zipfile.ZipFile, out: zipfile.ZipFile, name: str,
    cut: Callable[..., Iterable[Row]] | None, kept: Kept,
) -> None:
    """
    Write one member from the archive, if it has one.

    Parameters
    ----------
    feed : zipfile.ZipFile
        The archive.
    out : zipfile.ZipFile
        The archive being written.
    name : str
        The member's name.
    cut : Callable[..., Iterable[dict[str, str]]] | None
        Cuts the rows by the ids kept; None copies them as they are.
    kept : Kept
        The ids the member is cut by.
    """
    if name not in feed.namelist():
        return
    with _open_rows(feed=feed, name=name) as reader:
        _write_member(
            out=out,
            name=name,
            fields=list(reader.fieldnames or ()),
            rows=reader if cut is None else cut(rows=reader, kept=kept),
        )


def _write_members(
    *, feed: zipfile.ZipFile, out: zipfile.ZipFile,
    stops: tuple[list[str], dict[str, Row]], kept: Kept,
) -> None:
    """
    Write every member after the stop times.

    Parameters
    ----------
    feed : zipfile.ZipFile
        The archive.
    out : zipfile.ZipFile
        The archive being written.
    stops : tuple[list[str], dict[str, dict[str, str]]]
        The stops' header and rows.
    kept : Kept
        The ids the members are cut by.
    """
    for name, cut in MEMBERS:
        _write_cut_member(feed=feed, out=out, name=name, cut=cut, kept=kept)
    fields, rows = stops
    _write_member(
        out=out,
        name='stops.txt',
        fields=fields,
        rows=_stop_rows(stops=rows, kept=kept),
    )
    for name in COPIED:
        _write_cut_member(feed=feed, out=out, name=name, cut=None, kept=kept)


def cut_rail(
    *, archive: Path, out: Path, crs: frozenset[str] = CAMBRIDGE_CRS,
) -> CutReport:
    """
    Write a zip of the trips that call at the stations, from the national one.

    Trip, stop, route and station ids gain the rail prefix; stations are
    named by their code. The output is the same bytes for the same input.

    Parameters
    ----------
    archive : Path
        The national timetable zip, read member by member.
    out : Path
        The zip to write; removed again when the cut fails.
    crs : frozenset[str]
        The station codes to keep.

    Returns
    -------
    CutReport
        What the cut kept.

    Raises
    ------
    ValueError
        When stop_times.txt is out of trip order, or no trip calls at the
        stations.
    """
    kept = Kept()
    try:
        with zipfile.ZipFile(archive) as feed:
            with zipfile.ZipFile(out, 'w') as written:
                _cut(feed=feed, out=written, kept=kept, crs=crs)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    if kept.duplicates:
        logger.warning(
            'rail: %d duplicate ids, the first of each kept', kept.duplicates,
        )
    return CutReport(
        trips=len(kept.trips),
        stops=kept.stops_written,
        routes=len(kept.routes),
        services=len(kept.services),
        shapes=len(kept.shapes),
        duplicates=kept.duplicates,
    )


def _cut(
    *, feed: zipfile.ZipFile, out: zipfile.ZipFile, kept: Kept,
    crs: frozenset[str],
) -> None:
    """
    Write the cut's members.

    Parameters
    ----------
    feed : zipfile.ZipFile
        The archive.
    out : zipfile.ZipFile
        The archive being written.
    kept : Kept
        Takes the ids and counts of the cut.
    crs : frozenset[str]
        The station codes to keep.

    Raises
    ------
    ValueError
        When no trip calls at the stations.
    """
    fields, stops = _load_stops(feed=feed, kept=kept)
    _write_stop_times(feed=feed, out=out, stops=stops, kept=kept, crs=crs)
    if not kept.trips:
        raise ValueError('no trip calls at the stations')
    _write_members(feed=feed, out=out, stops=(fields, stops), kept=kept)
