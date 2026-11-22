"""The stations facing the stops a both-sides strip's buses approach by."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from paperroute_transit.core.model import Departure
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.reach import Reach, ReachInput, RowKind, StripKey
from paperroute_transit.core.sides import KerbPair
from paperroute_transit.core.strip_badges import Spot
from paperroute_transit.core.strip_follow import origin_of
from paperroute_transit.core.strip_slots import both_kerbs
from paperroute_transit.core.strip_times import TripInputs, from_call

UP: Final[float] = 0.0  # wedge bearing up the strip
DOWN: Final[float] = 180.0  # down it
REACHED: Final[float] = 0.95  # share of a hop that puts a bus at its next stop


@dataclass(frozen=True)
class Approach:
    """
    The rows a both-sides strip draws its approaching buses on.

    Attributes
    ----------
    origin : str
        The From station ID.
    facing : Mapping[str, str]
        The station across the road from each stop called at before the
        From.
    above : tuple[str, ...]
        Station IDs of the main rows above the From, nearest it first.
    below : tuple[str, ...]
        Station IDs of the main rows below the From, nearest it first.
    upper : frozenset[str]
        Station IDs of every row above the From, of any kind.
    lower : frozenset[str]
        Station IDs of every row below the From, of any kind.
    """

    origin: str
    facing: Mapping[str, str]
    above: tuple[str, ...]
    below: tuple[str, ...]
    upper: frozenset[str]
    lower: frozenset[str]


def approach_stations(*, data: ReachInput) -> frozenset[str]:
    """
    List the stations called at before the From on either kerb.

    Parameters
    ----------
    data : ReachInput
        The strip's runs.

    Returns
    -------
    frozenset[str]
        Every station in the before of the tails and the across tails.
    """
    return frozenset(
        station
        for run in (*data.tails, *data.across_tails)
        for station in run.before
    )


def facing_of(*, pairs: Mapping[str, KerbPair]) -> dict[str, str]:
    """
    Name the station across the road from each paired station.

    Parameters
    ----------
    pairs : Mapping[str, KerbPair]
        Kerb pairs by station.

    Returns
    -------
    dict[str, str]
        The partner's station ID by station.
    """
    return {
        station: pair.other.stop_id for station, pair in pairs.items()
    }


def approach_of(
    *, reach: Reach | None, key: StripKey | None,
) -> Approach | None:
    """
    Find the rows a strip draws its approaching buses on.

    Parameters
    ----------
    reach : Reach | None
        The strip's reach, or None.
    key : StripKey | None
        The strip's key, or None.

    Returns
    -------
    Approach | None
        The From and the rows either side of it; None without a reach or
        a From row, or for a strip not fed by both kerbs.
    """
    if reach is None or not both_kerbs(key=key):
        return None
    origin = origin_of(reach=reach)
    if origin is None:
        return None
    main = [
        index
        for index, row in enumerate(reach.rows)
        if row.kind is RowKind.MAIN
    ]
    return Approach(
        origin=reach.rows[origin].place.stop_id,
        facing=reach.facing,
        above=tuple(
            reach.rows[index].place.stop_id
            for index in reversed(main)
            if index < origin
        ),
        below=tuple(
            reach.rows[index].place.stop_id
            for index in main
            if index > origin
        ),
        upper=frozenset(row.place.stop_id for row in reach.rows[:origin]),
        lower=frozenset(
            row.place.stop_id for row in reach.rows[origin + 1:]
        ),
    )


def stop_reached(*, hop: tuple[int, float]) -> int:
    """
    Find the stop a vehicle is at or last left.

    Parameters
    ----------
    hop : tuple[int, float]
        The index of the hop's first stop and the share of it run.

    Returns
    -------
    int
        The hop's next stop once `REACHED` of the hop is run, else its
        first stop.
    """
    index, fraction = hop
    return index + 1 if fraction >= REACHED else index


def facing_station(
    *,
    lane: Sequence[str],
    side: Collection[str],
    facing: Mapping[str, str],
    station: str,
    count: int,
) -> str | None:
    """
    Find the row facing a stop.

    Parameters
    ----------
    lane : Sequence[str]
        Station IDs of the main rows on the bus's side, nearest the From
        first.
    side : Collection[str]
        Station IDs of every row on the bus's side.
    facing : Mapping[str, str]
        The station across the road from each stop.
    station : str
        The stop's station ID.
    count : int
        Stops between it and the From on the bus's trip.

    Returns
    -------
    str | None
        The stop itself when it is a row on the bus's side; else its
        partner when that is in the lane; else the lane's row as many
        stops from the From; else None.
    """
    if station in side:
        return station
    partner = facing.get(station)
    if partner in lane:
        return partner
    return lane[count - 1] if 0 < count <= len(lane) else None


def facing_row(
    *,
    pattern: TripPattern,
    index: int,
    origin: int,
    lane: Sequence[str],
    side: Collection[str],
    trips: TripInputs,
    facing: Mapping[str, str],
) -> str | None:
    """
    Find the facing station of the call at an index (see `facing_station`).

    Parameters
    ----------
    pattern : TripPattern
        The bus's trip.
    index : int
        The call's index.
    origin : int
        The index of the call at the From.
    lane : Sequence[str]
        Station IDs of the main rows on the bus's side, nearest the From
        first.
    side : Collection[str]
        Station IDs of every row on the bus's side.
    trips : TripInputs
        The strip's departures and live data.
    facing : Mapping[str, str]
        The station across the road from each stop.

    Returns
    -------
    str | None
        The station of the row facing that call, or None.
    """
    stop = pattern.stops[index].station
    return facing_station(
        lane=lane,
        side=side,
        facing=facing,
        station=trips.station_of.get(stop, stop),
        count=origin - index,
    )


def facing_spot(
    *,
    departure: Departure,
    pattern: TripPattern,
    at: int,
    trips: TripInputs,
    approach: Approach,
    spots: Mapping[str, Spot],
) -> Spot | None:
    """
    Find where a bus approaching a both-sides From is drawn.

    Parameters
    ----------
    departure : Departure
        The bus's departure from the From.
    pattern : TripPattern
        Its trip.
    at : int
        The index of the call it is at or last left.
    trips : TripInputs
        The strip's departures and live data.
    approach : Approach
        The rows either side of the From.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.

    Returns
    -------
    Spot | None
        The spot of the row facing that call (see `facing_station`),
        above the From for the picked kerb's buses and below it for the
        kerb across's; else midway between the rows facing the nearest
        calls before and after it that have one; None at or past the
        From, or when a row needed is not visible.
    """
    origin = from_call(
        departure=departure, pattern=pattern, origin=approach.origin,
        stations=trips.station_of, zone=trips.zone,
    )
    if origin is None or at >= origin:
        return None
    drawn = spots.keys()
    rows = [
        facing_row(
            pattern=pattern, index=index, origin=origin, trips=trips,
            lane=approach.below if departure.facts.across else approach.above,
            side=(
                approach.lower if departure.facts.across else approach.upper
            ) & drawn,
            facing=approach.facing,
        )
        for index in range(origin)
    ]
    own = rows[at]
    if own is not None:
        return spots.get(own)
    later = next((row for row in rows[at + 1:] if row), None)
    earlier = next((row for row in reversed(rows[:at]) if row), None)
    one = None if later is None else spots.get(later)
    two = None if earlier is None else spots.get(earlier)
    if one is None or two is None:
        return None
    return Spot(x=one.x, y=(one.y + two.y) / 2)


def toward_from(*, departure: Departure) -> float:
    """
    Point a wedge from an approaching bus towards the From.

    Parameters
    ----------
    departure : Departure
        The bus's departure from the From.

    Returns
    -------
    float
        Up for a departure from the kerb across, else down.
    """
    return UP if departure.facts.across else DOWN
