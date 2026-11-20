"""Tests for the Strip's departures list: loop passes and FASTER."""

from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import timedelta

from paperroute_testkit.core.factories import (
    START,
    departure,
    example_reach,
    inputs_of,
    live,
    stop_view_of,
)
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.stop_view import Pill
from paperroute_transit.core.strip_list import list_pills, loop_passes
from paperroute_transit.core.strip_model import StripInputs, trip_day
from paperroute_transit.core.strip_view import strip_panel


def live_departures(
    *, arrives: Sequence[int], cancelled: Collection[int] = (),
) -> tuple[LiveDeparture, ...]:
    """
    Make departures arriving some minutes after 8:00, in list order.

    Parameters
    ----------
    arrives : Sequence[int]
        Minutes after 8:00 each one arrives.
    cancelled : Collection[int]
        Indexes of the cancelled ones.

    Returns
    -------
    tuple[LiveDeparture, ...]
        One departure per arrival, leaving a minute apart.
    """
    return tuple(
        live(
            item=departure(
                departs=START + timedelta(minutes=index),
                ride=minutes - index,
                trip_id=f't{index}',
            ),
            cancelled=index in cancelled,
        )
        for index, minutes in enumerate(arrives)
    )


def panel_inputs(
    *,
    arrives: Sequence[int],
    cancelled: Collection[int] = (),
    end_points: bool = True,
    destination: str | None = 'Wx',
) -> StripInputs:
    """
    Make Strip inputs listing departures that arrive at some minutes.

    Parameters
    ----------
    arrives : Sequence[int]
        Minutes after 8:00 each departure arrives.
    cancelled : Collection[int]
        Indexes of the cancelled ones.
    end_points : bool
        Whether the pin's kind is chosen by end points.
    destination : str | None
        Destination name.

    Returns
    -------
    StripInputs
        Inputs whose Stop view lists the departures.
    """
    items = live_departures(arrives=arrives, cancelled=cancelled)
    now = START - timedelta(minutes=2)
    return replace(
        inputs_of(
            reach=example_reach(),
            departures=items,
            destination=destination,
        ),
        stop_view=stop_view_of(
            departures=items, now=now, destination=destination,
        ),
        end_points=end_points,
    )


def test_faster_beats_every_row_above() -> None:
    """FASTER needs an arrival before each row shown above."""
    items = live_departures(arrives=(30, 20, 25, 10))
    assert list_pills(items=items, faster=True) == (
        None, Pill.FASTER, None, Pill.FASTER,
    )


def test_faster_needs_more_than_the_row_ahead() -> None:
    """Beating only the row just ahead earns nothing."""
    items = live_departures(arrives=(10, 30, 20))
    assert list_pills(items=items, faster=True) == (None, None, None)


def test_a_cancelled_row_neither_earns_nor_counts() -> None:
    """Cancelled rows are skipped both ways."""
    items = live_departures(arrives=(30, 5, 20), cancelled=(1,))
    assert list_pills(items=items, faster=True) == (
        None, Pill.CANCELLED, Pill.FASTER,
    )


def test_a_cancelled_first_row_leaves_the_second_bare() -> None:
    """With nothing live above it, the second row earns nothing."""
    items = live_departures(arrives=(30, 20), cancelled=(0,))
    assert list_pills(items=items, faster=True) == (Pill.CANCELLED, None)


def test_no_faster_without_the_flag() -> None:
    """Without `faster` only CANCELLED is marked."""
    items = live_departures(arrives=(30, 20, 10), cancelled=(2,))
    assert list_pills(items=items, faster=False) == (
        None, None, Pill.CANCELLED,
    )


def test_no_faster_without_a_destination() -> None:
    """A list with no Destination marks no FASTER."""
    inputs = panel_inputs(arrives=(30, 20, 10), destination=None)
    assert {row.pill for row in strip_panel(inputs=inputs).rows} == {None}


def test_no_faster_off_end_points() -> None:
    """A mode chosen by line keeps its list without FASTER."""
    inputs = panel_inputs(arrives=(30, 20, 10), end_points=False)
    assert Pill.FASTER in {row.pill for row in inputs.stop_view.head_rows}
    assert {row.pill for row in strip_panel(inputs=inputs).rows} == {None}


def test_the_list_has_five_rows() -> None:
    """Six departures list five, FASTER marked by the new rule."""
    inputs = panel_inputs(arrives=(40, 35, 38, 36, 30, 20))
    panel = strip_panel(inputs=inputs)
    assert [row.pill for row in panel.rows] == [
        None, Pill.FASTER, None, None, Pill.FASTER,
    ]


def test_picking_follows_the_listed_rows() -> None:
    """The pills and the picked row follow the order the rows are shown."""
    inputs = panel_inputs(arrives=(10, 30, 20, 25, 22), cancelled=(0,))
    picked = inputs.stop_view.head_departures[2]
    inputs = replace(inputs, selected=trip_day(departure=picked))
    panel = strip_panel(inputs=inputs)
    assert picked.trip_id == 't2'
    assert [row.pill for row in panel.rows] == [
        None, Pill.CANCELLED, Pill.FASTER, None, None,
    ]
    assert panel.selected == 2


def trip_passes(
    *, seqs: Sequence[int], destination_seq: int,
) -> tuple[LiveDeparture, ...]:
    """
    Make one trip's passes through the From, each running to a stop.

    Parameters
    ----------
    seqs : Sequence[int]
        Sequence of each pass's call at the From.
    destination_seq : int
        Sequence of the call each pass runs to.

    Returns
    -------
    tuple[LiveDeparture, ...]
        One departure per pass, all of trip `x`.
    """
    return tuple(
        live(item=replace(
            departure(
                departs=START + timedelta(minutes=seq), trip_id='x',
            ),
            origin_seq=seq,
            destination_seq=destination_seq,
        ))
        for seq in seqs
    )


def test_loop_passes_drops_a_pass_with_a_later_call_before_the_to() -> None:
    """Only the pass from the last call at the From is kept."""
    passes = [item.departure for item in trip_passes(
        seqs=(2, 9), destination_seq=14,
    )]
    kept = loop_passes(departures=passes, from_seqs={'x': (2, 9)})
    assert [d.origin_seq for d in kept] == [9]


def test_loop_passes_keeps_a_trip_with_no_other_call() -> None:
    """A trip calling once at the From, or unknown, is kept."""
    passes = [item.departure for item in trip_passes(
        seqs=(2,), destination_seq=14,
    )]
    assert loop_passes(departures=passes, from_seqs={'x': (2,)}) == tuple(
        passes,
    )
    assert loop_passes(departures=passes, from_seqs={}) == tuple(passes)


def test_loop_passes_keeps_a_call_after_the_to() -> None:
    """A call at the From past the Destination does not drop a pass."""
    passes = [item.departure for item in trip_passes(
        seqs=(2,), destination_seq=8,
    )]
    kept = loop_passes(departures=passes, from_seqs={'x': (2, 9)})
    assert [d.origin_seq for d in kept] == [2]
