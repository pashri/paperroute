"""Build the route chooser's catalogue: route lists, tags and towards rows."""

import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from typing import Final

from paperroute_transit.build.gtfs_files import insert_many
from paperroute_transit.build.line_stations import (
    write_line_exits,
    write_line_stations,
)
from paperroute_transit.build.route_tags import (
    CatalogueRoute,
    agency_towns,
    tags_of,
)
from paperroute_transit.core.geo import Point, to_metres
from paperroute_transit.core.model import (
    INTERCITY,
    STATION_FIRST,
    SYDNEY_FERRIES,
    TRAINLINK,
    RouteKind,
    kind_for,
)

CATALOGUE_SCHEMA: Final[str] = """
CREATE TABLE chooser_routes (route_key TEXT PRIMARY KEY, kind TEXT NOT NULL,
  grp TEXT NOT NULL, grp_order INTEGER NOT NULL, number TEXT NOT NULL,
  sort INTEGER NOT NULL, name TEXT NOT NULL, tag TEXT NOT NULL,
  line TEXT NOT NULL);
CREATE TABLE chooser_towards (towards_id INTEGER PRIMARY KEY,
  route_key TEXT NOT NULL, headsign TEXT NOT NULL, trips INTEGER NOT NULL,
  stations TEXT NOT NULL);
CREATE INDEX chooser_towards_route ON chooser_towards (route_key, trips);
CREATE TABLE line_stations (line_key TEXT NOT NULL, pos INTEGER NOT NULL,
  station_id TEXT NOT NULL, head TEXT NOT NULL,
  PRIMARY KEY (line_key, pos)) WITHOUT ROWID;
CREATE TABLE line_aliases (station_id TEXT PRIMARY KEY,
  kept_id TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE line_track (line_key TEXT NOT NULL, station_id TEXT NOT NULL,
  PRIMARY KEY (line_key, station_id)) WITHOUT ROWID;
CREATE TABLE line_exits (line_key TEXT NOT NULL, station_id TEXT NOT NULL,
  off_id TEXT NOT NULL, lines TEXT NOT NULL,
  PRIMARY KEY (line_key, station_id, off_id)) WITHOUT ROWID;
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
    **{code: 111 + index for index, code in enumerate(TRAINLINK)},
}
OTHER_LINES: Final[int] = 200
LINE_KINDS: Final[frozenset[RouteKind]] = STATION_FIRST
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
ENDS: Final[str] = """
SELECT p.route_id, p.trips,
  (SELECT name FROM stops WHERE stop_id = json_extract(p.stations, '$[0]')),
  (SELECT name FROM stops WHERE stop_id = json_extract(p.stations, '$[#-1]'))
FROM route_patterns p
WHERE p.route_id IN (SELECT value FROM json_each(?))
"""
ENDS_DASH: Final[str] = ' – '
PLACE_ENDS: Final[re.Pattern[str]] = re.compile(r'[,:]')
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


def merge_key(*, kind: RouteKind, number: str, long_name: str) -> str:
    """
    Key the merged route a route ID joins.

    Parameters
    ----------
    kind : RouteKind
        Kind of route.
    number : str
        Route short name.
    long_name : str
        Route long name.

    Returns
    -------
    str
        Kind, number and long name; a replacement bus by kind and number
        alone, as its long names differ by direction.
    """
    if kind is RouteKind.REPLACEMENT:
        return f'{kind}:{number}'
    return f'{kind}:{number}:{long_name}'


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
                key=merge_key(
                    kind=kind, number=number, long_name=long_name,
                ),
                agency=agency or '',
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


def busiest_names(*, sources: Sequence[RouteSource]) -> dict[str, str]:
    """
    Pick the long name with the most trips of each merged route.

    Parameters
    ----------
    sources : Sequence[RouteSource]
        Route IDs.

    Returns
    -------
    dict[str, str]
        Long name by merged key; ties go to the smallest name.
    """
    trips: dict[str, Counter[str]] = defaultdict(Counter)
    for source in sources:
        trips[source.key][source.merge[2]] += source.trips
    return {key: busiest(counts=counts) for key, counts in trips.items()}


def merged_routes(
    *, conn: sqlite3.Connection, sources: Sequence[RouteSource] | None = None,
) -> list[CatalogueRoute]:
    """
    Merge the route IDs that make one route.

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
        kind, number and long name order. A replacement bus merges by
        kind and number alone, across directions, under its busiest
        long name.
    """
    sources = route_sources(conn=conn) if sources is None else sources
    points = middles(conn=conn, sources=sources)
    agencies = busiest_agencies(sources=sources)
    names = busiest_names(sources=sources)
    routes = {
        source.key: CatalogueRoute(
            route_key=source.key, kind=source.merge[0],
            number=source.merge[1], long_name=names[source.key],
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


def short_place(*, name: str) -> str:
    """
    Shorten a stop name to its place.

    Parameters
    ----------
    name : str
        Stop name, such as 'Wynyard Station, York St, Stand N'.

    Returns
    -------
    str
        The text before any comma or colon, without ' Station'.
    """
    return PLACE_ENDS.split(name)[0].strip().removesuffix(' Station')


def replacement_ends(
    *, conn: sqlite3.Connection, sources: Sequence[RouteSource],
) -> dict[str, str]:
    """
    Name each replacement bus route by where it runs from and to.

    Replacement long names are often long service notes, or 'Unknown'.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    sources : Sequence[RouteSource]
        Route IDs and the merged routes they join.

    Returns
    -------
    dict[str, str]
        'First – last' by route key, from the ends with the most trips;
        routes whose busiest ends are one place are left out.
    """
    keys = {
        source.route_id: source.key
        for source in sources
        if source.merge[0] is RouteKind.REPLACEMENT
    }
    ends: dict[str, Counter[str]] = defaultdict(Counter)
    rows = conn.execute(ENDS, (json.dumps(list(keys)),))
    for route_id, trips, first, last in rows:
        if first is not None and last is not None:
            places = (short_place(name=first), short_place(name=last))
            ends[keys[route_id]][ENDS_DASH.join(places)] += trips
    named = {key: busiest(counts=counts) for key, counts in ends.items()}
    return {
        key: name
        for key, name in named.items()
        if len(set(name.split(ENDS_DASH))) == 2
    }


def route_entries(
    *,
    routes: Sequence[CatalogueRoute],
    tags: Mapping[str, str],
    ends: Mapping[str, str],
) -> Iterator[Entry]:
    """
    Make one entry per merged route of the other kinds.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Merged routes outside the line kinds.
    tags : Mapping[str, str]
        Tags by route key.
    ends : Mapping[str, str]
        Replacement bus names by route key, used in place of the long
        name.

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
            name=ends.get(route.route_key, route.long_name),
            tag=tags.get(route.route_key, ''),
            line=line,
        )


def ferry_rank(*, number: str, fallback: int = OTHER_LINES) -> int:
    """
    Rank a ferry line: Sydney Ferries F1-F10 by number, others after.

    Parameters
    ----------
    number : str
        Ferry line number.
    fallback : int
        Rank of a line outside F1-F10.

    Returns
    -------
    int
        The number of an F1-F10 line, else ``fallback``.
    """
    return int(number[1:]) if SYDNEY_FERRIES.match(number) else fallback


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
        key=lambda entry: (
            (ferry_rank(number=entry.number), (), entry.name)
            if entry.kind is RouteKind.FERRY
            else (*route_order(number=entry.number), entry.name)
        ),
    )
    for rank, entry in enumerate(ranked, start=1):
        grp = bucket_of(number=entry.number)
        if entry.kind not in TAGGED_KINDS:
            grp = ''
        if entry.kind is RouteKind.TRAIN:
            rank = LINE_SORT.get(entry.number, OTHER_LINES + rank)
        elif entry.kind is RouteKind.FERRY:
            rank = ferry_rank(number=entry.number, fallback=OTHER_LINES + rank)
        yield (
            entry.key, str(entry.kind), grp,
            bucket_order(bucket=grp) if grp else 0, entry.number, rank,
            entry.name, entry.tag, entry.line,
        )


def busiest(*, counts: Counter[str]) -> str:
    """
    Pick the value with the most trips.

    Parameters
    ----------
    counts : Counter[str]
        Trips by value.

    Returns
    -------
    str
        The value with the most trips; the smallest on a tie.
    """
    return min(counts, key=lambda value: (-counts[value], value))


def merged_towards(
    *, group: Sequence[tuple[str, str, int]],
) -> tuple[str, int, str]:
    """
    Merge the headsigns that differ only in case into one towards row.

    Parameters
    ----------
    group : Sequence[tuple[str, str, int]]
        Headsign, station sequence and trips of each pattern row.

    Returns
    -------
    tuple[str, int, str]
        The busiest spelling, total trips and the busiest sequence.
    """
    spellings: Counter[str] = Counter()
    sequences: Counter[str] = Counter()
    for headsign, stations, trips in group:
        spellings[headsign] += trips
        sequences[stations] += trips
    return (
        busiest(counts=spellings),
        spellings.total(),
        busiest(counts=sequences),
    )


def towards_text(*, headsign: str) -> str:
    """
    Choose the text a towards row shows for a headsign.

    Parameters
    ----------
    headsign : str
        Pattern headsign.

    Returns
    -------
    str
        The headsign without the stand or street after a colon
        ('Lidcombe Station: Stand C' reads 'Lidcombe Station'); whole
        when nothing comes before the colon.
    """
    place = headsign.partition(':')[0].strip()
    return place or headsign


def towards_rows(*, conn: sqlite3.Connection) -> Iterator[tuple[object, ...]]:
    """
    Give one towards row per row key and headsign, ignoring case.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable whose temp.catalogue_keys maps route IDs to row keys.

    Yields
    ------
    tuple[object, ...]
        Row key, headsign, total trips and the busiest station sequence.
        Headsigns lose any stand or street after a colon.
    """
    groups: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    for key, headsign, stations, trips in conn.execute(TOWARDS):
        shown = towards_text(headsign=headsign)
        groups.setdefault((key, shown.casefold()), []).append(
            (shown, stations, trips),
        )
    for (key, _), group in groups.items():
        yield key, *merged_towards(group=group)


def write_towards(
    *, conn: sqlite3.Connection, routes: Sequence[CatalogueRoute],
    sources: Sequence[RouteSource],
) -> None:
    """
    Write the chooser_towards rows and the line tables.

    Every kept chooser row of a bus-style kind gets towards rows; every
    station-first line gets its station list and its exits instead.

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
    lines = {
        rows[source.key]
        for source in sources
        if source.merge[0] in LINE_KINDS
    }
    insert_many(conn=conn, rows=(
        row for row in towards_rows(conn=conn) if row[0] not in lines
    ), sql=(
        'INSERT INTO chooser_towards (route_key, headsign, trips, stations) '
        'VALUES (?, ?, ?, ?)'
    ))
    write_line_stations(conn=conn, keys={
        source.route_id: rows[source.key]
        for source in sources
        if source.merge[0] in LINE_KINDS
    })
    write_line_exits(conn=conn)
    conn.execute('DROP TABLE temp.catalogue_keys')


def rows_by_kind(*, entries: Sequence[Entry]) -> list[RouteRow]:
    """
    Number the entries kind by kind into chooser rows.

    Parameters
    ----------
    entries : Sequence[Entry]
        Every entry, in any order of kinds.

    Returns
    -------
    list[RouteRow]
        Each kind's rows, kinds in the order first seen.
    """
    kinds: dict[RouteKind, list[Entry]] = defaultdict(list)
    for entry in entries:
        kinds[entry.kind].append(entry)
    return [
        row
        for group in kinds.values()
        for row in kind_rows(entries=group)
    ]


def load_catalogue(*, conn: sqlite3.Connection) -> int:
    """
    Write the chooser and line tables.

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
            routes=[r for r in routes if r.kind not in LINE_KINDS],
            tags=tags,
            ends=replacement_ends(conn=conn, sources=sources),
        ),
    ]
    rows = rows_by_kind(entries=entries)
    insert_many(conn=conn, rows=rows, sql=(
        'INSERT INTO chooser_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
    ))
    write_towards(conn=conn, routes=routes, sources=sources)
    return len(rows)
