"""Tests for where the Map's solid lines part."""

import math
from dataclasses import replace

from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.map_line import MapLine
from paperroute_transit.core.partings import APART, PARTING_STEP, partings


def fork(*, split: float) -> tuple[MapLine, MapLine]:
    """Two solid lines east from (0, 0), one turning north at `split`."""
    east = make_shape(
        shape_id='bare-east', points=((0.0, 0.0), (20_000.0, 0.0)),
    )
    turn = make_shape(
        shape_id='bare-turn',
        points=((0.0, 0.0), (split, 0.0), (split, 20_000.0)),
    )
    return (
        MapLine(shape_id='bare-east', solid=(0.0, 20_000.0), shape=east),
        MapLine(shape_id='bare-turn', solid=(0.0, turn.along[-1]), shape=turn),
    )


def test_partings_find_where_solid_lines_part() -> None:
    """Two lines that share a road then part, part where they split."""
    found = partings(lines=fork(split=3000.0))
    assert found
    assert all(
        math.dist(point, (3000.0, 0.0)) <= APART + PARTING_STEP
        for point in found
    )


def test_lines_parting_before_300_m_do_not_count() -> None:
    """Lines that share under 300 m before parting have no parting."""
    assert partings(lines=fork(split=225.0)) == ()


def test_dashed_lines_have_no_partings() -> None:
    """Only solid lines part."""
    dashed = tuple(replace(line, solid=None) for line in fork(split=3000.0))
    assert partings(lines=dashed) == ()
