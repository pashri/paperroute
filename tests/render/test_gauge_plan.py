"""Tests for measuring the gauge against the hero's ink."""

from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import HEIGHT, WIDTH
from paperroute_transit.core.gauge_ends import RouteGauge
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.stop_under import GaugeCall
from paperroute_transit.render.gauge_plan import (
    LINE_FREE,
    NAME_FACE,
    WIDE_PITCH,
    keep_out,
    mark_lines,
    name_room,
)
from paperroute_transit.render.gauge_route import route_plan
from paperroute_transit.render.names import Naming
from tests.render.test_gauge_draw import named_trip

PLAIN: Final[Naming] = Naming(rules=NameRules())
CLEAR: Final[NDArray[np.bool_]] = keep_out(
    ink=np.zeros((HEIGHT, WIDTH), dtype=bool),
)


def blocked(*, rows: slice) -> NDArray[np.bool_]:
    """Keep-out for hero ink across some rows."""
    ink = np.zeros((HEIGHT, WIDTH), dtype=bool)
    ink[rows, 18:400] = True
    return keep_out(ink=ink)


def test_keep_out_grows_ink_and_closes_the_right() -> None:
    """Ink grows five pixels and the right edge is shut."""
    ink = np.zeros((HEIGHT, WIDTH), dtype=bool)
    ink[100, 300] = True
    keep = keep_out(ink=ink)
    assert keep[100, 305] and keep[95, 295]
    assert not keep[100, 306] and not keep[94, 300]
    assert keep[470, 415] and not keep[470, 414]


def test_name_room_runs_to_the_right_edge_on_clear_paper() -> None:
    """On clear paper a name runs to the edge."""
    assert name_room(
        keep=CLEAR, x=300.0, line_y=LINE_FREE, face=NAME_FACE,
    ) == pytest.approx(220, abs=2)


def test_name_room_stops_short_of_hero_text() -> None:
    """Hero ink shortens the room."""
    assert name_room(
        keep=blocked(rows=slice(370, 381)), x=28.0, line_y=LINE_FREE,
        face=NAME_FACE,
    ) == pytest.approx(48, abs=2)


def test_mark_lines_wrap_a_name_too_long_for_one_line() -> None:
    """A long name wraps onto two lines."""
    keep = blocked(rows=slice(265, 276))
    names = ('Hollis St', 'Cottenham Rampton Road Histon')
    gauge = RouteGauge(
        pattern=named_trip(names=names),
        start=0,
        end=None,
        calls=(GaugeCall(name=names[1], minutes=':05'),),
        buses=(),
    )
    plan = route_plan(gauge=gauge, keep=keep, naming=PLAIN)
    lines = mark_lines(plan=plan, keep=keep, naming=PLAIN)
    assert plan.wrap
    assert plan.pitch >= WIDE_PITCH
    assert lines[0] == ('Hollis St',)
    assert lines[1] == ('Cottenham Rampton', 'Road Histon')
