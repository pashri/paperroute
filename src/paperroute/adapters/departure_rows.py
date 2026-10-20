"""Turn timetable departure rows into Departures."""

from datetime import date

from paperroute.core.model import (
    CarryOn,
    Departure,
    TripFacts,
    kind_for,
    mode_for,
    service_time,
)


def to_departure(*, row: tuple[object, ...], day: date) -> Departure:
    """
    Turn a query row into a Departure.

    Parameters
    ----------
    row : tuple[object, ...]
        DEPARTURES or WHOLE_STOP row.
    day : date
        Service date the row's times count from.

    Returns
    -------
    Departure
        The departure.
    """
    (trip, route, route_type, agency, headsign, platform, oseq, ostop,
     dseq, dstop, dep, arr, aliases) = row[:13]
    return Departure(
        trip_id=str(trip),
        aliases=tuple(sorted(str(aliases).split())) if aliases else (),
        route=str(route),
        mode=mode_for(
            route_type=int(str(route_type)),
            agency_id=str(agency) if agency else None,
        ),
        headsign=str(headsign),
        platform=str(platform),
        origin_seq=int(str(oseq)),
        origin_stop=str(ostop),
        destination_seq=int(str(dseq)),
        destination_stop=str(dstop),
        departs=service_time(day=day, seconds=int(str(dep))),
        arrives=service_time(day=day, seconds=int(str(arr))),
        service_day=day,
        shape_id=str(row[13]) if row[13] else None,
        facts=facts_of(row=row),
    )


def facts_of(*, row: tuple[object, ...]) -> TripFacts:
    """
    Read a departure row's route kind and carry-on.

    Parameters
    ----------
    row : tuple[object, ...]
        DEPARTURES or WHOLE_STOP row.

    Returns
    -------
    TripFacts
        Kind of the route and the service the vehicle runs next, None
        when the row has none.
    """
    route, route_type, agency = row[1:4]
    then, then_to, then_trip = row[14:17]
    return TripFacts(
        kind=kind_for(
            route_type=int(str(route_type)),
            agency_id=str(agency) if agency else None,
            short_name=str(route),
        ),
        then=(
            None
            if then is None
            else CarryOn(
                route=str(then),
                headsign=str(then_to),
                trip_id=str(then_trip) if then_trip else '',
            )
        ),
    )
