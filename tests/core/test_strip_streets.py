"""Tests for heading a run of stops on one street once."""

from dataclasses import replace
from typing import Final

import pytest

from paperroute_testkit.core.factories import inputs_of, place
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.strip_model import PANE_ROWS, ROW_H, STRIP_ROWS
from paperroute_transit.core.strip_streets import (
    HEADING_ROOM,
    moved_y,
    room_ys,
    street_runs,
)
from paperroute_transit.core.strip_view import (
    build_strip_pane,
    build_strip_view,
    line_reach,
)

RULES: Final[NameRules] = NameRules()
BUSWAY: Final[tuple[str, ...]] = (
    'Histon Road',
    'The Busway Orchard Park',
    'The Busway Kings Hedges',
    'The Busway Milton Road',
    'Cambridge North',
)


def test_street_runs_groups_three_in_a_row() -> None:
    """Three neighbouring stops on The Busway make one run."""
    assert street_runs(names=BUSWAY, rules=RULES, least=3) == (
        (1, 3, 'The Busway'),
    )


def test_street_runs_skips_a_run_shorter_than_least() -> None:
    """A run shorter than the smallest group is left alone."""
    assert not street_runs(names=BUSWAY, rules=RULES, least=4)
    assert not street_runs(names=BUSWAY[:3], rules=RULES, least=3)


def test_street_runs_starts_after_a_top_slot_run() -> None:
    """A run in the top slot keeps that row whole; its heading starts next."""
    assert street_runs(names=BUSWAY[1:], rules=RULES, least=3) == (
        (1, 2, 'The Busway'),
    )


def test_street_runs_skips_empty_names() -> None:
    """Rows with no name break a run."""
    names = ('The Busway A', '', 'The Busway B', 'The Busway C')
    assert street_runs(names=names, rules=RULES, least=2) == (
        (2, 3, 'The Busway'),
    )


@pytest.mark.parametrize(('rows', 'take'), [
    (11, 2.0),
    (PANE_ROWS, HEADING_ROOM / (PANE_ROWS - 1)),
])
def test_room_ys_takes_room_evenly_from_every_gap(
    rows: int, take: float,
) -> None:
    """A run's room is taken evenly from every other gap; none drops."""
    ys = tuple(100.0 + index * ROW_H for index in range(rows))
    moved = room_ys(ys=ys, starts=(3,))
    gaps = [after - before for before, after in zip(moved, moved[1:])]
    assert moved[0] == pytest.approx(ys[0] - take)
    assert moved[-1] == pytest.approx(ys[-1])
    assert gaps[2] == pytest.approx(ROW_H + HEADING_ROOM)
    for gap in gaps[:2] + gaps[3:]:
        assert gap == pytest.approx(ROW_H - take)


def test_room_ys_without_runs_keeps_rows() -> None:
    """With no run the rows stay where they are."""
    assert room_ys(ys=(1.0, 2.0), starts=()) == (1.0, 2.0)


def test_moved_y_follows_the_rows_around_it() -> None:
    """A y between rows moves in proportion; past an end, with that end."""
    pairs = ((10.0, 8.0), (20.0, 28.0))
    assert moved_y(y=15.0, pairs=pairs) == 18.0
    assert moved_y(y=0.0, pairs=pairs) == -2.0
    assert moved_y(y=30.0, pairs=pairs) == 38.0


def _line(*, total: int, street: range) -> list[str]:
    """Name a line's stops, those in `street` on Norton Street."""
    return [
        f'X{index}, Norton Street' if index in street else f'S{index}'
        for index in range(total)
    ]


def test_slot_rows_takes_room_evenly_from_every_gap() -> None:
    """The Strip and the pane head the run and keep every row."""
    names = _line(total=STRIP_ROWS, street=range(3, 6))
    reach = line_reach(line=[
        place(f'P{index}', name=name) for index, name in enumerate(names)
    ])
    inputs = inputs_of(reach=reach)
    strip = build_strip_view(inputs=inputs)
    assert len(strip.rows) == STRIP_ROWS
    assert [row.heading for row in strip.rows][2:5] == ['', 'Norton St', '']
    assert [row.short for row in strip.rows][2:7] == [
        '', 'X3', 'X4', 'X5', '',
    ]
    take = HEADING_ROOM / (STRIP_ROWS - 1)
    assert strip.rows[3].y - strip.rows[2].y == pytest.approx(
        ROW_H + HEADING_ROOM,
    )
    assert strip.rows[2].y - strip.rows[1].y == pytest.approx(ROW_H - take)
    assert strip.rows[4].parent_y == strip.rows[3].y
    pane = build_strip_pane(inputs=inputs)
    assert len(pane.rows) == PANE_ROWS
    assert pane.rows[3].heading == 'Norton St'
    plain = build_strip_view(inputs=replace(inputs, street_group=4))
    assert not any(row.short for row in plain.rows)
