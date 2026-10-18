"""Build the route chooser's catalogue: route lists, tags and towards rows."""

import logging
import math
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
from paperroute.core.geo import Point, to_metres
from paperroute.core.model import INTERCITY, RouteKind, kind_for

logger = logging.getLogger(__name__)

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
CENTRES: Final[tuple[tuple[str, float, float, float], ...]] = (
    ('Sydney', 151.207, -33.870, 55),
    ('Newcastle', 151.750, -32.930, 40),
    ('C Coast', 151.340, -33.430, 30),
    ('Wollongong', 150.890, -34.420, 30),
    ('Blue Mtns', 150.400, -33.700, 25),
)  # label, lon, lat, radius in km
PLACE_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r'\s+(City Centre|Town Centre|CBD|Station|Interchange|Mall|City|Park Ave'
    r'|Shopping Centre|Shops|Market Square|Manning Mall|East|West|North'
    r'|South|CBD)$',
)
PLACE_SHORT: Final[dict[str, str]] = {
    'Port Macquarie': 'Port Mac',
    'Coffs Harbour': 'Coffs',
    'Wagga Wagga': 'Wagga',
    'Batemans Bay': 'Batemans',
    'Raymond Terrace': 'Raymond T',
    'Soldiers Point': 'Soldiers P',
    'Muswellbrook': 'Muswellbk',
    'Murwillumbah': 'Murwillum',
    'Queanbeyan': 'Queanbeyan',
    'University of Wollongong': 'UOW',
    'City Martin Place': 'City',
    'City Wynyard': 'City',
    'City QVB': 'City',
    'Broken Hill': 'Broken Hl',
    'Campbelltown': 'Campbelltn',
    'Stockland Forster': 'Forster',
    'QEII Square': 'Albury',
    'Orana': 'Dubbo',
    'Green Hills': 'Maitland',
    'Park Beach Plaza': 'Coffs',
    'Toormina Gardens': 'Coffs',
    'Sunnyside': 'Murwillum',
}
TAG_MAX: Final[int] = 10
REPLACEMENT: Final[re.Pattern[str]] = re.compile(
    r'^\d+[A-Z]?(T\d|BM|CN|HU|SC|SH)',
)
REPLACEMENT_LINES: Final[dict[str, str]] = {
    'BM': 'BMT', 'CN': 'CCN', 'HU': 'HUN', 'SC': 'SCO', 'SH': 'SHL',
}
FIRST_HUNDRED: Final[str] = '1\u201399'
LEVELS: Final[int] = 4  # region, first place, last place, via place
LETTERED: Final[int] = 10**6
NO_NUMBER: Final[int] = 10**9
LINE_SORT: Final[dict[str, int]] = {
    **{f'T{line}': line for line in range(1, 10)},
    **{code: 101 + index for index, code in enumerate(INTERCITY)},
}
LINE_KINDS: Final[frozenset[RouteKind]] = frozenset(
    {RouteKind.TRAIN, RouteKind.METRO, RouteKind.LIGHT_RAIL},
)
TAGGED_KINDS: Final[frozenset[RouteKind]] = frozenset(
    {RouteKind.BUS, RouteKind.SCHOOL},
)
LEADING_DIGITS: Final[re.Pattern[str]] = re.compile(r'^\d+')
NATURAL_PARTS: Final[re.Pattern[str]] = re.compile(r'\d+|\D+')
WORD: Final[re.Pattern[str]] = re.compile(r'[^\W_]+')
UNKNOWN_TOWN: Final[str] = '?'
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
class CatalogueRoute:
    """
    One chooser route: every route ID of a kind, number and long name.

    Attributes
    ----------
    route_key : str
        'kind:number:long name'.
    kind : RouteKind
        Kind.
    number : str
        Route short name.
    long_name : str
        Route long name.
    agency : str
        The operator running the most trips.
    middle : Point | None
        Median station point in metres; None without station positions.
    """

    route_key: str
    kind: RouteKind
    number: str
    long_name: str
    agency: str
    middle: Point | None


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


def end_places(*, long_name: str) -> list[str]:
    """
    Pull the place names out of a long name such as 'A to B via C'.

    Parameters
    ----------
    long_name : str
        Route long name.

    Returns
    -------
    list[str]
        The places before any 'via', '(', ' or ' or ' and ', cleaned of
        suffixes; 'Unknown' and blanks are left out.
    """
    trimmed = re.split(r' via |\(| or | and ', long_name)[0]
    return [
        PLACE_SUFFIX.sub('', part.strip()).strip()
        for part in trimmed.split(' to ')
        if part.strip() and part.strip() != 'Unknown'
    ]


def via_place(*, long_name: str) -> str:
    """
    Find the place a long name says the route runs via.

    Parameters
    ----------
    long_name : str
        Route long name.

    Returns
    -------
    str
        The text after ' via ' up to any '(', cleaned of suffixes; '' when
        there is none.
    """
    _, via, rest = long_name.partition(' via ')
    place = PLACE_SUFFIX.sub('', rest.split('(')[0].strip()).strip()
    return place if via else ''


def short_tag(*, place: str, other: str | None = None) -> str:
    """
    Shorten a place to a tag, keeping it apart from another place.

    Parameters
    ----------
    place : str
        Place name.
    other : str | None
        A different place whose cut might read the same.

    Returns
    -------
    str
        The PLACE_SHORT form or the name when it fits TAG_MAX, else its
        first nine characters and '.'; when that equals the other's cut
        although the names differ, its first four characters, '.', then
        its last five.
    """
    short = PLACE_SHORT.get(place, place)
    cut = short
    if len(short) > TAG_MAX:
        cut = short[:TAG_MAX - 1].rstrip() + '.'
    if other is None or other == place or short_tag(place=other) != cut:
        return cut
    return f'{short[:4]}.{short[-5:]}'


def agency_towns(*, routes: Sequence[CatalogueRoute]) -> dict[str, str]:
    """
    Name each operator after the place its routes most often end at.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Routes.

    Returns
    -------
    dict[str, str]
        Place by operator ID; operators with no places are missing.
    """
    ends: dict[str, Counter[str]] = defaultdict(Counter)
    for route in routes:
        ends[route.agency].update(end_places(long_name=route.long_name))
    return {
        agency: places.most_common(1)[0][0]
        for agency, places in ends.items()
        if places
    }


def centre_of(*, middle: Point) -> str | None:
    """
    Find the big centre a point belongs to, if any.

    Parameters
    ----------
    middle : Point
        Position in metres.

    Returns
    -------
    str | None
        The centre whose radius holds the point, nearest by share of
        radius, or None outside them all.
    """
    reach = []
    for label, lon, lat, radius in CENTRES:
        reached = math.dist(middle, to_metres(lon=lon, lat=lat))
        if (share := reached / (radius * 1000)) < 1:
            reach.append((share, label))
    return min(reach)[1] if reach else None


def level_values(
    *, route: CatalogueRoute, towns: Mapping[str, str],
) -> tuple[str, str, str, str]:
    """
    Give a route's candidate tags, level by level.

    Parameters
    ----------
    route : CatalogueRoute
        Route.
    towns : Mapping[str, str]
        Place by operator ID.

    Returns
    -------
    tuple[str, str, str, str]
        Region (centre, else operator town, else '?'), first place, last
        place and via place; '' where there is none.
    """
    centre = None if route.middle is None else centre_of(middle=route.middle)
    places = end_places(long_name=route.long_name) or ['']
    return (
        centre or towns.get(route.agency, UNKNOWN_TOWN),
        places[0],
        places[-1],
        via_place(long_name=route.long_name),
    )


def twin_of(*, place: str, places: Iterable[str]) -> str | None:
    """
    Find another place whose cut reads the same as this one's.

    Parameters
    ----------
    place : str
        Place.
    places : Iterable[str]
        Places to look through.

    Returns
    -------
    str | None
        The first different place with the same cut, if any.
    """
    cut = short_tag(place=place)
    return next(
        (
            other
            for other in places
            if other != place and short_tag(place=other) == cut
        ),
        None,
    )


def level_tags(
    *, values: Mapping[str, str], given: set[str],
) -> dict[str, str]:
    """
    Tag the routes whose value at one level tells them apart.

    Parameters
    ----------
    values : Mapping[str, str]
        Each untagged route's value at the level, by route key.
    given : set[str]
        Tags already given in the group.

    Returns
    -------
    dict[str, str]
        Tags, for routes whose value is usable and whose tag is unique.
    """
    tags = {
        key: short_tag(
            place=value, other=twin_of(place=value, places=values.values()),
        )
        for key, value in values.items()
        if value not in ('', UNKNOWN_TOWN)
    }
    seen = Counter(tags.values())
    return {
        key: tag
        for key, tag in tags.items()
        if seen[tag] == 1 and tag not in given
    }


def differing_word(
    *, words: tuple[str, ...], others: Sequence[tuple[str, ...]],
    taken: set[str],
) -> str | None:
    """
    Find the first word unlike the other routes' words in its place.

    Parameters
    ----------
    words : tuple[str, ...]
        One route's words.
    others : Sequence[tuple[str, ...]]
        The other routes' words.
    taken : set[str]
        Tags already used.

    Returns
    -------
    str | None
        Tag of the first word that differs from every other route's word
        at the same position and is not taken, if any.
    """
    return next(
        (
            short_tag(place=word)
            for index, word in enumerate(words)
            if all(other[index:index + 1] != (word,) for other in others)
            and short_tag(place=word) not in taken
        ),
        None,
    )


def free_word(*, words: tuple[str, ...], taken: set[str]) -> str:
    """
    Pick a route's first word whose tag is not yet used.

    Parameters
    ----------
    words : tuple[str, ...]
        One route's words.
    taken : set[str]
        Tags already used.

    Returns
    -------
    str
        That tag; else the first word's tag (shared), or '?' with no words.
    """
    tags = [short_tag(place=word) for word in words] or [UNKNOWN_TOWN]
    return next((tag for tag in tags if tag not in taken), tags[0])


def word_tags(
    *, routes: Sequence[CatalogueRoute], given: set[str],
) -> dict[str, str]:
    """
    Tag routes alike at every level by a word of their long names.

    Routes with the same words share one tag.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Routes left untagged.
    given : set[str]
        Tags already given in the group.

    Returns
    -------
    dict[str, str]
        Tags by route key.
    """
    words = {
        route.route_key: tuple(WORD.findall(route.long_name))
        for route in routes
    }
    units = list(dict.fromkeys(words.values()))
    taken = set(given)
    picks: dict[tuple[str, ...], str] = {}
    for unit in units:
        others = [other for other in units if other != unit]
        if pick := differing_word(words=unit, others=others, taken=taken):
            picks[unit] = pick
            taken.add(pick)
    for unit in (unit for unit in units if unit not in picks):
        picks[unit] = free_word(words=unit, taken=taken)
        taken.add(picks[unit])
    return {key: picks[unit] for key, unit in words.items()}


def group_tags(
    *, group: Sequence[CatalogueRoute], towns: Mapping[str, str],
) -> dict[str, str]:
    """
    Tag the routes of one repeated number.

    Each level (region, first place, last place, via place) tags the
    routes it tells apart; any left are warned about and tagged by words.

    Parameters
    ----------
    group : Sequence[CatalogueRoute]
        Routes sharing a kind and number.
    towns : Mapping[str, str]
        Place by operator ID.

    Returns
    -------
    dict[str, str]
        Tags by route key.
    """
    values = {
        route.route_key: level_values(route=route, towns=towns)
        for route in group
    }
    tags: dict[str, str] = {}
    for level in range(LEVELS):
        tags.update(level_tags(
            values={
                key: value[level]
                for key, value in values.items()
                if key not in tags
            },
            given=set(tags.values()),
        ))
    left = [route for route in group if route.route_key not in tags]
    if left:
        logger.warning(
            'route tags clash after every level: %s',
            ', '.join(route.route_key for route in left),
        )
        tags.update(word_tags(routes=left, given=set(tags.values())))
    return tags


def tags_of(
    *, routes: Sequence[CatalogueRoute], towns: Mapping[str, str],
) -> dict[str, str]:
    """
    Tag every route whose kind and number repeat.

    Parameters
    ----------
    routes : Sequence[CatalogueRoute]
        Merged routes.
    towns : Mapping[str, str]
        Place by operator ID.

    Returns
    -------
    dict[str, str]
        Tags by route key; routes with a unique number are missing.
    """
    groups: dict[tuple[RouteKind, str], list[CatalogueRoute]] = defaultdict(
        list,
    )
    for route in routes:
        groups[(route.kind, route.number)].append(route)
    tags: dict[str, str] = {}
    for group in groups.values():
        if len(group) > 1:
            tags.update(group_tags(group=group, towns=towns))
    return tags


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


def merged_routes(*, conn: sqlite3.Connection) -> list[CatalogueRoute]:
    """
    Merge the route IDs of each kind, number and long name.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.

    Returns
    -------
    list[CatalogueRoute]
        One route per kind, number and long name with route patterns, in
        kind, number and long name order.
    """
    sources = route_sources(conn=conn)
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
            rank = LINE_SORT.get(entry.number, rank)
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
) -> None:
    """
    Write the chooser_towards rows of every kept chooser row.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with chooser_routes written.
    routes : Sequence[CatalogueRoute]
        Merged routes; those without a chooser row get no towards rows.
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
        for source in route_sources(conn=conn)
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
    routes = merged_routes(conn=conn)
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
    rows = [
        row
        for _, group in groupby(entries, key=lambda entry: entry.kind)
        for row in kind_rows(entries=list(group))
    ]
    insert_many(conn=conn, rows=rows, sql=(
        'INSERT INTO chooser_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
    ))
    write_towards(conn=conn, routes=routes)
    return len(rows)
