"""Network queries on a station-first line's own tables."""

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from itertools import groupby
from typing import Final, Protocol

from paperroute.adapters.timetable_db import to_point
from paperroute.core.chooser import Choice, Column, Level
from paperroute.core.model import RouteKind, StopPlace, until_revisit
from paperroute.core.reach import RunTail, StripKey, line_key_of
from paperroute.core.reach_exits import found_exits
from paperroute.core.reach_layout import LineExit

LINE_TABLES: Final[tuple[str, ...]] = ('line_stations', 'line_aliases')
EXIT_TABLES: Final[tuple[str, ...]] = ('line_exits',)
TRACK_TABLES: Final[tuple[str, ...]] = ('line_track',)
LINE_EXITS: Final[str] = (
    'SELECT station_id, off_id, lines FROM line_exits WHERE line_key = ?'
)
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
ON_TRACK: Final[str] = """
WITH own AS (
  SELECT station_id FROM line_stations WHERE line_key = :line
  UNION
  SELECT station_id FROM line_track WHERE line_key = :line
)
SELECT station_id FROM own
UNION
SELECT station_id FROM line_aliases WHERE kept_id IN (SELECT * FROM own)
"""
PASSES: Final[str] = """
SELECT DISTINCT r.run_id, r.stations
FROM run_stops s JOIN runs r ON r.run_id = s.run_id
WHERE s.station_id IN (SELECT value FROM json_each(:stations))
  AND r.kind = :kind
"""
FROM_TAG: Final[str] = 'ALL'
type Row = tuple[object, ...]


class Fetch(Protocol):
    """Runs a query against the current timetable file."""

    def __call__(
        self, *, sql: str, params: tuple[object, ...] | dict[str, object],
    ) -> list[Row]:
        """
        Run a query.

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


class HasTables(Protocol):
    """Says whether the current timetable file has some tables."""

    def __call__(self, *, tables: tuple[str, ...]) -> bool:
        """
        Check for tables.

        Parameters
        ----------
        tables : tuple[str, ...]
            Table names.

        Returns
        -------
        bool
            True for a file with every table.
        """


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


class LineTables:
    """
    Read a station-first line's stations, TO column, track and exits.

    Parameters
    ----------
    fetch : Fetch
        Runs queries against the timetable file.
    has_tables : HasTables
        Says whether the file has some tables.
    """

    def __init__(self, *, fetch: Fetch, has_tables: HasTables) -> None:
        self.fetch = fetch
        self.has_tables = has_tables

    def has_lines(self) -> bool:
        """
        Say whether the file has the line station tables.

        Returns
        -------
        bool
            True for a file with line_stations and line_aliases.
        """
        return self.has_tables(tables=LINE_TABLES)

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

    def line_of(self, *, key: StripKey) -> frozenset[str]:
        """
        List the stations on the line a strip follows.

        Parameters
        ----------
        key : StripKey
            Strip kind and routes.

        Returns
        -------
        frozenset[str]
            The line's stations, the stations its trains run through
            without stopping (line_track, when the file has it), and the
            stations merged into either; empty for a strip that follows
            no line, a line the file does not list, or a file without
            the line tables.
        """
        line_key = line_key_of(key=key)
        if line_key is None or not self.has_lines():
            return frozenset()
        track = self.has_tables(tables=TRACK_TABLES)
        rows = self.fetch(
            sql=ON_TRACK if track else ON_LINE, params={'line': line_key},
        )
        return frozenset(str(station) for station, in rows)

    def exits_of(
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
            The line's stations (see `line_of`); empty for none.
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
        if not line or line_key is None or not self.has_tables(
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
