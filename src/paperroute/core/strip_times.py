"""Which trips feed a strip, and when they reach its stations."""

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from itertools import chain
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.stop_view import not_departed
from paperroute.core.strip_model import TripDay, trip_day
from paperroute_device.core.text import clock_text
from paperroute_transit.core.model import (
    Departure,
    RouteKind,
    service_seconds,
    service_time,
    shifted,
)
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.reach import Reach, RowKind, StripKey, every_route

STRIP_LOOKBACK: Final[timedelta] = timedelta(minutes=60)
STRIP_AHEAD: Final[timedelta] = timedelta(minutes=30)
STRIP_HOPS: Final[int] = 6  # joins followed past a trip's last stop
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
        The frame time.
    stale_after : timedelta
        How old a position may be before it is drawn at its timetable
        position instead.
    zone : tzinfo
        Local time zone the times show in.
    station_of : Mapping[str, str]
        The station ID of each stop under a strip station.
    """

    departures: tuple[LiveDeparture, ...]
    key: StripKey | None
    patterns: Mapping[str, TripPattern | None]
    positions: Mapping[str, PositionsSnapshot]
    now: datetime
    stale_after: timedelta
    zone: tzinfo
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


def filtered(
    *,
    departures: Sequence[Departure],
    only: tuple[str, ...],
) -> tuple[Departure, ...]:
    """
    Keep the departures on the chosen routes.

    Parameters
    ----------
    departures : Sequence[Departure]
        Departures to narrow.
    only : tuple[str, ...]
        Route short names to keep; empty keeps every departure.

    Returns
    -------
    tuple[Departure, ...]
        The kept departures, in their original order.
    """
    return tuple(
        departure
        for departure in departures
        if not only or departure.route in only
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
        Each departure's trip ID, and the trip IDs it runs next and runs
        on through as, when known.
    """
    found: set[str] = set()
    for departure in departures:
        found.add(departure.trip_id)
        for carry in (departure.facts.then, departure.facts.onto):
            if carry is not None and carry.trip_id:
                found.add(carry.trip_id)
    return frozenset(found)


def joined_ids(
    *, starts: Collection[str], patterns: Mapping[str, TripPattern | None],
) -> frozenset[str]:
    """
    List the trips loaded patterns run on through as, hop by hop.

    Parameters
    ----------
    starts : Collection[str]
        Trip IDs to start from.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns.

    Returns
    -------
    frozenset[str]
        The trips joined onto, up to `STRIP_HOPS` joins on from each
        starting trip whose patterns are loaded so far.
    """
    found: set[str] = set()
    for trip in starts:
        pattern = patterns.get(trip)
        for _ in range(STRIP_HOPS):
            if pattern is None or pattern.next_trip is None:
                break
            found.add(pattern.next_trip)
            pattern = patterns.get(pattern.next_trip)
    return frozenset(found)


def joined_patterns(
    *,
    pattern: TripPattern,
    patterns: Mapping[str, TripPattern | None],
    day: date,
) -> Iterator[TripPattern]:
    """
    Follow the trips a vehicle runs on through as.

    Parameters
    ----------
    pattern : TripPattern
        The boarded trip.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns.
    day : date
        Service date of the boarded trip.

    Yields
    ------
    TripPattern
        Each joined trip in order, up to `STRIP_HOPS`, stopping at one
        not loaded or not running that day.
    """
    for _ in range(STRIP_HOPS):
        joined = patterns.get(pattern.next_trip or '')
        if joined is None or day not in joined.days:
            return
        yield joined
        pattern = joined


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


def from_call(
    *,
    departure: Departure,
    pattern: TripPattern,
    origin: str,
    stations: Mapping[str, str],
    zone: tzinfo,
) -> int | None:
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
    zone : tzinfo
        Local time zone.

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
        day=departure.service_day, moment=departure.departs, zone=zone,
    )
    timed = [i for i in at_from if pattern.stops[i].departs == leaves]
    return next(iter(timed or at_from), None)


def started_pattern(
    *, item: LiveDeparture, trips: TripInputs, origin: str,
) -> tuple[TripPattern, int, int] | None:
    """
    Find a departure's pattern, its call at the From and its delay.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.

    Returns
    -------
    tuple[TripPattern, int, int] | None
        The pattern, the index of its call at the From and the delay in
        seconds; None when the pattern isn't loaded or doesn't call there.
    """
    departure = item.departure
    pattern = trips.patterns.get(departure.trip_id)
    start = None if pattern is None else from_call(
        departure=departure, pattern=pattern, origin=origin,
        stations=trips.station_of, zone=trips.zone,
    )
    if pattern is None or start is None:
        return None
    delay = int(item.departs.timestamp() - departure.departs.timestamp())
    return pattern, start, delay


def stops_after(
    *,
    item: LiveDeparture,
    trips: TripInputs,
    origin: str,
) -> Iterator[tuple[str, PatternStop, datetime]]:
    """
    List the calls a departure makes after the From, with times.

    The vehicle is followed on through the trips it joins onto. A joined
    trip's first call is where the vehicle already is, so it is skipped.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.

    Yields
    ------
    tuple[str, PatternStop, datetime]
        Each later call's strip station, the call, and its arrival
        shifted by the delay; none when the trip's pattern isn't loaded.
        Calls run through with no pick-up or set-down are left out.
    """
    day = item.departure.service_day
    started = started_pattern(item=item, trips=trips, origin=origin)
    if started is None:
        return
    pattern, start, delay = started
    ends = delay_ends(
        item=item, leaves=pattern.stops[start], delay=delay, zone=trips.zone,
    )
    joined = joined_patterns(
        pattern=pattern, patterns=trips.patterns, day=day,
    )
    calls = chain(
        pattern.stops[start + 1:], *(more.stops[1:] for more in joined),
    )
    for stop in calls:
        if not stop.passes:
            arrives = service_time(
                day=day, seconds=stop.arrives, zone=trips.zone,
            )
            yield (
                trips.station_of.get(stop.station, stop.station),
                stop,
                shifted(
                    moment=arrives,
                    seconds=call_delay(seconds=stop.arrives, ends=ends),
                    zone=trips.zone,
                ),
            )


def delay_ends(
    *, item: LiveDeparture, leaves: PatternStop, delay: int, zone: tzinfo,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """
    Pair a departure's delay at the From with its delay on arrival.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    leaves : PatternStop
        Its call at the From.
    delay : int
        Seconds late leaving the From.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    tuple[tuple[int, int], tuple[int, int]]
        Service-day seconds and delay at the From, then at the stop the
        departure's arrival time is for.
    """
    departure = item.departure
    arrives = service_seconds(
        day=departure.service_day, moment=departure.arrives, zone=zone,
    )
    late = int(item.arrives.timestamp() - departure.arrives.timestamp())
    return (leaves.departs, delay), (arrives, late)


def call_delay(
    *, seconds: int, ends: tuple[tuple[int, int], tuple[int, int]],
) -> int:
    """
    Work out a call's delay between the From and the arrival stop.

    Parameters
    ----------
    seconds : int
        The call's scheduled service-day seconds.
    ends : tuple[tuple[int, int], tuple[int, int]]
        Seconds and delay at the From and at the arrival stop.

    Returns
    -------
    int
        The delay moved in step with the scheduled time from the From's
        to the arrival's; the arrival's at and after the arrival stop.
    """
    (first, early), (last, late) = ends
    if seconds >= last:
        return late
    if last <= first:
        return early
    return early + round((late - early) * (seconds - first) / (last - first))


def arrivals_after(
    *,
    item: LiveDeparture,
    trips: TripInputs,
    origin: str,
) -> Iterator[tuple[str, datetime]]:
    """
    List the stations a departure calls at after the From, with times.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.

    Yields
    ------
    tuple[str, datetime]
        Each later call's station and arrival, as `stops_after` gives
        them.
    """
    for station, _, moment in stops_after(
        item=item, trips=trips, origin=origin,
    ):
        yield station, moment


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
    return any(
        station == target
        for station, _ in arrivals_after(
            item=item, trips=trips, origin=origin,
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


def shown_item(
    *,
    trips: TripInputs,
    origin: str,
    target: str | None,
    selected: TripDay | None,
) -> LiveDeparture | None:
    """
    Find the departure whose times a strip shows.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    target : str | None
        The Destination station ID, or None.
    selected : TripDay | None
        The picked train, or None.

    Returns
    -------
    LiveDeparture | None
        The picked train, else the next that can be taken; None when
        there is neither.
    """
    picked = next(
        (
            item
            for item in trips.departures
            if trip_day(departure=item.departure) == selected
        ),
        None,
    )
    if picked is not None:
        return picked
    return next_item(trips=trips, origin=origin, target=target)


def calls_before(
    *, item: LiveDeparture, trips: TripInputs, origin: str,
) -> dict[str, datetime]:
    """
    Find when a departure calls at stations before the From.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's loaded patterns, station map and time.
    origin : str
        The From station ID.

    Returns
    -------
    dict[str, datetime]
        Departure time by station for the calls before the From that it
        stops at, shifted by its delay at the From, leaving out the ones
        it has already made; empty when its pattern is unknown.
    """
    started = started_pattern(item=item, trips=trips, origin=origin)
    if started is None:
        return {}
    pattern, start, delay = started
    found = {
        trips.station_of.get(stop.station, stop.station): shifted(
            moment=service_time(
                day=item.departure.service_day,
                seconds=(
                    stop.arrives if stop.departs is None else stop.departs
                ),
                zone=trips.zone,
            ),
            seconds=delay,
            zone=trips.zone,
        )
        for stop in pattern.stops[:start]
        if not stop.passes
    }
    return {
        station: moment
        for station, moment in found.items()
        if moment >= trips.now
    }


def trip_times(
    *, item: LiveDeparture, trips: TripInputs, origin: str, above: bool,
) -> dict[str, datetime]:
    """
    Find when one departure calls at each station.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's loaded patterns, station map and time.
    origin : str
        The From station ID.
    above : bool
        Include the calls before the From, for rows above it.

    Returns
    -------
    dict[str, datetime]
        Time by station: its first call after the From, else, with
        `above`, a call before the From it has still to make (see
        `calls_before`).
    """
    after: dict[str, datetime] = {}
    for station, moment in arrivals_after(
        item=item, trips=trips, origin=origin,
    ):
        after.setdefault(station, moment)
    before = (
        calls_before(item=item, trips=trips, origin=origin) if above else {}
    )
    return {**before, **after}


def trip_text(
    *,
    item: LiveDeparture,
    trips: TripInputs,
    reach: Reach,
    rows: Collection[int],
) -> dict[int, str]:
    """
    Give strip rows one departure's times.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, with its From row.
    rows : Collection[int]
        The rows that may show a time.

    Returns
    -------
    dict[int, str]
        Clock time by approach, main or branch row among `rows` that the
        departure calls at after the From or, above the From, still has
        to call at before it (see `trip_times`); rows it skips, has
        passed or never reaches have none.
    """
    origin = next(
        index
        for index, row in enumerate(reach.rows)
        if row.kind is RowKind.ORIGIN
    )
    found = [
        trip_times(
            item=item,
            trips=trips,
            origin=reach.rows[origin].place.stop_id,
            above=above,
        )
        for above in (False, True)
    ]
    return {
        index: clock_text(
            moment=found[index < origin][row.place.stop_id], zone=trips.zone,
        )
        for index, row in enumerate(reach.rows)
        if index in rows
        and row.kind in (RowKind.APPROACH, RowKind.MAIN, RowKind.BRANCH)
        and row.place.stop_id in found[index < origin]
    }
