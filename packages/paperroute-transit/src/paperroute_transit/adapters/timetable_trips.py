"""Trip, pattern and label queries on the timetable file."""

from collections.abc import Collection
from datetime import date, timedelta

from paperroute_transit.adapters.pattern_rows import (
    JOINED_TRIPS,
    LABELS_BY_ALIAS,
    LABELS_BY_TRIP,
    PATTERN_STOPS,
    RUNNING_DAYS,
    TURNED_TRIPS,
    pattern_of,
)
from paperroute_transit.adapters.route_trip_rows import ROUTE_TRIPS
from paperroute_transit.adapters.timetable_base import (
    BY_ALIAS,
    BY_TRIP,
    LABEL_LIMIT,
    NO_ALONG,
    STOP_ALONG,
    TimetableBase,
    to_point,
)
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.route_trips import RouteTrip


class TripQueries(TimetableBase):
    """Trip, pattern, route-trip and label queries."""

    def resolve_trips(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, list[tuple[object, ...]]]:
        """
        Look up the trips behind requested IDs.

        A requested ID can be a trip, an alias of other trips, or both,
        when a bundle trip was only partly replaced by a feed trip.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, list[tuple[object, ...]]]
            Requested ID to its candidate rows (requested ID, trip ID,
            route, route type, agency, shape ID, direction ID, route
            ID). The trip with that ID comes first, then aliased trips in
            trip ID order.
        """
        shape = self.shape_column()
        found: dict[str, list[tuple[object, ...]]] = {}
        for sql in (BY_TRIP, BY_ALIAS):
            for row in self.fetch_in(sql=sql, ids=trip_ids, shape=shape):
                found.setdefault(str(row[0]), []).append(row)
        return found

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Stops, shape and running dates of trips.

        An alias can stand for several trips. Its dates are all the dates
        any of them runs, and its stops and shape come from the first one
        that runs on those dates.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.
        around : date
            Service date; the day before is included as well.

        Returns
        -------
        dict[str, TripPattern]
            Patterns by requested ID; unknown trips are absent.
        """
        found = self.resolve_trips(trip_ids=trip_ids)
        real = {str(row[1]) for rows in found.values() for row in rows}
        days = self.running_days(trip_ids=real, around=around)
        calls = self.call_stops(trip_ids=real)
        nexts = self.joined_trips(trip_ids=real)
        return {
            requested: pattern_of(
                requested=requested,
                rows=rows,
                days=days,
                calls=(calls, nexts),
                classify=self.classify,
            )
            for requested, rows in found.items()
        }

    def joined_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Find the trip each trip's vehicle runs on through as.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, str]
            Joined trip by trip ID, for joins onto passenger trips; empty
            for a file without joins.
        """
        features = self.features()
        if not features.joins:
            return {}
        sql = JOINED_TRIPS + (' AND n.service = 1' if features.service else '')
        return {
            str(trip): str(joined)
            for trip, joined in self.fetch_in(sql=sql, ids=trip_ids)
        }

    def turned_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Find the trip each trip's vehicle turns back as.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, str]
            Next trip after a turnaround by trip ID; empty for a file
            without trip_turns.
        """
        if not self.features().turns:
            return {}
        return {
            str(trip): str(turned)
            for trip, turned in self.fetch_in(sql=TURNED_TRIPS, ids=trip_ids)
        }

    def route_trips(
        self, *, route: str, source: str, day: date
    ) -> tuple[RouteTrip, ...]:
        """
        List a route's passenger trips on a service day and the day before.

        Parameters
        ----------
        route : str
            Route short name.
        source : str
            Feed source the route belongs to, as the city's classifier
            sorts it; trips of a same-named route on another feed source
            are left out.
        day : date
            Later of the two service days.

        Returns
        -------
        tuple[RouteTrip, ...]
            The trips, earlier service day first and then by first
            departure.
        """
        before = day - timedelta(days=1)
        rows = self.fetch(
            sql=ROUTE_TRIPS.format(service=self.service_filter()),
            params=(route, before.isoformat(), day.isoformat()),
        )
        return tuple(
            RouteTrip(
                trip_id=str(trip),
                day=date.fromisoformat(str(when)),
                starts=int(str(first)),
                ends=int(str(last)),
                direction=None if way is None else int(str(way)),
            )
            for trip, when, first, last, way, kind, agency in rows
            if self.classify(
                route_type=int(str(kind)),
                agency_id=str(agency) if agency else None,
                short_name=route,
            ).source == source
        )

    def running_days(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, list[date]]:
        """
        Find the dates trips run on for a service date and the day before.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.
        around : date
            Later of the two service dates.

        Returns
        -------
        dict[str, list[date]]
            Trip ID to its dates, earliest first.
        """
        before = around - timedelta(days=1)
        rows = self.fetch_in(
            sql=RUNNING_DAYS,
            ids=trip_ids,
            lead=(before.isoformat(), around.isoformat()),
        )
        days: dict[str, list[date]] = {}
        for trip, day in rows:
            days.setdefault(str(trip), []).append(
                date.fromisoformat(str(day))
            )
        return days

    def call_stops(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, list[PatternStop]]:
        """
        List the stops of trips in running order.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, list[PatternStop]]
            Trip ID to its calls.
        """
        calls: dict[str, list[PatternStop]] = {}
        rows = self.fetch_in(
            sql=PATTERN_STOPS,
            ids=trip_ids,
            along=STOP_ALONG if self.features().stop_along else NO_ALONG,
        )
        for trip, arr, dep, lon, lat, station, name, passes, along in rows:
            calls.setdefault(str(trip), []).append(
                PatternStop(
                    arrives=int(str(arr)),
                    departs=int(str(dep)),
                    point=to_point(
                        lon=lon,
                        lat=lat,
                        projection=self.projection,
                    ),
                    station=str(station),
                    name=str(name),
                    passes=bool(passes),
                    along=None if along is None else float(str(along)),
                )
            )
        return calls

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Route short names by trip or alias.

        Answers, including that an ID is unknown, are kept until the file
        is replaced or the cache outgrows its limit, so repeat lookups
        don't query.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, str]
            Short name by ID; unknown IDs are absent.
        """
        self.labels_checked()
        missing = {trip for trip in trip_ids if trip not in self.labels}
        result = {
            trip: label
            for trip in trip_ids
            if (label := self.labels.get(trip))
        }
        found = self.look_up_labels(trip_ids=missing)
        if len(self.labels) > LABEL_LIMIT:
            self.labels.clear()
        self.labels.update({trip: found.get(trip, '') for trip in missing})
        return result | found

    def labels_checked(self) -> None:
        """Forget the cached labels when the file has been replaced."""
        self.fetch(sql='SELECT 1', params=())
        if self.identity != self.labels_id:
            self.labels.clear()
            self.labels_id = self.identity

    def look_up_labels(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, str]:
        """
        Query route short names by trip, then by alias.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, str]
            Short name by ID.
        """
        found = {
            str(trip): str(label)
            for trip, label in self.fetch_in(
                sql=LABELS_BY_TRIP, ids=trip_ids
            )
        }
        for alias, label in self.fetch_in(
            sql=LABELS_BY_ALIAS, ids=set(trip_ids) - found.keys()
        ):
            found.setdefault(str(alias), str(label))
        return found
