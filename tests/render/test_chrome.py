"""Tests for the shared header, banner and badge."""

from paperroute.render.canvas import INK, PAPER, Canvas, Face, text_width
from paperroute.render.chrome import TABS, header


def _tab_x(*, tab: str) -> int:
    """Return an x inside the box of a tab in the header."""
    right = 800 - 12 - text_width(
        text='8:41', face=Face(weight=800, px=22))
    for name in reversed(TABS):
        width = text_width(text=name, face=Face(weight=700, px=12)) + 10
        right -= width + 4
        if name == tab:
            return int(right + 2)
    raise ValueError(tab)


def test_header_marks_active_tab() -> None:
    """The active tab is a paper box; the others stay ink."""
    canvas = Canvas()
    header(
        canvas, origin='A', destination='B', clock='8:41', active='Map')
    grey = canvas.grey()
    assert int(grey[13, _tab_x(tab='Map')]) == PAPER
    assert int(grey[13, _tab_x(tab='Stop')]) == INK
