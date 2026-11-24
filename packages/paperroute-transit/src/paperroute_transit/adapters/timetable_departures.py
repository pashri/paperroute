"""Departure queries on the timetable file."""

from collections.abc import Collection
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import cast

from paperroute_transit.adapters.calendar_rows import (
    SERVICE_DAY_LENGTH,
    service_span,
)
from paperroute_transit.adapters.carry_on_rows import (
    CARRY_ONS,
    carry_on_departure,
    first_to_arrive,
)
from paperroute_transit.adapters.departure_rows import (
    DEPARTURES,
    WHOLE_STOP,
    to_departure,
)
from paperroute_transit.adapters.timetable_base import (
    FROM_CALLS,
    NEXT_JOIN,
    TimetableBase,
)
from paperroute_transit.core.model import (
    HIDDEN_HEADSIGNS,
    Departure,
    Pin,
    ServiceSpan,
    pin_routes,
    service_epoch,
    service_seconds,
)
from paperroute_transit.core.modes import end_point_modes, line_modes
from paperroute_transit.core.reach import StripKey
from paperroute_transit.core.sides import both_sides
from paperroute_transit.core.strip_list import loop_passes
from paperroute_transit.core.strip_times import (
    board_departures,
    filtered,
    strip_departures,
)


class DepartureQueries(TimetableBase):
    """Departure and service-span queries."""

    def day_departures(self, *, pin: Pin, day: date) -> list[Departure]:
        """
        Direct services leaving the pin's From on a service day.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        day : date
            Service day.

        Returns
        -------
        list[Departure]
            The departures `departures` lists for `SERVICE_DAY_LENGTH`
            from the day's start, whose service day is `day`.
        """
        start = service_epoch(day=day, zone=self.zone)
        found = self.departures(
            pin=pin, start=start, end=start + SERVICE_DAY_LENGTH,
        )
        return [item for item in found if item.service_day == day]

    def service_span(self, *, pin: Pin, day: date) -> ServiceSpan | None:
        """
        When a pin's trips run on a service day.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        day : date
            Service day.

        Returns
        -------
        ServiceSpan | None
            The span of the trips `departures` lists that day, up to
            `SERVICE_DAY_LENGTH` after its start; None when there are
            none.
        """
        return service_span(
            departures=self.day_departures(pin=pin, day=day),
            day=day,
            fetch_in=lambda sql, ids: self.fetch_in(sql=sql, ids=ids),
            zone=self.zone,
        )

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Direct services leaving the pin's From between two moments.

        Service days from the day before start to end's day are searched, so
        trips running past midnight are found.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            In departure order, then trip ID. A both-sides pin lists the
            kerb across too, those departures marked `facts.across`.
            With a Destination, only the pin's kind and routes, as on
            its strip: a station's bus stands sit under it too.
            Without one, the departures of the pin's kind (see
            `board_departures`) from any platform of the
            From, each running to its last stop. A
            trip calling at the From several times in a row, such as a
            bus moving between two stands, leaves once, from its last call
            there that takes passengers on. The pin's route filter
            (`Pin.only`) is applied last.
        """
        destination = pin.destination
        first = start.astimezone(self.zone).date()
        last = end.astimezone(self.zone).date()
        days = {first - timedelta(days=1), first, last}
        found = [
            departure
            for day in sorted(days)
            for departure in self.on_day(
                ends=(
                    pin.origin.stop_id,
                    None if destination is None else destination.stop_id,
                ),
                day=day,
                start=start,
                end=end,
            )
        ]
        across = pin.opposite if both_sides(pin=pin) else None
        if across is not None:
            found.extend(
                replace(
                    departure, facts=replace(departure.facts, across=True),
                )
                for day in sorted(days)
                for departure in self.on_day(
                    ends=(across.stop_id, None), day=day, start=start, end=end,
                )
            )
        if destination is None:
            found = list(board_departures(
                departures=found, kind=pin.kind, modes=self.city.modes,
            ))
        else:
            if pin.kind in end_point_modes(modes=self.city.modes):
                found = list(loop_passes(
                    departures=found,
                    from_seqs=self.from_seqs(
                        trip_ids={d.trip_id for d in found},
                        origin=pin.origin.stop_id,
                    ),
                ))
            found = list(strip_departures(
                departures=first_to_arrive(departures=found),
                key=StripKey(
                    station=pin.origin.stop_id,
                    kind=pin.kind,
                    routes=pin_routes(
                        pin=pin,
                        end_points=end_point_modes(modes=self.city.modes),
                    ),
                ),
                line_kinds=line_modes(modes=self.city.modes),
            ))
        return sorted(
            filtered(departures=found, only=pin.only),
            key=lambda departure: (departure.departs, departure.trip_id),
        )

    def from_seqs(
        self, *, trip_ids: Collection[str], origin: str,
    ) -> dict[str, tuple[int, ...]]:
        """
        Read where each trip calls at a From, whatever the day.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.
        origin : str
            From stop or station ID; its platforms count.

        Returns
        -------
        dict[str, tuple[int, ...]]
            Each trip's visits to the From, in order, as the sequence of
            each visit's first call; calls where no one gets on or off
            do not count, and a call straight after another at the From
            is part of that visit.
        """
        found: dict[str, list[int]] = {}
        for trip, seq in self.fetch_in(
            sql=FROM_CALLS,
            ids=trip_ids,
            tail=(origin, origin, origin, origin),
        ):
            found.setdefault(str(trip), []).append(int(cast(int, seq)))
        return {trip: tuple(seqs) for trip, seqs in found.items()}

    def on_day(
        self,
        *,
        ends: tuple[str, str | None],
        day: date,
        start: datetime,
        end: datetime,
    ) -> list[Departure]:
        """
        List departures from one service day within the range.

        Parameters
        ----------
        ends : tuple[str, str | None]
            From and Destination stop or station IDs; no Destination for
            the whole stop.
        day : date
            Service date.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            Unsorted departures, leaving out empty-running trips. With
            a Destination, trips that reach it only by running on as
            another trip are added (see CARRY_ONS).
        """
        features = self.features()
        sql = DEPARTURES if ends[1] is not None else WHOLE_STOP
        params: dict[str, object] = {
            'day': day.isoformat(),
            'origin': ends[0],
            'dest': ends[1],
            'lo': service_seconds(day=day, moment=start, zone=self.zone),
            'hi': service_seconds(day=day, moment=end, zone=self.zone),
        }
        rows = self.fetch(
            sql=sql.format(
                shape=self.shape_column(),
                then=features.next_columns,
                next_join=NEXT_JOIN if features.carry_ons else '',
                service=self.service_filter(),
            ),
            params=params,
        )
        found = [
            to_departure(
                row=row, day=day, zone=self.zone, classify=self.classify,
            )
            for row in rows
        ]
        if ends[1] is not None and features.joins:
            found.extend(
                carry_on_departure(
                    row=row, day=day, zone=self.zone, classify=self.classify,
                )
                for row in self.fetch(
                    sql=CARRY_ONS.format(
                        shape=self.shape_column(),
                        service=self.service_filter(),
                    ),
                    params=params,
                )
            )
        return [
            departure
            for departure in found
            if departure.headsign not in HIDDEN_HEADSIGNS
        ]
