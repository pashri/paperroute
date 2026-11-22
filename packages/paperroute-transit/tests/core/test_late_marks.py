from dataclasses import replace

from paperroute_testkit.render.factories import stop_row
from paperroute_transit.core.late_marks import (
    MARK_FLOOR,
    MINUS,
    late_mark,
    row_mark,
)
from paperroute_transit.core.stop_view import Estimate, Pill


def estimate(*, late: int, uncertain: bool = False) -> Estimate:
    """Make an estimate due at 8:05."""
    return Estimate(due='8:05', late=late, uncertain=uncertain)


def test_late_mark_signs_late_and_early() -> None:
    """Late reads plus, early a real minus."""
    assert late_mark(estimate=estimate(late=4)) == '+4'
    assert late_mark(estimate=estimate(late=-2)) == f'{MINUS}2'
    assert MINUS == '−' and MINUS != '-'


def test_late_mark_is_silent_under_the_floor() -> None:
    """Under two minutes either way, or with no estimate, no mark."""
    assert MARK_FLOOR == 2
    assert [
        late_mark(estimate=estimate(late=late)) for late in (-1, 0, 1)
    ] == ['', '', '']
    assert late_mark(estimate=None) == ''


def test_late_mark_folds_uncertainty_in() -> None:
    """An uncertain mark ends in a question mark."""
    assert late_mark(estimate=estimate(late=12, uncertain=True)) == '+12?'
    assert late_mark(estimate=estimate(late=-3, uncertain=True)) == '−3?'
    assert late_mark(estimate=estimate(late=1, uncertain=True)) == ''


def test_row_mark_needs_a_live_countdown() -> None:
    """Timetable and cancelled rows carry no mark."""
    marked = replace(stop_row(), estimate=estimate(late=4))
    assert row_mark(row=marked) == '+4'
    assert row_mark(row=replace(marked, live=False)) == ''
    assert row_mark(row=replace(marked, pill=Pill.CANCELLED)) == ''
    assert row_mark(row=replace(marked, pill=Pill.FASTER)) == '+4'
