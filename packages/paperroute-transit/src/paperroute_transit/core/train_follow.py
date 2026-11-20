"""The trains a train Strip follows, chosen from the departures listed."""

from dataclasses import dataclass

from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.reach import Reach, ReachInput
from paperroute_transit.core.stop_view import in_view
from paperroute_transit.core.strip_model import TripDay, trip_day
from paperroute_transit.core.strip_times import TripInputs, live_strip
from paperroute_transit.core.train_line import main_departure, train_reach
from paperroute_transit.core.train_network import TrainNetwork
from paperroute_transit.core.train_sides import north_hops, side_trains


@dataclass(frozen=True)
class Following:
    """
    What a train Strip is laid out from.

    Attributes
    ----------
    trips : TripInputs
        The strip's departures, loaded patterns and station map.
    destination : str | None
        The Destination station ID, or None.
    selected : TripDay | None
        The train picked in the panel, or None for the next.
    loop_name : str
        The city's name for a train that returns to the From.
    """

    trips: TripInputs
    destination: str | None
    selected: TripDay | None
    loop_name: str


def listed_items(*, trips: TripInputs) -> list[LiveDeparture]:
    """
    List the departures the panel beside the Strip lists.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures.

    Returns
    -------
    list[LiveDeparture]
        The strip's departures not yet gone, in the panel's order.
    """
    return in_view(departures=live_strip(trips=trips), now=trips.now)


def picked_index(
    *, items: list[LiveDeparture], selected: TripDay | None,
) -> int | None:
    """
    Find the picked train among the listed departures.

    Parameters
    ----------
    items : list[LiveDeparture]
        The listed departures.
    selected : TripDay | None
        The picked train, or None.

    Returns
    -------
    int | None
        Its index, or None when nothing is picked or it is not listed.
    """
    return next(
        (
            index
            for index, item in enumerate(items)
            if selected is not None
            and trip_day(departure=item.departure) == selected
        ),
        None,
    )


def train_strip(*, data: ReachInput, follow: Following) -> Reach:
    """
    Lay out a train Strip along the trains it follows.

    Parameters
    ----------
    data : ReachInput
        The From and the kind's network.
    follow : Following
        The departures, Destination and pick.

    Returns
    -------
    Reach
        With a Destination, the next listed train to reach it, or the
        picked one. With none, the next train each way, sorted by the
        side its first station is on. See `train_reach`.
    """
    origin = data.origin.stop_id
    trips = follow.trips
    items = listed_items(trips=trips)
    picked = picked_index(items=items, selected=follow.selected)
    if follow.destination is not None:
        return train_reach(
            data=data,
            trips=trips,
            main=main_departure(
                items=items, trips=trips, origin=origin, picked=picked,
            ),
            destination=follow.destination,
            loop_name=follow.loop_name,
        )
    network = data.network or TrainNetwork(hops={}, runs=())
    above, below = side_trains(
        items=items,
        trips=trips,
        picked=picked,
        sides=north_hops(
            network=network,
            origin=origin,
            places={origin: data.origin, **data.places},
        ),
        origin=origin,
        network=network,
    )
    return train_reach(
        data=data,
        trips=trips,
        main=below,
        destination=None,
        above=above,
        loop_name=follow.loop_name,
    )
