"""Tests for drawing the next bus from each kerb."""

from dataclasses import replace
from typing import Final

import numpy as np

from paperroute_device.render.canvas import HEIGHT, WIDTH, text_width
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.stop_sides import NOTHING_THIS_SIDE, TOWARDS
from paperroute_transit.core.stop_sides import each_way as each_way_view
from paperroute_transit.core.stop_under import EachWay, GaugeCall, StopGauge
from paperroute_transit.core.stop_view import Row, StopView
from paperroute_transit.render.names import Naming
from paperroute_transit.render.stop_sides import (
    KERB_STEP,
    LABEL_FACE,
    draw_each_way,
    draw_each_way_view,
)
from paperroute_transit.render.stop_view import SPLIT, draw_stop_view
from tests.core.test_stop_sides import BOTH, inputs_of, item
from tests.render.test_chrome import Recording

GAUGE: Final[StopGauge] = StopGauge(
    origin='Orchard Close',
    calls=(
        GaugeCall(name='Hollis St', minutes=':08'),
        GaugeCall(name='Parramatta Rd', minutes=':12'),
        GaugeCall(name='Newtown', minutes=':20'),
    ),
    end=2,
)
LONG_END: Final[str] = (
    'Leichhardt Marketplace Shopping Centre Interchange and Bus Layover'
    ' Bay near the Old Showground Road Reserve'
)
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


def test_draw_each_way_view_draws_no_gauge() -> None:
    """A gauge on the view changes nothing in the each-way drawing."""
    assert np.array_equal(
        draw_each_way_view(view=replace(VIEW, gauge=GAUGE)),
        draw_each_way_view(view=VIEW),
    )


def test_draw_each_way_fits_a_long_end_before_the_split() -> None:
    """A kerb's end too long for its block is shortened to fit."""
    long = replace(kerbs().here, end=LONG_END)
    canvas = Recording()
    naming = Naming(rules=VIEW.name_rules)
    draw_each_way(
        canvas,
        each_way=replace(kerbs(), here=long),
        top=HEADER,
        naming=naming,
    )
    drawn = canvas.drawn[1]
    left = 18 + text_width(text=TOWARDS, face=LABEL_FACE) + 6
    assert drawn != LONG_END
    assert left + text_width(text=drawn, face=LABEL_FACE) <= SPLIT
