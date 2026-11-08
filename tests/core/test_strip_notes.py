"""Tests for the note on the station where a strip run carries on."""

from dataclasses import replace
from datetime import timedelta

from paperroute.city import SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import (
    EIGHT,
    LINE,
    START,
    T2_ON,
    calls,
    carry_on_inputs,
    departure,
    live,
    pattern,
)
from paperroute_transit.core.reach import Reach, RowKind
from paperroute_transit.core.strip_model import CarryNote, StripInputs
from paperroute_transit.core.strip_notes import carry_note
from paperroute_transit.core.strip_view import strip_rows, trips_of

T8_NOTE = CarryNote(route='T8', then='T2 Leppington')


def note_of(*, inputs: StripInputs) -> tuple[int, CarryNote] | None:
    """
    Find the strip's carry-on note.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.

    Returns
    -------
    tuple[int, CarryNote] | None
        The note's row and note.
    """
    assert inputs.reach is not None
    return carry_note(trips=trips_of(inputs=inputs), reach=inputs.reach)


def row_of(*, reach: Reach, station: str) -> int:
    """
    Find a station's row.

    Parameters
    ----------
    reach : Reach
        The reach.
    station : str
        Station ID.

    Returns
    -------
    int
        Its row index.
    """
    return next(
        index
        for index, row in enumerate(reach.rows)
        if row.place.stop_id == station
    )


def with_run(
    *, inputs: StripInputs, stations: str, minutes: int = 20,
) -> StripInputs:
    """
    Add a run x from A that carries on as the T2, to the inputs.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.
    stations : str
        Stations it calls at, one letter each.
    minutes : int
        Minutes after 8:00 it leaves A.

    Returns
    -------
    StripInputs
        The inputs with run x, its pattern loaded.
    """
    item = departure(
        departs=START + timedelta(minutes=minutes), trip_id='x',
        route='T8', mode=SydneyFeed.TRAIN, kind=SydneyMode.TRAIN, then=T2_ON,
    )
    run = pattern(
        trip_id='x', route='T8',
        stops=calls(
            stations=stations, start=EIGHT + minutes * 60, points=LINE,
        ),
    )
    return replace(
        inputs,
        departures=(*inputs.departures, live(item=item)),
        patterns={**inputs.patterns, 'x': run},
    )


def test_carry_note_on_the_change_station() -> None:
    """Run a's note sits on C, and C's strip row carries it."""
    inputs = carry_on_inputs(now=START - timedelta(minutes=2))
    assert inputs.reach is not None
    change = row_of(reach=inputs.reach, station='C')
    assert note_of(inputs=inputs) == (change, T8_NOTE)
    rows, _ = strip_rows(inputs=inputs, reach=inputs.reach)
    assert {row.name: row.note for row in rows} == {
        'A': None, 'B': None, 'C': T8_NOTE, 'D': None,
    }


def test_only_the_next_such_run_gets_a_note() -> None:
    """A later carry-on run ending at B adds no note at B."""
    inputs = with_run(
        inputs=carry_on_inputs(now=START - timedelta(minutes=2)),
        stations='AB',
    )
    assert inputs.reach is not None
    assert note_of(inputs=inputs) == (
        row_of(reach=inputs.reach, station='C'), T8_NOTE,
    )


def test_gone_runs_give_no_note() -> None:
    """Once a has left A, the next carry-on run's station has the note."""
    gone = carry_on_inputs(now=START + timedelta(minutes=2))
    assert note_of(inputs=gone) is None
    inputs = with_run(inputs=gone, stations='AB')
    assert inputs.reach is not None
    assert note_of(inputs=inputs) == (
        row_of(reach=inputs.reach, station='B'), T8_NOTE,
    )


def test_cancelled_runs_give_no_note() -> None:
    """A cancelled carry-on run has no note."""
    inputs = carry_on_inputs(now=START - timedelta(minutes=2))
    (item,) = inputs.departures
    cancelled = replace(inputs, departures=(replace(item, cancelled=True),))
    assert note_of(inputs=cancelled) is None


def test_unloaded_runs_give_no_note() -> None:
    """A carry-on run whose pattern isn't loaded has no note."""
    inputs = carry_on_inputs(now=START - timedelta(minutes=2))
    assert note_of(inputs=replace(inputs, patterns={})) is None


def test_no_note_on_the_from() -> None:
    """A run that loops back to end at the From has no note."""
    inputs = with_run(
        inputs=carry_on_inputs(now=START + timedelta(minutes=2)),
        stations='ABA',
    )
    assert note_of(inputs=inputs) is None


def test_no_note_off_the_strip() -> None:
    """Off the line, on an exit row or in a closed fold: no note."""
    gone = carry_on_inputs(now=START + timedelta(minutes=2))
    away = with_run(inputs=gone, stations='ABZ')
    exiting = with_run(
        inputs=carry_on_inputs(now=START + timedelta(minutes=2), off=True),
        stations='ABCD',
    )
    assert exiting.reach is not None
    off = row_of(reach=exiting.reach, station='D')
    assert exiting.reach.rows[off].kind is RowKind.EXIT
    folded = with_run(inputs=gone, stations='ABCD')
    assert folded.reach is not None
    rows = list(folded.reach.rows)
    last = row_of(reach=folded.reach, station='D')
    rows[last] = replace(rows[last], kind=RowKind.FOLD)
    folded = replace(folded, reach=replace(folded.reach, rows=tuple(rows)))
    for inputs in (away, exiting, folded):
        assert note_of(inputs=inputs) is None
