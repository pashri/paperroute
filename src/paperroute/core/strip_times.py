"""Which trips feed a strip, and when they reach its stations."""

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.model import (
    Departure,
    RouteKind,
    service_seconds,
    service_time,
    shifted,
)
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.reach import Reach, RowKind, StripKey, every_route
from paperroute.core.stop_view import clock_text, not_departed

STRIP_LOOKBACK: Final[timedelta] = timedelta(minutes=60)
STRIP_AHEAD: Final[timedelta] = timedelta(minutes=30)
BOARD_KINDS: Final[dict[RouteKind, frozenset[RouteKind]]] = {
    **{kind: frozenset({kind}) for kind in RouteKind},
    RouteKind.BUS: frozenset({
        RouteKind.BUS, RouteKind.SCHOOL, RouteKind.REPLACEMENT,
    }),
}


@dataclass(frozen=True)
class TripInputs:
    """
    The departures and live data a strip's times and vehicles come from.

    Attributes
    ----------
    departures : tuple[LiveDeparture, ...]
        Every departure from the From, with live times applied.
    key : StripKey | None
        Which runs feed the strip; None for no departures.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by trip ID; None when unknown.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed in use.
    now : datetime
        Current time.
    station_of : Mapping[str, str]
        The station ID of each stop under a strip station.
    """

    departures: tuple[LiveDeparture, ...]
    key: StripKey | None
    patterns: Mapping[str, TripPattern | None]
    positions: Mapping[str, PositionsSnapshot]
    now: datetime
    station_of: Mapping[str, str]


def on_strip(*, departure: Departure, key: StripKey) -> bool:
    """
    Say whether a departure feeds a strip.

    Parameters
    ----------
    departure : Departure
        Departure.
    key : StripKey
        Strip kind and routes.

    Returns
    -------
    bool
        True when the key has no kind, or the departure is of its kind
        and, for a kind that takes only its routes, one of them.
    """
    if key.kind is None:
        return True
    if departure.facts.kind != key.kind:
        return False
    return every_route(key=key) or departure.route in key.routes


def strip_departures(
    *, departures: Sequence[Departure], key: StripKey,
) -> tuple[Departure, ...]:
    """
    Keep the departures of a strip's own kind or routes.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    key : StripKey
        Strip kind and routes.

    Returns
    -------
    tuple[Departure, ...]
        The ones that feed the strip, in order.
    """
    return tuple(
        departure
        for departure in departures
        if on_strip(departure=departure, key=key)
    )


def board_departures(
    *, departures: Sequence[Departure], kind: RouteKind | None,
) -> tuple[Departure, ...]:
    """
    Keep the departures a pin's board shows when it has no Destination.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    kind : RouteKind | None
        The pin's kind.

    Returns
    -------
    tuple[Departure, ...]
        Every departure for no kind; otherwise those whose route kind is
        one of `BOARD_KINDS` for it, in order. A Bus board also keeps
        school and replacement buses.
    """
    if kind is None:
        return tuple(departures)
    return tuple(
        departure
        for departure in departures
        if departure.facts.kind in BOARD_KINDS[kind]
    )


def strip_trips(
    *, departures: Sequence[Departure], key: StripKey, now: datetime,
) -> frozenset[str]:
    """
    List the trips whose vehicles the strip draws.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    key : StripKey
        Strip kind and routes.
    now : datetime
        Current time.

    Returns
    -------
    frozenset[str]
        Trip IDs of the strip's departures that left in the last
        `STRIP_LOOKBACK` or leave in the next `STRIP_AHEAD`, and of the
        trips they carry on as; every one.
    """
    return trip_ids(
        departures=window_departures(
            departures=departures, key=key, now=now,
        ),
    )


def trip_ids(*, departures: Iterable[Departure]) -> frozenset[str]:
    """
    List departures' trip IDs and the trips they carry on as.

    Parameters
    ----------
    departures : Iterable[Departure]
        Departures.

    Returns
    -------
    frozenset[str]
        Each departure's trip ID, and its carry-on's trip ID when known.
    """
    found: set[str] = set()
    for departure in departures:
        found.add(departure.trip_id)
        then = departure.facts.then
        if then is not None and then.trip_id:
            found.add(then.trip_id)
    return frozenset(found)


def window_departures(
    *, departures: Sequence[Departure], key: StripKey, now: datetime,
) -> list[Departure]:
    """
    Keep a strip's departures timetabled near now, one per trip.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    key : StripKey
        Strip kind and routes.
    now : datetime
        Current time.

    Returns
    -------
    list[Departure]
        The first departure of each trip leaving from `STRIP_LOOKBACK`
        before now to `STRIP_AHEAD` after, in order.
    """
    start = (now - STRIP_LOOKBACK).timestamp()
    end = (now + STRIP_AHEAD).timestamp()
    kept: dict[str, Departure] = {}
    for departure in strip_departures(departures=departures, key=key):
        if start <= departure.departs.timestamp() <= end:
            kept.setdefault(departure.trip_id, departure)
    return list(kept.values())


def live_strip(*, trips: TripInputs) -> list[LiveDeparture]:
    """
    Keep the live departures that feed the strip.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.

    Returns
    -------
    list[LiveDeparture]
        The strip's departures; none without a key.
    """
    key = trips.key
    if key is None:
        return []
    return [
        item
        for item in trips.departures
        if on_strip(departure=item.departure, key=key)
    ]


def from_call(*, departure: Departure, pattern: TripPattern, origin: str,
              stations: Mapping[str, str]) -> int | None:
    """
    Find the call at which a departure leaves the From.

    Parameters
    ----------
    departure : Departure
        Departure.
    pattern : TripPattern
        Its trip.
    origin : str
        The From station ID.
    stations : Mapping[str, str]
        Station ID by stop ID, for stops under a strip station.

    Returns
    -------
    int | None
        The index of the call at the From at the departure's time, else
        the first call at the From, else None.
    """
    at_from = [
        index
        for index, stop in enumerate(pattern.stops)
        if stations.get(stop.station, stop.station) == origin
    ]
    leaves = service_seconds(
        day=departure.service_day, moment=departure.departs,
    )
    timed = [i for i in at_from if pattern.stops[i].departs == leaves]
    return next(iter(timed or at_from), None)


def arrivals_after(
    *,
    item: LiveDeparture,
    pattern: TripPattern,
    origin: str,
    stations: Mapping[str, str],
) -> Iterator[tuple[str, datetime]]:
    """
    List the stations a departure calls at after the From, with times.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    pattern : TripPattern
        Its trip.
    origin : str
        The From station ID.
    stations : Mapping[str, str]
        Station ID by stop ID, for stops under a strip station.

    Yields
    ------
    tuple[str, datetime]
        Each later call's station and arrival, shifted by the delay.
    """
    departure = item.departure
    start = from_call(
        departure=departure, pattern=pattern, origin=origin, stations=stations,
    )
    if start is None:
        return
    delay = int(item.departs.timestamp() - departure.departs.timestamp())
    for stop in pattern.stops[start + 1:]:
        arrives = service_time(day=departure.service_day, seconds=stop.arrives)
        yield (
            stations.get(stop.station, stop.station),
            shifted(moment=arrives, seconds=delay),
        )


def first_arrivals(*, trips: TripInputs, origin: str
                   ) -> dict[str, datetime]:
    """
    Find the first arrival at each station after the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.

    Returns
    -------
    dict[str, datetime]
        Earliest arrival by station, among the strip's departures not yet
        gone and not cancelled whose patterns are loaded.
    """
    best: dict[str, datetime] = {}
    upcoming = not_departed(
        departures=live_strip(trips=trips), now=trips.now,
    )
    for item in upcoming:
        pattern = trips.patterns.get(item.departure.trip_id)
        if item.cancelled or pattern is None:
            continue
        for station, moment in arrivals_after(
            item=item,
            pattern=pattern,
            origin=origin,
            stations=trips.station_of,
        ):
            if station not in best or moment < best[station]:
                best[station] = moment
    return best


def boardable(*, item: LiveDeparture, trips: TripInputs, origin: str,
              target: str | None) -> bool:
    """
    Say whether a departure from the From can be taken.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    bool
        False when cancelled; with a Destination, True only when the
        trip's loaded pattern calls there after the From.
    """
    if item.cancelled:
        return False
    if target is None:
        return True
    pattern = trips.patterns.get(item.departure.trip_id)
    return pattern is not None and any(
        station == target
        for station, _ in arrivals_after(
            item=item, pattern=pattern, origin=origin,
            stations=trips.station_of,
        )
    )


def next_item(*, trips: TripInputs, origin: str, target: str | None
              ) -> LiveDeparture | None:
    """
    Find the strip's next departure from the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    target : str | None
        The Destination's station ID, or None for any departure.

    Returns
    -------
    LiveDeparture | None
        The departure not yet gone that can be taken and leaves first
        by its live time, or None.
    """
    upcoming = not_departed(
        departures=live_strip(trips=trips), now=trips.now,
    )
    return min(
        (
            item
            for item in upcoming
            if boardable(
                item=item, trips=trips, origin=origin, target=target,
            )
        ),
        key=lambda item: item.departs,
        default=None,
    )


def next_departure(*, trips: TripInputs, origin: str, target: str | None
                   ) -> datetime | None:
    """
    Find when the strip's next departure leaves the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    target : str | None
        The Destination's station ID, or None for any departure.

    Returns
    -------
    datetime | None
        The earliest live departure time among the strip's departures
        not yet gone that can be taken, or None.
    """
    item = next_item(trips=trips, origin=origin, target=target)
    return None if item is None else item.departs


def approach_times(*, trips: TripInputs, origin: str,
                   stations: Collection[str]) -> dict[str, datetime]:
    """
    Find when the From's next departure calls at stations before it.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    stations : Collection[str]
        Station IDs before the From.

    Returns
    -------
    dict[str, datetime]
        Departure time by station for each of the stations the next
        departure (any target) stops at before the From, shifted by its
        live delay; empty when there is none or its pattern is unknown.
    """
    item = next_item(trips=trips, origin=origin, target=None)
    if item is None:
        return {}
    departure = item.departure
    pattern = trips.patterns.get(departure.trip_id)
    start = None if pattern is None else from_call(
        departure=departure, pattern=pattern, origin=origin,
        stations=trips.station_of,
    )
    if pattern is None or start is None:
        return {}
    delay = int(item.departs.timestamp() - departure.departs.timestamp())
    found: dict[str, datetime] = {}
    for stop in pattern.stops[:start]:
        station = trips.station_of.get(stop.station, stop.station)
        if station in stations:
            found[station] = shifted(
                moment=service_time(
                    day=departure.service_day,
                    seconds=(
                        stop.arrives
                        if stop.departs is None
                        else stop.departs
                    ),
                ),
                seconds=delay,
            )
    return found


def approach_text(*, trips: TripInputs, reach: Reach, origin: str
                  ) -> dict[int, str]:
    """
    Give each approach row the time the next departure calls there.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach.
    origin : str
        The From station ID.

    Returns
    -------
    dict[int, str]
        Clock time by approach row; a row the trip skips has none.
    """
    rows = {
        index: row.place.stop_id
        for index, row in enumerate(reach.rows)
        if row.kind == RowKind.APPROACH
    }
    found = approach_times(
        trips=trips, origin=origin, stations=set(rows.values()),
    ) if rows else {}
    return {
        index: clock_text(moment=found[key])
        for index, key in rows.items()
        if key in found
    }
