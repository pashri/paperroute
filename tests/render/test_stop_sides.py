"""Tests for drawing the next bus from each kerb."""

from dataclasses import replace
from typing import Final

import numpy as np

from paperroute_device.render.canvas import HEIGHT, WIDTH
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.stop_sides import NOTHING_THIS_SIDE, TOWARDS
from paperroute_transit.core.stop_sides import each_way as each_way_view
from paperroute_transit.core.stop_under import EachWay
from paperroute_transit.core.stop_view import Row, StopView
from paperroute_transit.render.names import Naming
from paperroute_transit.render.stop_sides import (
    KERB_STEP,
    draw_each_way,
    draw_each_way_view,
)
from paperroute_transit.render.stop_view import SPLIT, draw_stop_view
from tests.core.test_stop_sides import BOTH, inputs_of, item
from tests.render.test_chrome import Recording

VIEW: Final[StopView] = each_way_view(inputs=inputs_of(items=(
    item(minutes=2, across=True), item(minutes=5, across=False),
    item(minutes=7, across=True),
)), towards=BOTH.towards)


def kerbs(*, across: bool = True) -> EachWay[Row]:
    """The test view's two kerbs, the one across without a bus if asked."""
    assert VIEW.each_way is not None
    if across:
        return VIEW.each_way
    gone = replace(VIEW.each_way.across, hero=None, under=None)
    return replace(VIEW.each_way, across=gone)


def test_draw_each_way_labels_both_kerbs_towards_their_ends() -> None:
    """Each kerb is labelled towards the place its buses run."""
    canvas = Recording()
    naming = Naming(rules=VIEW.name_rules)
    draw_each_way(canvas, each_way=kerbs(), top=HEADER, naming=naming)
    assert [text for text in canvas.drawn if text in (
        TOWARDS, 'Leichhardt', 'Newtown',
    )] == [TOWARDS, 'Leichhardt', TOWARDS, 'Newtown']


def test_kerb_without_a_bus_says_nothing_comes() -> None:
    """A kerb with no bus in view keeps its block and says so."""
    lone = kerbs(across=False)
    canvas = Recording()
    naming = Naming(rules=VIEW.name_rules)
    draw_each_way(canvas, each_way=lone, top=HEADER, naming=naming)
    assert NOTHING_THIS_SIDE in canvas.drawn


def test_draw_each_way_view_keeps_each_kerb_in_its_band() -> None:
    """The kerb across draws below its rule, left of the split."""
    lone = kerbs(across=False)
    full = draw_each_way_view(view=VIEW)
    empty = draw_each_way_view(view=replace(VIEW, each_way=lone))
    rows, columns = np.nonzero(full != empty)
    assert full.shape == (HEIGHT, WIDTH)
    assert rows.min() >= HEADER + KERB_STEP and columns.max() < SPLIT


def test_draw_each_way_view_without_kerbs_is_the_stop_view() -> None:
    """A view with no kerbs draws as the plain Stop view."""
    plain = replace(VIEW, each_way=None)
    assert np.array_equal(
        draw_each_way_view(view=plain), draw_stop_view(view=plain),
    )
