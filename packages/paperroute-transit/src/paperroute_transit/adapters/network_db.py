"""Network repository: chooser columns, line stations and station runs."""

import json
import sqlite3
from collections.abc import Collection, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, cast

from paperroute_device.adapters.sqlite_ro import (
    Identity,
    ReadOnlyDatabase,
    file_identity,
)
from paperroute_transit.adapters.network_lines import LineTables, Row
from paperroute_transit.adapters.timetable_db import DEFAULT_PATH, to_point
from paperroute_transit.core.chooser import (
    Choice,
    ChooserPath,
    Column,
    Level,
    kind_specs,
)
from paperroute_transit.core.city import City, LineGroup
from paperroute_transit.core.model import StopPlace, until_revisit
from paperroute_transit.core.modes import ModeSpec, line_modes
from paperroute_transit.core.reach import (
    ReachInput,
    RunTail,
    StripKey,
    cut_to_line,
    every_route,
    merge_tails,
)

NETWORK_TABLES: Final[tuple[str, ...]] = (
    'runs', 'run_stops', 'chooser_routes', 'chooser_towards',
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
REPLACEMENT_LINES: Final[str] = """
SELECT r.line, r.line, COALESCE(t.name, r.line), ''
FROM (SELECT DISTINCT line FROM chooser_routes WHERE kind = :kind) r
LEFT JOIN chooser_routes t ON t.kind = :replaces AND t.number = r.line
ORDER BY COALESCE(t.sort, 1000000), r.line
"""
HAS_REPLACEMENTS: Final[str] = (
    'SELECT 1 FROM chooser_routes WHERE kind = ? LIMIT 1'
)
REPLACEMENTS_OF: Final[str] = (
    ROUTE_COLUMNS + 'WHERE kind = ? AND line = ?' + ROUTE_ORDER
)
REPLACEMENTS_LABEL: Final[str] = 'Trackwork'
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


def kind_of(
    *, path: ChooserPath, modes: Sequence[ModeSpec],
) -> ModeSpec | None:
    """
    Read the kind a chooser path starts with.

    Parameters
    ----------
    path : ChooserPath
        Chooser path.
    modes : Sequence[ModeSpec]
        The city's modes.

    Returns
    -------
    ModeSpec | None
        The spec of its first, KIND, step's mode; None when it has none
        or the KIND column does not offer the mode.
    """
    if not path or path[0][0] is not Level.KIND:
        return None
    return next(
        (spec for spec in kind_specs(modes=modes) if spec.id == path[0][1]),
        None,
    )


def replacing_spec(
    *, modes: Sequence[ModeSpec], under: str,
) -> ModeSpec | None:
    """
    Find the mode whose routes stand in for lines, listed under a mode.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        The city's modes.
    under : str
        The mode the replacements row is listed under.

    Returns
    -------
    ModeSpec | None
        The first spec under that mode with `replaces` set, or None.
    """
    return next(
        (
            spec
            for spec in modes
            if spec.under == under and spec.replaces is not None
        ),
        None,
    )


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


def ruled_off(
    *, lines: Sequence[Choice], group_of: LineGroup,
) -> tuple[Choice, ...]:
    """
    Put a rule above each line that starts a new group.

    Parameters
    ----------
    lines : Sequence[Choice]
        Line choices in order.
    group_of : LineGroup
        The group each line is listed in.

    Returns
    -------
    tuple[Choice, ...]
        The same lines, each one whose group differs from the line before
        it with ``rule_above``.
    """
    groups = [group_of(number=line.badge) for line in lines]
    return tuple(
        replace(line, rule_above=True) if index and group != groups[index - 1]
        else line
        for index, (line, group) in enumerate(zip(lines, groups))
    )


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
    city : City
        The city: its modes, line groups and the projection stop points
        are placed in.
    path : Path | None
        Timetable file; None for the one on the device.
    connection : sqlite3.Connection | None
        Connection to read instead of the file. It is never reopened, and
        close() closes it.
    """

    def __init__(
        self,
        *,
        city: City,
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.city = city
        self.projection = city.projection
        self.line_kinds = line_modes(modes=city.modes)
        self.db = ReadOnlyDatabase(
            path=path or DEFAULT_PATH,
            connection=connection,
            probe='SELECT 1 FROM stops LIMIT 1',
            label='network',
            allow_missing=True,
        )
        self.schema: dict[
            tuple[str, ...], tuple[Identity | None, bool]
        ] = {}
        self.lines = LineTables(
            fetch=self.fetch,
            has_tables=self._has_tables,
            projection=city.projection,
            line_kinds=self.line_kinds,
        )

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
        return self.lines.has_lines()

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
        spec = kind_of(path=path, modes=self.city.modes)
        if spec is None:
            return None
        return self._level_next(path=path, spec=spec)

    def _level_next(
        self, *, path: ChooserPath, spec: ModeSpec,
    ) -> Column | None:
        """
        Work out the column after a path's last step, given its mode.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far, not empty.
        spec : ModeSpec
            The mode the path starts with.

        Returns
        -------
        Column | None
            The next column, possibly empty; None when nothing follows.
        """
        kind = spec.id
        replacing = replacing_spec(modes=self.city.modes, under=kind)
        level, key = path[-1]
        match level:
            case Level.KIND:
                return self._kind_next(spec=spec)
            case Level.GROUP:
                return self.route_column(
                    sql=ROUTES_IN_GROUP, params=(kind, key),
                )
            case Level.LINE | Level.STATION if spec.by_line:
                return self.station_first_column(path=path, kind=kind)
            case Level.REPLACEMENTS | Level.LINE if replacing is not None:
                return self._replacement_next(
                    level=level, key=key, spec=replacing,
                )
            case Level.ROUTE | Level.LINE:
                return self.towards_column(route_key=key)
        return None

    def _replacement_next(
        self, *, level: Level, key: str, spec: ModeSpec,
    ) -> Column:
        """
        Work out the column after the replacements row or one of its lines.

        Parameters
        ----------
        level : Level
            REPLACEMENTS or LINE.
        key : str
            The line's key.
        spec : ModeSpec
            The mode whose routes stand in for lines.

        Returns
        -------
        Column
            The replacement lines, or the routes replacing a line.
        """
        if level is Level.REPLACEMENTS:
            return self.replacement_lines(spec=spec)
        return self.route_column(sql=REPLACEMENTS_OF, params=(spec.id, key))

    def _kind_next(self, *, spec: ModeSpec) -> Column:
        """
        Work out the column after a kind.

        Parameters
        ----------
        spec : ModeSpec
            The kind chosen, one the KIND column offers.

        Returns
        -------
        Column
            Groups for a mode whose route numbers are tagged, lines for
            one chosen by line and routes for any other.
        """
        if spec.tagged:
            return self.group_column(kind=spec.id)
        if spec.by_line:
            return self.line_column(kind=spec.id)
        return self.route_column(sql=ROUTES_OF_KIND, params=(spec.id,))

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
                key=spec.id, label=spec.label, badge=spec.letter,
                detail=unit_count(
                    count=counts[spec.id],
                    unit='line' if spec.by_line else 'route',
                ),
            )
            for spec in kind_specs(modes=self.city.modes)
            if spec.id in counts
        )
        return Column(level=Level.KIND, items=items)

    def group_column(self, *, kind: str) -> Column:
        """
        The GROUP column of a kind: its hundreds groups, counted.

        Parameters
        ----------
        kind : str
            A mode whose route numbers are tagged.

        Returns
        -------
        Column
            Groups in group order, ending with a replacements row keyed
            by the mode listed under this one whose routes stand in for
            lines, when it has any routes.
        """
        rows = self.fetch(sql=GROUPS, params=(kind,))
        items = tuple(
            Choice(key=str(grp), label=str(grp), detail=str(count))
            for grp, count in rows
        )
        replacing = replacing_spec(modes=self.city.modes, under=kind)
        if replacing is not None and self.fetch(
            sql=HAS_REPLACEMENTS, params=(replacing.id,),
        ):
            items = (*items, Choice(
                key=replacing.id, label=REPLACEMENTS_LABEL, rule_above=True,
                level=Level.REPLACEMENTS,
            ))
        return Column(level=Level.GROUP, items=items)

    def replacement_lines(self, *, spec: ModeSpec) -> Column:
        """
        The LINE column of a replacing mode: the lines it stands in for.

        Parameters
        ----------
        spec : ModeSpec
            The mode whose routes stand in for lines.

        Returns
        -------
        Column
            Lines in line order, each group of lines ruled off.
        """
        rows = self.fetch(
            sql=REPLACEMENT_LINES,
            params={'kind': spec.id, 'replaces': spec.replaces},
        )
        lines = ruled_off(
            lines=route_choices(rows=rows),
            group_of=self.city.catalogue.line_group,
        )
        return Column(level=Level.LINE, items=lines)

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

    def line_column(self, *, kind: str) -> Column:
        """
        The LINE column of a kind, each group of lines ruled off.

        Parameters
        ----------
        kind : str
            A mode chosen by line.

        Returns
        -------
        Column
            Lines in sort order.
        """
        rows = self.fetch(sql=ROUTES_OF_KIND, params=(kind,))
        lines = ruled_off(
            lines=route_choices(rows=rows),
            group_of=self.city.catalogue.line_group,
        )
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
        self, *, path: ChooserPath, kind: str,
    ) -> Column | None:
        """
        The STATION or TO column after a station-first line or station.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far, ending in a LINE or STATION step.
        kind : str
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
            return self.lines.station_column(line_key=key)
        if path[-2][0] is not Level.LINE:
            return None
        return self.lines.to_column(
            line_key=path[-2][1], origin=key, kind=kind,
        )

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
                point=to_point(
                    lon=lon,
                    lat=lat,
                    projection=self.projection,
                ),
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
                point=to_point(
                    lon=lon,
                    lat=lat,
                    projection=self.projection,
                ),
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
            'any_route': every_route(key=key, line_kinds=self.line_kinds),
            'routes': json.dumps(list(key.routes)),
        })
        return merged_tails(rows=rows)

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
            before its first station off the line, and the ways off it as
            exits (see `LineTables`).
        """
        if not self.has_network():
            return None
        station = self.station_for(stop_id=key.station)
        tails = self.tails(station=station, key=key)
        line = self.lines.line_of(key=key)
        exits = self.lines.exits_of(
            key=key, tails=tails, line=line, origin=station,
        )
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
