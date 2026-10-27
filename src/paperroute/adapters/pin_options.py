"""The choices a pin's editor offers, read from the timetable file."""

import json
import math
from collections.abc import Collection, Iterable
from operator import itemgetter
from typing import Final

from paperroute.adapters.change_rows import (
    BETWEEN,
    REACHED,
    change_box,
    nearest_changes,
)
from paperroute.adapters.network_db import (
    ROUTE_COLUMNS,
    ROUTE_ORDER,
    SqliteNetworkRepository,
    route_choices,
)
from paperroute.adapters.network_lines import Row
from paperroute.adapters.timetable_db import to_point
from paperroute.build.catalogue import route_order
from paperroute.core.chooser import UNPICKED, Choice, focusable
from paperroute.core.geo import to_lonlat
from paperroute.core.model import STATION_FIRST, RouteKind, StopPlace
from paperroute.core.pin_choices import KIND_NAMES, ChangeStop, StopMatch
from paperroute.core.reach import RowKind, StripKey, build_reach
from paperroute.core.route_colours import RouteColour, common_colours
from paperroute.core.strip_times import BOARD_KINDS

SEARCH_LIMIT: Final[int] = 20
MIN_SEARCH: Final[int] = 2
SHOWN_ROUTES: Final[int] = 6
NEARBY_M: Final[float] = 400
NEARBY_COUNT: Final[int] = 5
LAST: Final[int] = 1_000_000
FROM_TAG: Final[str] = 'ALL'
SEARCH: Final[str] = r"""
SELECT stop_id, name, lon, lat FROM stops
WHERE name LIKE :any ESCAPE '\'
  AND EXISTS (SELECT 1 FROM run_stops s WHERE s.station_id = stops.stop_id)
ORDER BY name LIKE :start ESCAPE '\' DESC,
  name LIKE :word ESCAPE '\' DESC, LENGTH(name), name
LIMIT :limit
"""
SERVED: Final[str] = """
SELECT s.station_id, r.kind, r.route, MIN(COALESCE(c.sort, :last))
FROM run_stops s JOIN runs r ON r.run_id = s.run_id
  LEFT JOIN chooser_routes c ON c.kind = r.kind AND c.number = r.route
WHERE s.station_id IN (SELECT value FROM json_each(:ids))
GROUP BY s.station_id, r.kind, r.route
"""
PLACE: Final[str] = (
    'SELECT stop_id, name, lon, lat FROM stops WHERE stop_id = ?'
)
TRIP_STOP: Final[str] = """
SELECT p.stop_id, p.name, p.lon, p.lat FROM stops s
JOIN stops p ON p.stop_id = COALESCE(NULLIF(s.parent_station, ''), s.stop_id)
WHERE s.stop_id = ?
"""
STATION_LINES: Final[str] = (
    ROUTE_COLUMNS
    + """
WHERE kind = :kind AND route_key IN (
  SELECT line_key FROM line_stations WHERE station_id IN (
    SELECT :station
    UNION
    SELECT kept_id FROM line_aliases WHERE station_id = :station))
"""
    + ROUTE_ORDER
)
STOP_LINES: Final[str] = (
    ROUTE_COLUMNS
    + """
WHERE kind = :kind AND number IN (
  SELECT r.route FROM run_stops s JOIN runs r ON r.run_id = s.run_id
  WHERE s.station_id = :station AND r.kind = :kind)
"""
    + ROUTE_ORDER
)
FILTERS: Final[str] = """
SELECT r.route
FROM run_stops s JOIN runs r ON r.run_id = s.run_id
  LEFT JOIN chooser_routes c ON c.kind = r.kind AND c.number = r.route
WHERE s.station_id = :station
  AND r.kind IN (SELECT value FROM json_each(:kinds))
GROUP BY r.route
ORDER BY MIN(COALESCE(c.sort, :last)), r.route
"""
COLOURS: Final[str] = """
SELECT short_name, color, text_color, COUNT(*) FROM routes
WHERE short_name IN (SELECT value FROM json_each(:names))
GROUP BY short_name, color, text_color
"""
CLOSE: Final[str] = """
SELECT stop_id, name, lon, lat FROM stops
WHERE lon BETWEEN :west AND :east AND lat BETWEEN :south AND :north
  AND stop_id != :own
  AND EXISTS (
    SELECT 1 FROM run_stops s JOIN runs r ON r.run_id = s.run_id
    WHERE s.station_id = stops.stop_id
      AND (:any_kind OR r.kind = :kind))
"""


def escaped(*, text: str) -> str:
    """
    Escape the characters LIKE treats as patterns.

    Parameters
    ----------
    text : str
        Text to match literally.

    Returns
    -------
    str
        The text with a backslash before each backslash, percent sign
        and underscore.
    """
    return (
        text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    )


def place_of(*, row: Row) -> StopPlace:
    """
    Make a place from a stop ID, name, longitude and latitude.

    Parameters
    ----------
    row : Row
        Stops columns.

    Returns
    -------
    StopPlace
        The place.
    """
    stop_id, name, lon, lat = row
    return StopPlace(
        stop_id=str(stop_id), name=str(name), point=to_point(lon=lon, lat=lat),
    )


def kind_named(*, name: object) -> RouteKind | None:
    """
    Read a run's kind.

    Parameters
    ----------
    name : object
        The runs table's kind column.

    Returns
    -------
    RouteKind | None
        The kind; None when it is not one that is offered.
    """
    return next((kind for kind in KIND_NAMES if kind == name), None)


def served_by(
    *, rows: Iterable[Row],
) -> dict[str, list[tuple[int, int, str]]]:
    """
    Group what serves each station.

    Parameters
    ----------
    rows : Iterable[Row]
        Station ID, kind, route and the route's sort position.

    Returns
    -------
    dict[str, list[tuple[int, int, str]]]
        For each station, the position of the kind in `KIND_NAMES`, the
        route's sort position and the route; rows of an unoffered kind
        are left out.
    """
    order = list(KIND_NAMES)
    served: dict[str, list[tuple[int, int, str]]] = {}
    for station, kind_name, route, pos in rows:
        kind = kind_named(name=kind_name)
        if kind is not None:
            served.setdefault(str(station), []).append(
                (order.index(kind), int(str(pos)), str(route)),
            )
    return served


def call_order(
    call: tuple[int, int, str],
) -> tuple[bool, int, tuple[int, tuple[tuple[int, int | str], ...]]]:
    """
    Sort a served route: rail and ferry kinds first, then by kind.

    A station's bus stands share its ID, so without this a busy station
    lists only buses and reads as a bus stop.

    Parameters
    ----------
    call : tuple[int, int, str]
        Kind position, sort position and route, as `served_by` gives.

    Returns
    -------
    tuple[bool, int, tuple[int, tuple[tuple[int, int | str], ...]]]
        Whether the kind comes after stations' own kinds, the kind
        position and the route's `route_order`.
    """
    kind = list(KIND_NAMES)[call[0]]
    return (
        kind not in STATION_FIRST,
        call[0],
        route_order(number=call[2]),
    )


def match_of(
    *, place: StopPlace, calls: Iterable[tuple[int, int, str]],
) -> StopMatch:
    """
    Make the search result for a place.

    Parameters
    ----------
    place : StopPlace
        The place.
    calls : Iterable[tuple[int, int, str]]
        What serves it, as `served_by` gives.

    Returns
    -------
    StopMatch
        The place with its kinds in `KIND_NAMES` order, and up to
        `SHOWN_ROUTES` route numbers, in `call_order`.
    """
    ordered = sorted(calls, key=call_order)
    kinds = list(KIND_NAMES)
    return StopMatch(
        place=place,
        kinds=tuple(
            kinds[index] for index in sorted({call[0] for call in ordered})
        ),
        routes=tuple(dict.fromkeys(c[2] for c in ordered))[:SHOWN_ROUTES],
    )


def unique_by_badge(*, lines: Iterable[Choice]) -> tuple[Choice, ...]:
    """
    Keep the first choice of each route number.

    Parameters
    ----------
    lines : Iterable[Choice]
        Choices in order.

    Returns
    -------
    tuple[Choice, ...]
        The choices, without a later one of a number already seen.
    """
    seen: dict[str, Choice] = {}
    for line in lines:
        seen.setdefault(line.badge, line)
    return tuple(seen.values())


def text_or_none(*, value: object) -> str | None:
    """
    Read a nullable text column.

    Parameters
    ----------
    value : object
        The column's value.

    Returns
    -------
    str | None
        The text; None for NULL.
    """
    return None if value is None else str(value)


class SqlitePinOptions:
    """
    The choices a pin's editor offers, read from the timetable file.

    Every answer is empty on a file without the run and chooser tables.

    Parameters
    ----------
    network : SqliteNetworkRepository
        The network repository over the timetable file.
    """

    def __init__(self, *, network: SqliteNetworkRepository) -> None:
        self.network = network

    def search(self, *, text: str) -> tuple[StopMatch, ...]:
        """
        Find stops and stations that runs serve by name.

        Parameters
        ----------
        text : str
            Part of a name; trimmed, and at least `MIN_SEARCH` long.

        Returns
        -------
        tuple[StopMatch, ...]
            At most `SEARCH_LIMIT`: names that start with the text, then
            names with a word that does, then shorter names, then by
            name.
        """
        text = text.strip()
        if len(text) < MIN_SEARCH or not self.network.has_network():
            return ()
        safe = escaped(text=text)
        found = self.network.fetch(sql=SEARCH, params={
            'any': f'%{safe}%',
            'start': f'{safe}%',
            'word': f'% {safe}%',
            'limit': SEARCH_LIMIT,
        })
        places = [place_of(row=row) for row in found]
        rows = self.network.fetch(sql=SERVED, params={
            'ids': json.dumps([place.stop_id for place in places]),
            'last': LAST,
        })
        served = served_by(rows=rows)
        return tuple(
            match_of(place=place, calls=served.get(place.stop_id, ()))
            for place in places
        )

    def place(self, *, stop_id: str) -> StopPlace | None:
        """
        Read a stop or station.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        StopPlace | None
            The place; None when the timetable does not know it.
        """
        rows = self.network.fetch(sql=PLACE, params=(stop_id,))
        return place_of(row=rows[0]) if rows else None

    def trip_stop(self, *, stop_id: str) -> StopPlace | None:
        """
        Map a Trip Planner stop to the editor's station-level place.

        Parameters
        ----------
        stop_id : str
            Trip Planner stop ID.

        Returns
        -------
        StopPlace | None
            The stop, lifted to its parent station; None when unknown.
        """
        rows = self.network.fetch(sql=TRIP_STOP, params=(stop_id,))
        return place_of(row=rows[0]) if rows else None

    def kinds_at(self, *, stop_id: str) -> tuple[RouteKind, ...]:
        """
        List the kinds of route that have a line or route at a stop.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        tuple[RouteKind, ...]
            Kinds in `KIND_NAMES` order.
        """
        return tuple(
            kind
            for kind in KIND_NAMES
            if self.lines_at(stop_id=stop_id, kind=kind)
        )

    def lines_at(self, *, stop_id: str, kind: RouteKind) -> tuple[Choice, ...]:
        """
        List the lines or routes of a kind that serve a stop.

        A station-first kind lists the lines whose stations hold the
        station, or a station merged into it. Any other kind lists the
        routes of that kind's runs through the stop.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.
        kind : RouteKind
            Kind of route.

        Returns
        -------
        tuple[Choice, ...]
            Choices in chooser order, one per route number.
        """
        if not self.network.has_network():
            return ()
        station = self.network.station_for(stop_id=stop_id)
        if kind in STATION_FIRST:
            if not self.network.has_lines():
                return ()
            sql = STATION_LINES
        else:
            sql = STOP_LINES
        rows = self.network.fetch(
            sql=sql, params={'kind': str(kind), 'station': station},
        )
        return unique_by_badge(lines=route_choices(rows=rows))

    def destinations(
        self, *, origin: StopPlace, kind: RouteKind, line: Choice,
    ) -> tuple[StopPlace, ...]:
        """
        List the places the device offers as the To.

        Parameters
        ----------
        origin : StopPlace
            The From.
        kind : RouteKind
            Kind of route.
        line : Choice
            The line or route, from `lines_at`.

        Returns
        -------
        tuple[StopPlace, ...]
            For a station-first kind, the stations of the line's TO
            column the cursor can rest on, in list order. For any other
            kind, the strip rows after and above the From that the TO
            knob can stop on. Empty for a file without the network or
            line tables.
        """
        if kind in STATION_FIRST:
            return self.line_stations(origin=origin, kind=kind, line=line)
        data = self.network.reach_input(
            key=StripKey(
                station=origin.stop_id, kind=kind, routes=(line.badge,),
            ),
            origin=origin,
        )
        if data is None:
            return ()
        rows = build_reach(data=data).rows
        return tuple({
            row.place.stop_id: row.place
            for row in rows
            if row.kind not in UNPICKED and row.kind is not RowKind.ORIGIN
        }.values())

    def line_stations(
        self, *, origin: StopPlace, kind: RouteKind, line: Choice,
    ) -> tuple[StopPlace, ...]:
        """
        List the stations the TO column of a line offers from a station.

        Parameters
        ----------
        origin : StopPlace
            The From.
        kind : RouteKind
            A station-first kind.
        line : Choice
            The line.

        Returns
        -------
        tuple[StopPlace, ...]
            The column's rows the cursor can rest on, without the From;
            empty for a file without the line tables or a line that does
            not hold the From.
        """
        if not self.network.has_network() or not self.network.has_lines():
            return ()
        column = self.network.lines.to_column(
            line_key=line.key, origin=origin.stop_id, kind=kind,
        )
        if column is None:
            return ()
        return tuple(
            row.place
            for row in column.items
            if focusable(choice=row) and row.tag != FROM_TAG and row.place
        )

    def filters_at(
        self, *, stop_id: str, kind: RouteKind,
    ) -> tuple[str, ...]:
        """
        List the route numbers a pin's departures can be limited to.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.
        kind : RouteKind
            Kind of route.

        Returns
        -------
        tuple[str, ...]
            Route numbers of the runs through the stop of every kind
            that boards with this one, in chooser order.
        """
        if not self.network.has_network():
            return ()
        rows = self.network.fetch(sql=FILTERS, params={
            'station': self.network.station_for(stop_id=stop_id),
            'kinds': json.dumps([str(each) for each in BOARD_KINDS[kind]]),
            'last': LAST,
        })
        return tuple(str(route) for route, in rows)

    def nearby(
        self, *, place: StopPlace, kind: RouteKind | None,
    ) -> tuple[StopPlace, ...]:
        """
        List stops close to a place that runs of a kind serve.

        Parameters
        ----------
        place : StopPlace
            The place, which is left out of the answer.
        kind : RouteKind | None
            Kind of route, or None for any.

        Returns
        -------
        tuple[StopPlace, ...]
            At most `NEARBY_COUNT` stops within `NEARBY_M` metres,
            nearest first; empty when the place has no point.
        """
        if place.point is None or not self.network.has_network():
            return ()
        x, y = place.point
        west, south = to_lonlat(point=(x - NEARBY_M, y - NEARBY_M))
        east, north = to_lonlat(point=(x + NEARBY_M, y + NEARBY_M))
        rows = self.network.fetch(sql=CLOSE, params={
            'west': west, 'east': east, 'south': south, 'north': north,
            'own': place.stop_id,
            'any_kind': kind is None,
            'kind': None if kind is None else str(kind),
        })
        close = sorted(
            (
                (math.dist(place.point, found.point), found.stop_id, found)
                for found in (place_of(row=row) for row in rows)
                if found.point is not None
            ),
            key=itemgetter(0, 1),
        )
        return tuple(
            found for dist, _, found in close if dist <= NEARBY_M
        )[:NEARBY_COUNT]

    def change_stops(self, *, place: StopPlace) -> tuple[ChangeStop, ...]:
        """
        List the stops a connection can change to from a place.

        Parameters
        ----------
        place : StopPlace
            The place leg 1 is left at.

        Returns
        -------
        tuple[ChangeStop, ...]
            Run-served stops within 500 m, the place included, nearest
            first; empty when the place has no point.
        """
        point = place.point
        if point is None or not self.network.has_network():
            return ()
        rows = self.network.fetch(sql=CLOSE, params=change_box(point=point))
        return nearest_changes(
            place=place,
            point=point,
            found=(place_of(row=row) for row in rows),
        )

    def reached_from(self, *, place: StopPlace) -> tuple[StopPlace, ...]:
        """
        List the stations a run calls at after a place.

        Parameters
        ----------
        place : StopPlace
            The place leg 2 is joined at.

        Returns
        -------
        tuple[StopPlace, ...]
            Stations after the place's station on any run, without it,
            by name and then ID.
        """
        if not self.network.has_network():
            return ()
        rows = self.network.fetch(sql=REACHED, params={
            'station': self.network.station_for(stop_id=place.stop_id),
        })
        return tuple(place_of(row=row) for row in rows)

    def routes_between(
        self, *, origin: StopPlace, destination: StopPlace,
    ) -> tuple[str, ...]:
        """
        List the routes that run from one place to another.

        Parameters
        ----------
        origin : StopPlace
            Place boarded at.
        destination : StopPlace
            Place reached.

        Returns
        -------
        tuple[str, ...]
            Route numbers of runs that list the destination's station
            after the origin's, in chooser order.
        """
        if not self.network.has_network():
            return ()
        rows = self.network.fetch(sql=BETWEEN, params={
            'origin': self.network.station_for(stop_id=origin.stop_id),
            'destination': self.network.station_for(
                stop_id=destination.stop_id,
            ),
            'last': LAST,
        })
        return tuple(str(route) for route, in rows)

    def route_colours(
        self, *, names: Collection[str],
    ) -> dict[str, RouteColour]:
        """
        Read the colours of routes by short name.

        Parameters
        ----------
        names : Collection[str]
            Route short names.

        Returns
        -------
        dict[str, RouteColour]
            Each name's most common valid colours; names without one are
            left out, and the answer is empty for a file without the
            network tables.
        """
        if not self.network.has_network():
            return {}
        rows = self.network.fetch(
            sql=COLOURS, params={'names': json.dumps(sorted(names))},
        )
        return common_colours(
            rows=(
                (
                    str(name),
                    text_or_none(value=fill),
                    text_or_none(value=text),
                    int(str(count)),
                )
                for name, fill, text, count in rows
            ),
        )
