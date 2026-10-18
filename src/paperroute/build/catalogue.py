"""Build the route chooser's catalogue: route lists, tags and towards rows."""

import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from typing import Final

from paperroute.build.gtfs_files import insert_many
from paperroute.build.route_tags import CatalogueRoute, agency_towns, tags_of
from paperroute.core.geo import Point, to_metres
from paperroute.core.model import INTERCITY, RouteKind, kind_for

CATALOGUE_SCHEMA: Final[str] = """
CREATE TABLE chooser_routes (route_key TEXT PRIMARY KEY, kind TEXT NOT NULL,
  grp TEXT NOT NULL, grp_order INTEGER NOT NULL, number TEXT NOT NULL,
  sort INTEGER NOT NULL, name TEXT NOT NULL, tag TEXT NOT NULL,
  line TEXT NOT NULL);
CREATE TABLE chooser_towards (towards_id INTEGER PRIMARY KEY,
  route_key TEXT NOT NULL, headsign TEXT NOT NULL, trips INTEGER NOT NULL,
  stations TEXT NOT NULL);
CREATE INDEX chooser_towards_route ON chooser_towards (route_key, trips);
"""
REPLACEMENT: Final[re.Pattern[str]] = re.compile(
    r'^\d+[A-Z]?(T\d|BM|CN|HU|SC|SH)',
)
REPLACEMENT_LINES: Final[dict[str, str]] = {
    'BM': 'BMT', 'CN': 'CCN', 'HU': 'HUN', 'SC': 'SCO', 'SH': 'SHL',
}
FIRST_HUNDRED: Final[str] = '1\u201399'
LETTERED: Final[int] = 10**6
NO_NUMBER: Final[int] = 10**9
LINE_SORT: Final[dict[str, int]] = {
    **{f'T{line}': line for line in range(1, 10)},
    **{code: 101 + index for index, code in enumerate(INTERCITY)},
}
OTHER_LINES: Final[int] = 200
LINE_KINDS: Final[frozenset[RouteKind]] = frozenset(
    {RouteKind.TRAIN, RouteKind.METRO, RouteKind.LIGHT_RAIL},
)
TAGGED_KINDS: Final[frozenset[RouteKind]] = frozenset(
    {RouteKind.BUS, RouteKind.SCHOOL},
)
LEADING_DIGITS: Final[re.Pattern[str]] = re.compile(r'^\d+')
NATURAL_PARTS: Final[re.Pattern[str]] = re.compile(r'\d+|\D+')
ROUTES: Final[str] = """
SELECT r.route_id, r.short_name, r.long_name, r.type, r.agency_id,
  SUM(p.trips)
FROM routes r JOIN route_patterns p USING (route_id)
GROUP BY r.route_id ORDER BY r.route_id
"""
BUNDLE_NAMES: Final[str] = """
SELECT short_name, long_name, type, agency_id FROM routes
WHERE source = 'bundle' ORDER BY route_id
"""
KEYS_TABLE: Final[str] = (
    'CREATE TEMP TABLE catalogue_keys (route_id TEXT PRIMARY KEY, '
    'route_key TEXT NOT NULL) WITHOUT ROWID'
)
MIDDLES: Final[str] = """
SELECT k.route_key, s.lon, s.lat
FROM (
  SELECT DISTINCT k.route_key, j.value AS station
  FROM route_patterns p JOIN temp.catalogue_keys k USING (route_id),
    json_each(p.stations) j
) k JOIN stops s ON s.stop_id = k.station
WHERE s.lon IS NOT NULL AND s.lat IS NOT NULL
ORDER BY k.route_key
"""
TOWARDS: Final[str] = """
SELECT k.route_key, p.headsign, p.stations, SUM(p.trips)
FROM route_patterns p JOIN temp.catalogue_keys k USING (route_id)
  JOIN chooser_routes c ON c.route_key = k.route_key
GROUP BY k.route_key, p.headsign, p.stations
ORDER BY k.route_key, p.headsign, p.stations
"""
type Merge = tuple[RouteKind, str, str]
type RouteRow = tuple[str, str, str, int, str, int, str, str, str]


@dataclass(frozen=True)
class RouteSource:
    """One route ID with patterns, the merged route it joins and its trips."""

    route_id: str
    merge: Merge
    key: str
    agency: str
    trips: int


def bucket_of(*, number: str) -> str:
    """
    Put a route number in its hundreds group.

    Parameters
    ----------
    number : str
        Route short name.

    Returns
    -------
    str
        '1-99' (with an en dash), '600s', or up to two leading letters of
        a lettered route.
    """
    lead = LEADING_DIGITS.match(number)
    if lead is None:
        letters = number.rstrip('0123456789').rstrip()
        lead_letters = ''.join(char for char in letters if char.isalpha())
        return lead_letters[:2] or number
    hundreds = int(lead.group()) // 100 * 100
    return f'{hundreds}s' if hundreds else FIRST_HUNDRED


def bucket_order(*, bucket: str) -> int:
    """
    Sort numbered groups first in number order, then lettered ones.

    Parameters
    ----------
    bucket : str
        Group label from bucket_of.

    Returns
    -------
    int
        Sort key; lettered groups sort by their first four bytes.
    """
    if bucket == FIRST_HUNDRED:
        return 0
    if bucket.endswith('s') and bucket[:-1].isdigit():
        return int(bucket[:-1])
    head = bucket.encode()[:4].ljust(4, b'\0')
    return LETTERED + int.from_bytes(head)


def route_order(
    *, number: str,
) -> tuple[int, tuple[tuple[int, int | str], ...]]:
    """
    Sort route names in number order, then in natural order.

    Parameters
    ----------
    number : str
        Route short name.

    Returns
    -------
    tuple[int, tuple[tuple[int, int | str], ...]]
        The leading number (names without one last), then the name's
        digit and text runs, digits compared as numbers.
    """
    lead = LEADING_DIGITS.match(number)
    parts = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in NATURAL_PARTS.findall(number)
    )
    return (NO_NUMBER if lead is None else int(lead.group())), parts


def line_name(*, long_name: str, code: str) -> str:
    """
    Tidy a line's long name for the chooser.

    Parameters
    ----------
    long_name : str
        Route long name, such as 'T1 North Shore and Western Line'.
    code : str
        Line code, such as 'T1'.

    Returns
    -------
    str
        The name without a leading code or trailing ' Line', with ' & '
        for ' and '.
    """
    name = long_name.strip().removeprefix(f'{code} ').removesuffix(' Line')
    return name.replace(' and ', ' & ')


def route_sources(*, conn: sqlite3.Connection) -> list[RouteSource]:
    """
    Read every route ID with route patterns and a kind.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.

    Returns
    -------
    list[RouteSource]
        Routes in route ID order.
    """
    found = []
    for route_id, number, long_name, route_type, agency, trips in (
        conn.execute(ROUTES)
    ):
        kind = kind_for(
            route_type=route_type, agency_id=agency, short_name=number,
        )
        if kind is not None:
            found.append(RouteSource(
                route_id=route_id, merge=(kind, number, long_name),
                key=f'{kind}:{number}:{long_name}', agency=agency or '',
                trips=trips,
            ))
    return found


def write_keys(
    *, conn: sqlite3.Connection, keys: Iterable[tuple[str, str]],
) -> None:
    """
    Fill a fresh temporary table of route keys by route ID.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    keys : Iterable[tuple[str, str]]
        Route ID and key.
    """
    conn.execute('DROP TABLE IF EXISTS temp.catalogue_keys')
    conn.execute(KEYS_TABLE)
    insert_many(conn=conn, sql='INSERT INTO temp.catalogue_keys VALUES (?, ?)',
                rows=keys)


def middles(
    *, conn: sqlite3.Connection, sources: Sequence[RouteSource],
) -> dict[str, Point]:
    """
    Find each merged route's median station point.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    sources : Sequence[RouteSource]
        Route IDs to merge.

    Returns
    -------
    dict[str, Point]
        Metres by merged key; routes with no placed stations are missing.
    """
    write_keys(conn=conn, keys=(
        (source.route_id, source.key) for source in sources
    ))
    found = {}
    for key, rows in groupby(conn.execute(MIDDLES), key=itemgetter(0)):
        spots = [(lon, lat) for _, lon, lat in rows]
        found[key] = to_metres(
            lon=statistics.median(lon for lon, _ in spots),
            lat=statistics.median(lat for _, lat in spots),
        )
    return found


def busiest_agencies(*, sources: Sequence[RouteSource]) -> dict[str, str]:
    """
    Pick the operator running the most trips of each merged route.

    Parameters
    ----------
    sources : Sequence[RouteSource]
        Route IDs.

    Returns
    -------
    dict[str, str]
        Operator by merged key; ties go to the lowest ID.
    """
    trips: dict[str, Counter[str]] = defaultdict(Counter)
    for source in sources:
        trips[source.key][source.agency] += source.trips
    return {
        key: min(counts.items(), key=lambda item: (-item[1], item[0]))[0]
        for key, counts in trips.items()
    }


def merged_routes(
    *, conn: sqlite3.Connection, sources: Sequence[RouteSource] | None = None,
) -> list[CatalogueRoute]:
    """
    Merge the route IDs of each kind, number and long name.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    sources : Sequence[RouteSource] | None
        Route IDs from route_sources; None to read them.

    Returns
    -------
    list[CatalogueRoute]
        One route per kind, number and long name with route patterns, in
        kind, number and long name order.
    """
    sources = route_sources(conn=conn) if sources is None else sources
    points = middles(conn=conn, sources=sources)
    agencies = busiest_agencies(sources=sources)
    routes = {
        source.key: CatalogueRoute(
            route_key=source.key, kind=source.merge[0],
            number=source.merge[1], long_name=source.merge[2],
            agency=agencies[source.key], middle=points.get(source.key),
        )
        for source in sources
    }
    return sorted(routes.values(), key=lambda route: (
        route.kind, route_order(number=route.number), route.long_name,
    ))


def bundle_names(*, conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """
    Read the bundle's first long name for each line.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    dict[tuple[str, str], str]
        Long name by kind and number.
    """
    found: dict[tuple[str, str], str] = {}
    for number, long_name, route_type, agency in conn.execute(BUNDLE_NAMES):
        kind = kind_for(
            route_type=route_type, agency_id=agency, short_name=number,
        )
        if kind in LINE_KINDS:
            found.setdefault((str(kind), number), long_name)
    return found


@dataclass(frozen=True)
class Entry:
    """One chooser row before sorting: its key, names, tag and line."""

    key: str
    kind: RouteKind
    number: str
    name: str
    tag: str
    line: str


def line_entries(
    *, routes: Sequence[CatalogueRoute], names: Mapping[tuple[str, str], str],
) -> Iterator[Entry]:
    """
    Make one entry per train, metro and light rail line.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Merged routes of the line kinds.
    names : Mapping[tuple[str, str], str]
        Bundle long names by kind and number.

    Yields
    ------
    Entry
        One per kind and number.
    """
    firsts: dict[str, CatalogueRoute] = {}
    for route in routes:
        firsts.setdefault(f'{route.kind}:{route.number}', route)
    for key, route in firsts.items():
        long_name = names.get((str(route.kind), route.number), route.long_name)
        yield Entry(
            key=key, kind=route.kind, number=route.number,
            name=line_name(long_name=long_name, code=route.number),
            tag='', line='',
        )


def route_entries(
    *, routes: Sequence[CatalogueRoute], tags: Mapping[str, str],
) -> Iterator[Entry]:
    """
    Make one entry per merged route of the other kinds.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Merged routes outside the line kinds.
    tags : Mapping[str, str]
        Tags by route key.

    Yields
    ------
    Entry
        One per kept route; replacement buses without a line are dropped.
    """
    for route in routes:
        line = ''
        if route.kind is RouteKind.REPLACEMENT:
            if (found := REPLACEMENT.match(route.number)) is None:
                continue
            line = REPLACEMENT_LINES.get(found.group(1), found.group(1))
        yield Entry(
            key=route.route_key, kind=route.kind, number=route.number,
            name=route.long_name, tag=tags.get(route.route_key, ''),
            line=line,
        )


def kind_rows(*, entries: Sequence[Entry]) -> Iterator[RouteRow]:
    """
    Sort one kind's entries and give their chooser_routes rows.

    Parameters
    ----------
    entries : Sequence[Entry]
        Entries of one kind.

    Yields
    ------
    RouteRow
        chooser_routes values.
    """
    ranked = sorted(
        entries,
        key=lambda entry: (route_order(number=entry.number), entry.name),
    )
    for rank, entry in enumerate(ranked, start=1):
        grp = bucket_of(number=entry.number)
        if entry.kind not in TAGGED_KINDS:
            grp = ''
        if entry.kind is RouteKind.TRAIN:
            rank = LINE_SORT.get(entry.number, OTHER_LINES + rank)
        yield (
            entry.key, str(entry.kind), grp,
            bucket_order(bucket=grp) if grp else 0, entry.number, rank,
            entry.name, entry.tag, entry.line,
        )


def towards_rows(*, conn: sqlite3.Connection) -> Iterator[tuple[object, ...]]:
    """
    Give one towards row per row key and headsign.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable whose temp.catalogue_keys maps route IDs to row keys.

    Yields
    ------
    tuple[object, ...]
        Row key, headsign, total trips and the busiest station sequence.
    """
    rows = conn.execute(TOWARDS)
    for (key, headsign), group in groupby(rows, key=itemgetter(0, 1)):
        sequences = [(trips, stations) for _, _, stations, trips in group]
        busiest = max(sequences, key=itemgetter(0))
        yield key, headsign, sum(trips for trips, _ in sequences), busiest[1]


def write_towards(
    *, conn: sqlite3.Connection, routes: Sequence[CatalogueRoute],
    sources: Sequence[RouteSource],
) -> None:
    """
    Write the chooser_towards rows of every kept chooser row.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with chooser_routes written.
    routes : Sequence[CatalogueRoute]
        Merged routes; those without a chooser row get no towards rows.
    sources : Sequence[RouteSource]
        The route IDs they were merged from.
    """
    rows = {
        route.route_key: (
            f'{route.kind}:{route.number}'
            if route.kind in LINE_KINDS
            else route.route_key
        )
        for route in routes
    }
    write_keys(conn=conn, keys=(
        (source.route_id, rows[source.key])
        for source in sources
    ))
    insert_many(conn=conn, rows=towards_rows(conn=conn), sql=(
        'INSERT INTO chooser_towards (route_key, headsign, trips, stations) '
        'VALUES (?, ?, ?, ?)'
    ))
    conn.execute('DROP TABLE temp.catalogue_keys')


def load_catalogue(*, conn: sqlite3.Connection) -> int:
    """
    Write the chooser_routes and chooser_towards tables.

    Repeated Bus and School numbers are tagged; lines get one row each.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables and the CATALOGUE_SCHEMA tables.

    Returns
    -------
    int
        Number of chooser_routes rows written.
    """
    sources = route_sources(conn=conn)
    routes = merged_routes(conn=conn, sources=sources)
    tagged = [route for route in routes if route.kind in TAGGED_KINDS]
    tags = tags_of(routes=tagged, towns=agency_towns(routes=tagged))
    entries = [
        *line_entries(
            routes=[r for r in routes if r.kind in LINE_KINDS],
            names=bundle_names(conn=conn),
        ),
        *route_entries(
            routes=[r for r in routes if r.kind not in LINE_KINDS], tags=tags,
        ),
    ]
    kinds: dict[RouteKind, list[Entry]] = defaultdict(list)
    for entry in entries:
        kinds[entry.kind].append(entry)
    rows = [
        row
        for group in kinds.values()
        for row in kind_rows(entries=group)
    ]
    insert_many(conn=conn, rows=rows, sql=(
        'INSERT INTO chooser_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
    ))
    write_towards(conn=conn, routes=routes, sources=sources)
    return len(rows)
