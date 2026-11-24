"""Find where a vehicle turns back to run its next trip."""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from paperroute_transit.build.runs import TripCalls

TURN_SCHEMA: Final[str] = '''
CREATE TABLE trip_turns (trip_id TEXT PRIMARY KEY, next_trip TEXT NOT NULL)
'''


def runs_on(
    *, current: TripCalls, after: TripCalls | None, joined: set[str],
) -> bool:
    """
    Decide whether a run carries on from one trip into the next.

    Rail trips run on into any route; others only into the same route
    short name. A trip already in the run is never joined again.

    Parameters
    ----------
    current : TripCalls
        Last trip in the run so far.
    after : TripCalls | None
        Its next trip on the block, if any.
    joined : set[str]
        Trip IDs already in the run.

    Returns
    -------
    bool
        True when after joins the run.
    """
    if after is None or after.trip_id in joined:
        return False
    return current.rail or after.route == current.route


def reverses(*, trip: TripCalls, after: TripCalls) -> bool:
    """
    Say whether a vehicle turns back where one trip becomes the next.

    Parameters
    ----------
    trip : TripCalls
        Earlier trip.
    after : TripCalls
        Trip it becomes.

    Returns
    -------
    bool
        True when after's second station is trip's second-to-last.
    """
    return (
        len(trip.stations) > 1 and len(after.stations) > 1
        and after.stations[1] == trip.stations[-2]
    )


def block_pairs(
    *, trips: Iterable[TripCalls], nexts: dict[str, TripCalls],
    turned: bool,
) -> Iterator[tuple[str, str]]:
    """
    Pair each shown trip with the next trip its vehicle runs on as.

    Parameters
    ----------
    trips : Iterable[TripCalls]
        Trips, hidden ones included.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.
    turned : bool
        True for the pairs where the vehicle turns back, False for the
        pairs where it runs on through.

    Yields
    ------
    tuple[str, str]
        Trip ID and the next trip's ID.
    """
    for trip in trips:
        after = nexts.get(trip.trip_id)
        if trip.shown and after is not None and runs_on(
            current=trip, after=after, joined={trip.trip_id},
        ) and reverses(trip=trip, after=after) == turned:
            yield trip.trip_id, after.trip_id


def trip_turns(
    *, trips: Iterable[TripCalls], nexts: dict[str, TripCalls],
) -> Iterator[tuple[str, str]]:
    """
    Find the shown trips whose vehicle turns back as its next trip.

    A turnaround is a pair that would join, but whose next trip's second
    station is this trip's second-to-last.

    Parameters
    ----------
    trips : Iterable[TripCalls]
        Trips, hidden ones included.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.

    Yields
    ------
    tuple[str, str]
        Trip ID and the next trip's ID.
    """
    yield from block_pairs(trips=trips, nexts=nexts, turned=True)
