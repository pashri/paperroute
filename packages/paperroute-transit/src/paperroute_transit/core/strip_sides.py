"""Times for the rows above a both-sides From."""

from paperroute_device.core.text import clock_text
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.reach import Reach, RowKind
from paperroute_transit.core.strip_follow import boardable_items, origin_of
from paperroute_transit.core.strip_model import TripDay
from paperroute_transit.core.strip_times import (
    TripInputs,
    shown_item,
    trip_times,
)


def across_times(*, trips: TripInputs, reach: Reach) -> dict[int, str]:
    """
    Time the rows above the From by the next bus from the kerb across.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach.

    Returns
    -------
    dict[int, str]
        Clock time by main or branch row above the From that the next
        boardable bus from the kerb across calls at after the From.
        Empty for a strip not fed by both kerbs, with no such bus, or
        with its pattern not loaded.
    """
    origin = origin_of(reach=reach)
    if origin is None:
        return {}
    station = reach.rows[origin].place.stop_id
    item = next(
        (
            each
            for each in boardable_items(
                trips=trips, origin=station, target=None,
            )
            if each.departure.facts.across
        ),
        None,
    )
    if item is None:
        return {}
    found = trip_times(
        item=item, trips=trips, origin=station, above=False,
    )
    return {
        index: clock_text(moment=found[row.place.stop_id], zone=trips.zone)
        for index, row in enumerate(reach.rows[:origin])
        if row.kind in (RowKind.MAIN, RowKind.BRANCH)
        and row.place.stop_id in found
    }


def kerb_item(
    *,
    trips: TripInputs,
    origin: str,
    target: str | None,
    selected: TripDay | None,
) -> LiveDeparture | None:
    """
    Find the departure from the picked kerb whose times a strip shows.

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
        The picked train, else the next boardable one that does not
        leave from the kerb across; None when there is neither.
    """
    item = shown_item(
        trips=trips, origin=origin, target=target, selected=selected,
    )
    if item is None or selected is not None:
        return item
    if not item.departure.facts.across:
        return item
    return next(
        (
            each
            for each in boardable_items(
                trips=trips, origin=origin, target=target,
            )
            if not each.departure.facts.across
        ),
        None,
    )
