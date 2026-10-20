"""Network repository: chooser columns, line stations and station runs."""

import json
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from itertools import groupby
from pathlib import Path
from typing import Final, cast

from paperroute.adapters.sqlite_ro import (
    Identity,
    ReadOnlyDatabase,
    file_identity,
)
from paperroute.adapters.timetable_db import DEFAULT_PATH, to_point
from paperroute.core.chooser import (
    KIND_LABELS,
    KIND_LETTERS,
    KIND_ORDER,
    Choice,
    ChooserPath,
    Column,
    Level,
)
from paperroute.core.model import (
    INTERCITY,
    STATION_FIRST,
    SYDNEY_FERRIES,
    SYDNEY_LINES,
    TRAINLINK,
    RouteKind,
    StopPlace,
    until_revisit,
)
from paperroute.core.reach import (
    LINE_KINDS,
    ReachInput,
    RunTail,
    StripKey,
    cut_to_line,
    every_route,
    line_key_of,
    merge_tails,
)
from paperroute.core.reach_exits import found_exits
from paperroute.core.reach_layout import LineExit

NETWORK_TABLES: Final[tuple[str, ...]] = (
    'runs', 'run_stops', 'chooser_routes', 'chooser_towards',
)
LINE_TABLES: Final[tuple[str, ...]] = ('line_stations', 'line_aliases')
EXIT_TABLES: Final[tuple[str, ...]] = ('line_exits',)
LINE_EXITS: Final[str] = (
    'SELECT station_id, off_id, lines FROM line_exits WHERE line_key = ?'
)
SCHEMA_CHECK: Final[str] = """
SELECT COUNT(*) FROM sqlite_master
WHERE type = 'table' AND name IN (SELECT value FROM json_each(?))
"""
KIND_COUNTS: Final[str] = (
    'SELECT kind, COUNT(*) FROM chooser_routes GROUP BY kind'
)
GROUPS: Final[str] = """
SELECT grp, COUNT(*) FROM chooser_routes WHERE kind = ?
GROUP BY grp ORDER BY MIN(grp_order), grp
"""
ROUTE_COLUMNS: Final[str] = (
    'SELECT route_key, number, name, tag FROM chooser_routes '
)
ROUTE_ORDER: Final[str] = ' ORDER BY sort, route_key'
ROUTES_OF_KIND: Final[str] = ROUTE_COLUMNS + 'WHERE kind = ?' + ROUTE_ORDER
ROUTES_IN_GROUP: Final[str] = (
    ROUTE_COLUMNS + 'WHERE kind = ? AND grp = ?' + ROUTE_ORDER
)
LINE_OF: Final[str] = (
    'SELECT number FROM chooser_routes WHERE route_key = ?'
)
REPLACEMENTS_OF: Final[str] = (
    ROUTE_COLUMNS
    + f"WHERE kind = 'replacement' AND line = ({LINE_OF})"
    + ROUTE_ORDER
)
TOWARDS: Final[str] = """
SELECT towards_id, headsign, trips FROM chooser_towards
WHERE route_key = ? ORDER BY trips DESC, towards_id
"""
TOWARDS_LINE: Final[str] = """
SELECT j.value, s.name, s.lon, s.lat
FROM chooser_towards t, json_each(t.stations) j
JOIN stops s ON s.stop_id = j.value
WHERE t.towards_id = ?
ORDER BY j.key
"""
STATION_OF: Final[str] = """
SELECT COALESCE(NULLIF(parent_station, ''), stop_id) FROM stops
WHERE stop_id = ?
"""
RUNS_THROUGH: Final[str] = """
SELECT r.stations, r.trips, s.pos
FROM run_stops s JOIN runs r ON r.run_id = s.run_id
WHERE s.station_id = :station
  AND (:kind IS NULL OR r.kind = :kind)
  AND (:any_route OR r.route IN (SELECT value FROM json_each(:routes)))
ORDER BY s.run_id, s.pos
"""
PLACES: Final[str] = """
SELECT stop_id, name, lon, lat FROM stops
WHERE stop_id IN (SELECT value FROM json_each(?))
"""
CHILDREN: Final[str] = """
SELECT stop_id, COALESCE(NULLIF(parent_station, ''), stop_id) FROM stops
WHERE stop_id IN (SELECT value FROM json_each(:ids))
  OR parent_station IN (SELECT value FROM json_each(:ids))
"""
LINE_STATIONS: Final[str] = """
SELECT l.station_id, l.head, s.name, s.lon, s.lat, COALESCE(h.name, l.head)
FROM line_stations l JOIN stops s ON s.stop_id = l.station_id
  LEFT JOIN stops h ON h.stop_id = l.head
WHERE l.line_key = ?
ORDER BY l.pos
"""
ALIASES: Final[str] = 'SELECT station_id, kept_id FROM line_aliases'
ON_LINE: Final[str] = """
SELECT station_id FROM line_stations WHERE line_key = :line
UNION
SELECT a.station_id FROM line_aliases a
JOIN line_stations l ON l.station_id = a.kept_id AND l.line_key = :line
"""
PASSES: Final[str] = """
SELECT DISTINCT r.run_id, r.stations
FROM run_stops s JOIN runs r ON r.run_id = s.run_id
WHERE s.station_id IN (SELECT value FROM json_each(:stations))
  AND r.kind = :kind
"""
FROM_TAG: Final[str] = 'ALL'
type Row = tuple[object, ...]


def unit_count(*, count: int, unit: str) -> str:
    """
    Write a count with its unit, such as '1 line' or '1,234 routes'.

    Parameters
    ----------
    count : int
        Number.
    unit : str
        Singular unit.

    Returns
    -------
    str
        Label.
    """
    return f'{count:,} {unit}{"" if count == 1 else "s"}'


def kind_of(*, path: ChooserPath) -> RouteKind | None:
    """
    Read the kind a chooser path starts with.

    Parameters
    ----------
    path : ChooserPath
        Chooser path.

    Returns
    -------
    RouteKind | None
        The kind of its first, KIND, step; None when it has none or the
        kind is not offered.
    """
    if not path or path[0][0] is not Level.KIND:
        return None
    return next((kind for kind in KIND_ORDER if kind == path[0][1]), None)


def route_choices(*, rows: Iterable[Row]) -> tuple[Choice, ...]:
    """
    Turn chooser_routes rows into route choices.

    Parameters
    ----------
    rows : Iterable[Row]
        Route key, number, name and tag.

    Returns
    -------
    tuple[Choice, ...]
        Choices in row order.
    """
    return tuple(
        Choice(
            key=str(key), label=str(name), badge=str(number), tag=str(tag),
        )
        for key, number, name, tag in rows
    )


def line_group(*, number: str) -> int:
    """
    Sort a line number into the group it is listed in.

    Parameters
    ----------
    number : str
        Line number, such as 'T1', 'BMT' or 'F1'.

    Returns
    -------
    int
        0 for Sydney Trains and Sydney Ferries lines, 1 for intercity
        lines, 2 for NSW TrainLink lines and 3 for anything else.
    """
    if number in INTERCITY:
        return 1
    if number in TRAINLINK:
        return 2
    if SYDNEY_LINES.match(number) or SYDNEY_FERRIES.match(number):
        return 0
    return 3


def ruled_off(*, lines: Sequence[Choice]) -> tuple[Choice, ...]:
    """
    Put a rule above each line that starts a new group.

    Parameters
    ----------
    lines : Sequence[Choice]
        Line choices in order.

    Returns
    -------
    tuple[Choice, ...]
        The same lines, each one whose group differs from the line before
        it with ``rule_above``.
    """
    groups = [line_group(number=line.badge) for line in lines]
    return tuple(
        replace(line, rule_above=True) if index and group != groups[index - 1]
        else line
        for index, (line, group) in enumerate(zip(lines, groups))
    )


def short_name(*, name: object) -> str:
    """
    Name a station without its ' Station' suffix.

    Parameters
    ----------
    name : object
        Stop name read from the file.

    Returns
    -------
    str
        The name.
    """
    return str(name).removesuffix(' Station')


def station_choices(*, rows: Iterable[Row]) -> tuple[Choice, ...]:
    """
    Turn line_stations rows into station rows under block headings.

    Parameters
    ----------
    rows : Iterable[Row]
        Station ID, head, name, longitude, latitude and head name, in
        line order. A row with a head opens a block.

    Returns
    -------
    tuple[Choice, ...]
        A heading before each block, then one row per station.
    """
    items: list[Choice] = []
    for station, head, name, lon, lat, head_name in rows:
        if head:
            items.append(Choice(
                key='', label=f'To {short_name(name=head_name)}',
                heading=True,
            ))
        items.append(Choice(
            key=str(station), label=short_name(name=name),
            place=StopPlace(
                stop_id=str(station), name=str(name),
                point=to_point(lon=lon, lat=lat),
            ),
        ))
    return tuple(items)


def to_choice(
    *, choice: Choice, origin: str, reached: Collection[str],
) -> Choice:
    """
    Mark a station row for the TO column.

    Parameters
    ----------
    choice : Choice
        Row of the station column.
    origin : str
        The From station ID.
    reached : Collection[str]
        Stations reached directly from the From.

    Returns
    -------
    Choice
        The From tagged ALL, an unreached station dim, any other row as
        it was.
    """
    if choice.heading:
        return choice
    if choice.key == origin:
        return replace(choice, tag=FROM_TAG)
    return choice if choice.key in reached else replace(choice, dim=True)


def later_stops(
    *, rows: Iterable[Row], origin: str, aliases: Mapping[str, str],
) -> frozenset[str]:
    """
    Collect the stations runs call at after a pass of the From.

    Stations are first mapped to the station kept for them, with
    back-to-back calls at one kept station made one. Each pass is then
    cut, as a strip's tails are, before the first station the run would
    call at again, so a run that turns back reaches nothing past the
    turn.

    Parameters
    ----------
    rows : Iterable[Row]
        Run ID and stations as JSON of each run through the From.
    origin : str
        The From's kept station ID.
    aliases : Mapping[str, str]
        Kept station by merged station ID.

    Returns
    -------
    frozenset[str]
        Kept stations after the From on some pass.
    """
    reached: set[str] = set()
    for _, stations in rows:
        calls = [
            station
            for station, _ in groupby(
                aliases.get(call, call) for call in json.loads(str(stations))
            )
        ]
        for pos, station in enumerate(calls):
            if station == origin:
                reached.update(until_revisit(stations=calls[pos:])[1:])
    return frozenset(reached)


def merged_tails(*, rows: Iterable[Row]) -> tuple[RunTail, ...]:
    """
    Make one tail per pass of the From and merge identical tails.

    Each tail is cut before the first station it would call at again
    after the From; stations before the From never cut it. Tails are
    then merged as `merge_tails` does.

    Parameters
    ----------
    rows : Iterable[Row]
        Each run's stations as JSON, its trip-days and the From's
        position in it, one row per pass.

    Returns
    -------
    tuple[RunTail, ...]
        Tails, the most trips first, then by their stations.
    """
    passes = (
        (tuple(json.loads(str(stations))), cast(int, trips), cast(int, pos))
        for stations, trips, pos in rows
    )
    return merge_tails(tails=(
        RunTail(
            before=calls[:at],
            after=until_revisit(stations=calls[at:])[1:],
            trips=weight,
        )
        for calls, weight, at in passes
    ))


class SqliteNetworkRepository:
    """
    Read-only chooser and run queries against the timetable file.

    A file built before the run and chooser tables answers None.

    Parameters
    ----------
    path : Path | None
        Timetable file; None for the one on the device.
    connection : sqlite3.Connection | None
        Connection to read instead of the file. It is never reopened, and
        close() closes it.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db = ReadOnlyDatabase(
            path=path or DEFAULT_PATH,
            connection=connection,
            probe='SELECT 1 FROM stops LIMIT 1',
            label='network',
        )
        self.schema: dict[
            tuple[str, ...], tuple[Identity | None, bool]
        ] = {}

    def fetch(
        self, *, sql: str, params: tuple[object, ...] | dict[str, object]
    ) -> list[Row]:
        """
        Run a query against the current file.

        Parameters
        ----------
        sql : str
            Query.
        params : tuple[object, ...] | dict[str, object]
            Query parameters.

        Returns
        -------
        list[Row]
            All rows.
        """
        return self.db.fetch(sql=sql, params=params)

    def _has_tables(self, *, tables: tuple[str, ...]) -> bool:
        """
        Say whether the file has every one of some tables.

        The answer is kept while the file is unchanged on disk.

        Parameters
        ----------
        tables : tuple[str, ...]
            Table names.

        Returns
        -------
        bool
            True for a file with every table.
        """
        path = self.db.path
        known = self.schema.get(tables)
        if known is not None and (
            path is None or file_identity(path=path) == known[0]
        ):
            return known[1]
        self.fetch(sql='SELECT 1', params=())
        if known is None or known[0] != self.db.identity:
            rows = self.fetch(sql=SCHEMA_CHECK, params=(json.dumps(tables),))
            found = bool(rows) and rows[0][0] == len(tables)
            known = (self.db.identity, found)
            self.schema[tables] = known
        return known[1]

    def has_network(self) -> bool:
        """
        Say whether the file has the run and chooser tables.

        Returns
        -------
        bool
            True for a file with every network table.
        """
        return self._has_tables(tables=NETWORK_TABLES)

    def has_lines(self) -> bool:
        """
        Say whether the file has the line station tables.

        Returns
        -------
        bool
            True for a file with line_stations and line_aliases.
        """
        return self._has_tables(tables=LINE_TABLES)

    def choices(self, *, path: ChooserPath) -> Column | None:
        """
        The chooser column that follows a path.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far; () for the KIND column.

        Returns
        -------
        Column | None
            The next column; None for an old file, an unknown path or a
            path with nothing after it.
        """
        if not self.has_network():
            return None
        column = self.next_column(path=path) if path else self.kind_column()
        return column if column is not None and column.items else None

    def next_column(self, *, path: ChooserPath) -> Column | None:
        """
        Work out the column after a non-empty path.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far.

        Returns
        -------
        Column | None
            The next column, possibly empty; None when nothing follows.
        """
        kind = kind_of(path=path)
        level, key = path[-1]
        column = None
        match (level, kind):
            case (Level.KIND, RouteKind.BUS | RouteKind.SCHOOL):
                column = self.group_column(kind=kind)
            case (Level.KIND, RouteKind.COACH):
                column = self.route_column(sql=ROUTES_OF_KIND, params=(kind,))
            case (
                Level.KIND,
                RouteKind.TRAIN
                | RouteKind.METRO
                | RouteKind.LIGHT_RAIL
                | RouteKind.FERRY,
            ):
                column = self.line_column(kind=kind)
            case (Level.GROUP, RouteKind()):
                column = self.route_column(
                    sql=ROUTES_IN_GROUP, params=(kind, key),
                )
            case (Level.LINE | Level.STATION, RouteKind()) if (
                kind in STATION_FIRST
            ):
                column = self.station_first_column(path=path, kind=kind)
            case (Level.ROUTE | Level.LINE, RouteKind()):
                column = self.towards_column(route_key=key)
            case (Level.REPLACEMENTS, RouteKind.TRAIN) if (
                path[-2][0] is Level.LINE
            ):
                column = self.route_column(
                    sql=REPLACEMENTS_OF, params=(path[-2][1],),
                )
        return column

    def kind_column(self) -> Column:
        """
        The KIND column: each kind with routes, counted.

        Returns
        -------
        Column
            Kinds in chooser order.
        """
        counts = {
            str(kind): cast(int, count)
            for kind, count in self.fetch(sql=KIND_COUNTS, params=())
        }
        items = tuple(
            Choice(
                key=kind, label=KIND_LABELS[kind], badge=KIND_LETTERS[kind],
                detail=unit_count(
                    count=counts[kind],
                    unit='line' if kind in LINE_KINDS else 'route',
                ),
            )
            for kind in KIND_ORDER
            if kind in counts
        )
        return Column(level=Level.KIND, items=items)

    def group_column(self, *, kind: RouteKind) -> Column:
        """
        The GROUP column of a kind: its hundreds groups, counted.

        Parameters
        ----------
        kind : RouteKind
            Bus or School.

        Returns
        -------
        Column
            Groups in group order.
        """
        rows = self.fetch(sql=GROUPS, params=(kind,))
        items = tuple(
            Choice(key=str(grp), label=str(grp), detail=str(count))
            for grp, count in rows
        )
        return Column(level=Level.GROUP, items=items)

    def route_column(self, *, sql: str, params: tuple[object, ...]) -> Column:
        """
        A ROUTE column of the chooser_routes rows a query picks.

        Parameters
        ----------
        sql : str
            Query giving route key, number, name and tag.
        params : tuple[object, ...]
            Query parameters.

        Returns
        -------
        Column
            Routes in sort order.
        """
        rows = self.fetch(sql=sql, params=params)
        return Column(level=Level.ROUTE, items=route_choices(rows=rows))

    def line_column(self, *, kind: RouteKind) -> Column:
        """
        The LINE column of a kind, each group of lines ruled off.

        Parameters
        ----------
        kind : RouteKind
            Train, Metro, Light rail or Ferry.

        Returns
        -------
        Column
            Lines in sort order.
        """
        rows = self.fetch(sql=ROUTES_OF_KIND, params=(kind,))
        lines = ruled_off(lines=route_choices(rows=rows))
        return Column(level=Level.LINE, items=lines)

    def towards_column(self, *, route_key: str) -> Column:
        """
        The TOWARDS column of a route, busiest first.

        Parameters
        ----------
        route_key : str
            Chooser row key.

        Returns
        -------
        Column
            Headsigns with trip counts.
        """
        items = tuple(
            Choice(key=str(towards), label=str(headsign), detail=str(trips))
            for towards, headsign, trips in self.fetch(
                sql=TOWARDS, params=(route_key,),
            )
        )
        return Column(level=Level.TOWARDS, items=items)

    def station_first_column(
        self, *, path: ChooserPath, kind: RouteKind,
    ) -> Column | None:
        """
        The STATION or TO column after a station-first line or station.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far, ending in a LINE or STATION step.
        kind : RouteKind
            The path's station-first kind.

        Returns
        -------
        Column | None
            The line's stations after a LINE step; the TO column after a
            STATION step that follows a LINE step; None otherwise or for
            a file without the line tables.
        """
        if not self.has_lines():
            return None
        level, key = path[-1]
        if level is Level.LINE:
            return self.station_column(line_key=key)
        if path[-2][0] is not Level.LINE:
            return None
        return self.to_column(line_key=path[-2][1], origin=key, kind=kind)

    def station_column(self, *, line_key: str) -> Column:
        """
        The STATION column of a line: its stations in blocks.

        Parameters
        ----------
        line_key : str
            Chooser row key of the line.

        Returns
        -------
        Column
            Block headings and stations in line order.
        """
        rows = self.fetch(sql=LINE_STATIONS, params=(line_key,))
        return Column(level=Level.STATION, items=station_choices(rows=rows))

    def to_column(
        self, *, line_key: str, origin: str, kind: RouteKind,
    ) -> Column | None:
        """
        The TO column of a line from a station.

        Parameters
        ----------
        line_key : str
            Chooser row key of the line.
        origin : str
            The From station ID.
        kind : RouteKind
            The line's kind.

        Returns
        -------
        Column | None
            The station column with the From tagged ALL and every station
            no run of the kind reaches directly from it dim; None when the
            From is not on the line. A station merged into another by
            line_aliases counts as the station kept for it, both as the
            From and as a later stop.
        """
        aliases = {
            str(merged): str(kept)
            for merged, kept in self.fetch(sql=ALIASES, params=())
        }
        origin = aliases.get(origin, origin)
        stations = self.station_column(line_key=line_key).items
        if origin not in {choice.key for choice in stations}:
            return None
        merged = [origin, *(m for m, k in aliases.items() if k == origin)]
        rows = self.fetch(sql=PASSES, params={
            'stations': json.dumps(merged), 'kind': str(kind),
        })
        reached = later_stops(rows=rows, origin=origin, aliases=aliases)
        return Column(level=Level.TO, items=tuple(
            to_choice(choice=choice, origin=origin, reached=reached)
            for choice in stations
        ))

    def towards_line(self, *, towards: int) -> tuple[StopPlace, ...] | None:
        """
        The stations of a towards row's busiest stop sequence.

        Parameters
        ----------
        towards : int
            Towards ID.

        Returns
        -------
        tuple[StopPlace, ...] | None
            Stations in travel order with their names and points; None for
            an old file or an unknown towards ID.
        """
        if not self.has_network():
            return None
        rows = self.fetch(sql=TOWARDS_LINE, params=(towards,))
        line = tuple(
            StopPlace(
                stop_id=str(stop_id), name=str(name),
                point=to_point(lon=lon, lat=lat),
            )
            for stop_id, name, lon, lat in rows
        )
        return line or None

    def station_for(self, *, stop_id: str) -> str:
        """
        Find the station a stop belongs to.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        str
            Its parent station's ID, or the ID itself.
        """
        rows = self.fetch(sql=STATION_OF, params=(stop_id,))
        return str(rows[0][0]) if rows else stop_id

    def places(self, *, stations: Collection[str]) -> dict[str, StopPlace]:
        """
        Read stations with their names and points.

        Parameters
        ----------
        stations : Collection[str]
            Station IDs.

        Returns
        -------
        dict[str, StopPlace]
            Places by ID; unknown IDs are absent.
        """
        rows = self.fetch(sql=PLACES, params=(json.dumps(list(stations)),))
        return {
            str(stop_id): StopPlace(
                stop_id=str(stop_id), name=str(name),
                point=to_point(lon=lon, lat=lat),
            )
            for stop_id, name, lon, lat in rows
        }

    def station_of(self, *, stations: Collection[str]) -> dict[str, str]:
        """
        Map stations and the stops under them to the station.

        Parameters
        ----------
        stations : Collection[str]
            Station IDs.

        Returns
        -------
        dict[str, str]
            Station ID by stop or station ID.
        """
        rows = self.fetch(
            sql=CHILDREN, params={'ids': json.dumps(list(stations))},
        )
        return {str(stop_id): str(station) for stop_id, station in rows}

    def tails(self, *, station: str, key: StripKey) -> tuple[RunTail, ...]:
        """
        Read the tails of the runs through a station that feed a strip.

        Train, Metro and Light rail take every run of their kind; other
        kinds only their routes' runs, or every run of the kind when no
        routes are chosen; no kind takes every run.

        Parameters
        ----------
        station : str
            Station ID.
        key : StripKey
            Strip kind and routes.

        Returns
        -------
        tuple[RunTail, ...]
            Merged tails.
        """
        rows = self.fetch(sql=RUNS_THROUGH, params={
            'station': station,
            'kind': None if key.kind is None else str(key.kind),
            'any_route': every_route(key=key),
            'routes': json.dumps(list(key.routes)),
        })
        return merged_tails(rows=rows)

    def _line_of(self, *, key: StripKey) -> frozenset[str]:
        """
        List the stations on the line a strip follows.

        Parameters
        ----------
        key : StripKey
            Strip kind and routes.

        Returns
        -------
        frozenset[str]
            The line's stations and the stations merged into them; empty
            for a strip that follows no line, a line the file does not
            list, or a file without the line tables.
        """
        line_key = line_key_of(key=key)
        if line_key is None or not self.has_lines():
            return frozenset()
        rows = self.fetch(sql=ON_LINE, params={'line': line_key})
        return frozenset(str(station) for station, in rows)

    def _exits_of(
        self, *, key: StripKey, tails: Sequence[RunTail],
        line: Collection[str], origin: str,
    ) -> tuple[LineExit, ...]:
        """
        Find the ways the runs through a line strip's From leave its line.

        Parameters
        ----------
        key : StripKey
            Strip kind and routes.
        tails : Sequence[RunTail]
            Tails through the From, not cut to the line.
        line : Collection[str]
            The line's stations (see `_line_of`); empty for none.
        origin : str
            The From's station ID.

        Returns
        -------
        tuple[LineExit, ...]
            Exits by junction and off station, each with the lines the
            file lists for its hop, or none; empty without a line or
            without the line_exits table.
        """
        line_key = line_key_of(key=key)
        if not line or line_key is None or not self._has_tables(
            tables=EXIT_TABLES,
        ):
            return ()
        rows = self.fetch(sql=LINE_EXITS, params=(line_key,))
        named = {(str(at), str(off)): str(lines) for at, off, lines in rows}
        found = found_exits(tails=tails, line=line, origin=origin)
        return tuple(
            LineExit(
                junction=at,
                off=off,
                lines=tuple(named.get((at, off), '').split()),
                trips=trips,
            )
            for (at, off), trips in sorted(found.items())
        )

    def reach_input(
        self, *, key: StripKey, origin: StopPlace,
    ) -> ReachInput | None:
        """
        Gather the runs through a strip's From and the places they reach.

        Parameters
        ----------
        key : StripKey
            The From and which runs feed the strip.
        origin : StopPlace
            The From place.

        Returns
        -------
        ReachInput | None
            The From's station, its tails, their places and stops; None
            for an old file. A strip following a line has each tail cut
            before its first station off the line (see `_line_of`), and
            the ways off it as exits.
        """
        if not self.has_network():
            return None
        station = self.station_for(stop_id=key.station)
        tails = self.tails(station=station, key=key)
        line = self._line_of(key=key)
        exits = self._exits_of(key=key, tails=tails, line=line, origin=station)
        if line:
            tails = cut_to_line(tails=tails, line=line)
        named = {station, *(each.off for each in exits)}.union(
            *(tail.before + tail.after for tail in tails),
        )
        places = self.places(stations=named)
        return ReachInput(
            origin=(
                origin
                if origin.stop_id == station
                else places.get(station, origin)
            ),
            tails=tails,
            places=places,
            station_of=self.station_of(stations=places),
            exits=exits,
        )

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()
