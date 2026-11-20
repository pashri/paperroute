"""Which departures the Strip lists and which earn FASTER."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import Departure
from paperroute_transit.core.stop_view import Pill, Row
from paperroute_transit.core.strip_model import PICK_FROM, StripInputs


def loop_passes(
    *,
    departures: Sequence[Departure],
    from_seqs: Mapping[str, tuple[int, ...]],
) -> tuple[Departure, ...]:
    """
    Drop each pass that calls at the From again before its Destination.

    Parameters
    ----------
    departures : Sequence[Departure]
        Departures from the From to the Destination.
    from_seqs : Mapping[str, tuple[int, ...]]
        Each trip's call sequences at the From, by trip ID.

    Returns
    -------
    tuple[Departure, ...]
        The departures with no call at the From strictly between their
        origin and destination sequences.
    """
    return tuple(
        departure
        for departure in departures
        if not any(
            departure.origin_seq < seq < departure.destination_seq
            for seq in from_seqs.get(departure.trip_id, ())
        )
    )


def list_pills(
    *, items: Sequence[LiveDeparture], faster: bool,
) -> tuple[Pill | None, ...]:
    """
    Mark each listed departure CANCELLED, FASTER or nothing.

    Parameters
    ----------
    items : Sequence[LiveDeparture]
        Departures in the order they are listed.
    faster : bool
        Whether FASTER applies.

    Returns
    -------
    tuple[Pill | None, ...]
        One marker or None per departure. FASTER marks a departure that
        arrives before every departure above it that is not cancelled;
        the first such departure never earns it.
    """
    result: list[Pill | None] = []
    earliest: float | None = None
    for item in items:
        if item.cancelled:
            result.append(Pill.CANCELLED)
            continue
        arrives = item.arrives.timestamp()
        beats = faster and earliest is not None and arrives < earliest
        result.append(Pill.FASTER if beats else None)
        earliest = arrives if earliest is None else min(earliest, arrives)
    return tuple(result)


def listed_rows(*, inputs: StripInputs, shown: int) -> tuple[Row, ...]:
    """
    Give each listed row its pill by the list's own rule.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    shown : int
        How many of the Stop view's head rows are listed.

    Returns
    -------
    tuple[Row, ...]
        No rows while picking a From, else the listed rows with their pills
        replaced. FASTER applies to an
        end-point kind with a Destination. A row whose departure is not
        among the inputs' is cancelled when it was, else bare.
    """
    if inputs.picking == PICK_FROM:
        return ()
    stop = inputs.stop_view
    rows = stop.head_rows[:shown]
    found = {
        (item.departure.trip_id, item.departure.service_day,
         item.departure.origin_seq): item
        for item in inputs.departures
    }
    items = [
        found.get((t.trip_id, t.service_day, t.origin_seq))
        for t in stop.head_departures[:shown]
    ]
    if None in items:
        return tuple(
            replace(
                row,
                pill=Pill.CANCELLED if row.pill is Pill.CANCELLED else None,
            )
            for row in rows
        )
    pills = list_pills(
        items=[item for item in items if item is not None],
        faster=inputs.end_points and inputs.destination is not None,
    )
    return tuple(
        replace(row, pill=pill) for row, pill in zip(rows, pills, strict=True)
    )
