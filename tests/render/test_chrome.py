"""Tests for the shared header, banner and badge."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from paperroute.core.stop_view import NOT_PINNED, PinTag
from paperroute.core.stop_view import pin_tag as tag_of
from paperroute.render.canvas import (
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
    Point,
    text_width,
)
from paperroute.render.chrome import (
    TABS,
    header,
    header_tabs,
    pin_tag,
    view_header,
)

PINNED = PinTag(text='pin 2/3', pinned=True)


class Recording(Canvas):
    """A canvas that keeps the texts and polygons it is asked to draw."""

    def __init__(self) -> None:
        super().__init__()
        self.fitted: list[str] = []
        self.drawn: list[str] = []
        self.polygons: list[Sequence[Point]] = []

    def fit(self, *, text: str, face: Face, width: float) -> str:
        """Record the text and fit it as usual."""
        self.fitted.append(text)
        return super().fit(text=text, face=face, width=width)

    def text(self, **kwargs: Any) -> float:
        """Record the text and draw it as usual."""
        self.drawn.append(kwargs['text'])
        return super().text(**kwargs)

    def polygon(self, *, points: Sequence[Point], grey: int) -> None:
        """Record the polygon and draw it as usual."""
        self.polygons.append(points)
        super().polygon(points=points, grey=grey)


def _tab_x(*, tab: str) -> int:
    """Return an x inside the box of a tab in the header."""
    right = WIDTH - 12 - text_width(
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
        canvas,
        origin='A',
        destination='B',
        clock='8:41',
        active='Map',
        tag=NOT_PINNED,
    )
    grey = canvas.grey()
    assert int(grey[13, _tab_x(tab='Map')]) == PAPER
    assert int(grey[13, _tab_x(tab='Stop')]) == INK
    x = _tab_x(tab='Map')
    assert int(grey[12:30, x:x + 30].min()) == INK


def _tag_box(*, tag: PinTag) -> tuple[Canvas, float]:
    """Draw a tag to the left of the tabs on an ink header."""
    canvas = Canvas()
    canvas.rect(box=(0, 0, WIDTH, 40), grey=INK)
    right = header_tabs(canvas, clock='8:41', active='Map')
    return canvas, pin_tag(canvas, right=right, tag=tag)


def test_pin_tag_text() -> None:
    """A pinned place reads pin n/total; an unpinned one not pinned."""
    assert tag_of(index=1, total=3).text == 'pin 2/3'
    assert tag_of(index=1, total=3).pinned
    assert tag_of(index=None, total=3) == NOT_PINNED
    assert NOT_PINNED.text == 'not pinned'


def test_pinned_tag_is_paper_box() -> None:
    """A pinned tag is a paper box 19 px tall with ink text."""
    canvas, left = _tag_box(tag=PINNED)
    grey = canvas.grey()
    x = int(left) + 1
    assert int(grey[11, x]) == PAPER
    assert int(grey[29, x]) == PAPER
    assert int(grey[10, x]) == INK
    assert int(grey[30, x]) == INK
    assert int(grey[11:30, x:x + 60].min()) == INK
    assert int(grey[12, x]) == PAPER


def test_pin_tag_sits_8px_left_of_the_tabs() -> None:
    """The tag's right edge is 8 px left of the edge given."""
    canvas = Canvas()
    width = text_width(text='pin 2/3', face=Face(weight=700, px=12)) + 10
    left = pin_tag(canvas, right=300, tag=PINNED)
    assert left == 300 - 8 - width


def test_unpinned_tag_is_dashed() -> None:
    """An unpinned tag's outline row alternates paper and ink."""
    canvas, left = _tag_box(tag=NOT_PINNED)
    row = canvas.grey()[11, int(left):int(left) + 40]
    assert PAPER in row
    assert INK in row
    inside = canvas.grey()[20, int(left) + 1]
    assert int(inside) == INK


def test_unpinned_tag_text_is_paper() -> None:
    """An unpinned tag's text is paper on the ink header."""
    canvas, left = _tag_box(tag=NOT_PINNED)
    text = canvas.grey()[14:27, int(left) + 3:int(left) + 50]
    assert int(text.max()) == PAPER


def test_no_destination_title() -> None:
    """With no destination the header draws no arrow and one name."""
    canvas = Recording()
    header(
        canvas,
        origin='Town Hall',
        destination=None,
        clock='8:41',
        active='Stop',
        tag=NOT_PINNED,
    )
    assert canvas.fitted == ['Town Hall']
    assert not any(len(points) == 3 for points in canvas.polygons)
    assert 'None' not in canvas.drawn


def test_picking_title() -> None:
    """Picking shows the origin, an arrow and the pick target."""
    canvas = Recording()
    header(
        canvas,
        origin='Town Hall',
        destination=None,
        clock='8:41',
        active='Stop',
        tag=NOT_PINNED,
        picking='To',
    )
    assert canvas.fitted == ['Town Hall', 'pick To']
    assert 'pick To' in canvas.drawn
    assert len(canvas.polygons) == 1


def test_titles_stop_before_the_tag() -> None:
    """A long origin and destination leave the tag's box untouched."""
    canvas = Canvas()
    header(
        canvas,
        origin='A very long origin stop name indeed' * 3,
        destination='An equally long destination stop name' * 3,
        clock='8:41',
        active='Map',
        tag=PINNED,
    )
    left = (
        WIDTH - 12
        - text_width(text='8:41', face=Face(weight=800, px=22))
    )
    for tab in TABS:
        left -= text_width(text=tab, face=Face(weight=700, px=12)) + 14
    box_left = left - 8 - (
        text_width(text='pin 2/3', face=Face(weight=700, px=12)) + 10
    )
    grey = canvas.grey()
    assert int(grey[:40, int(box_left) - 8:int(box_left)].max()) == INK
    ink_columns = np.flatnonzero(grey[:40, :int(box_left)].max(axis=0))
    limit = box_left - 8
    slack = text_width(text='W…', face=Face(weight=700, px=18))
    assert limit - 18 - slack <= ink_columns.max() <= limit - 18


@dataclass(frozen=True)
class Titles:
    """The texts a header shows."""

    origin: str
    destination: str | None
    tag: PinTag
    clock: str


def test_view_header_passes_picking() -> None:
    """A view's header shows the pick target while picking."""
    view = Titles(
        origin='Town Hall', destination='Parramatta', tag=NOT_PINNED,
        clock='8:41',
    )
    picking = Recording()
    view_header(picking, view=view, active='Strip', picking='TO')
    plain = Recording()
    view_header(plain, view=view, active='Strip')
    assert picking.fitted == ['Town Hall', 'pick TO']
    assert plain.fitted == ['Town Hall', 'Parramatta']
