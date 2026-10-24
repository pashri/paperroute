"""Tests for the Alerts view drawing."""

from typing import Final

import numpy as np

from paperroute.core.alert_model import Alert
from paperroute.core.alerts_view import AlertRow, AlertsView, AlertText
from paperroute.core.stop_view import NOT_PINNED
from paperroute.render.alerts_view import (
    ALERT_SPLIT,
    BODY_PITCH,
    TEXT_HEIGHT,
    TEXT_LEFT,
    TEXT_RIGHT,
    draw_alerts_view,
    pages_of,
)
from paperroute.render.canvas import INK, PAPER, WIDTH, Face, text_width
from paperroute.render.chrome import HEADER
from tests.core.alert_factories import STRATHFIELD_UPDATE, WESTERN

ROW_H = 70
TEXT_WIDTH: Final[int] = TEXT_RIGHT - TEXT_LEFT
LONG = 'This is a long description. ' * 60


def text_of(*, found: Alert, body: str | None = None) -> AlertText:
    """
    Build an alert's full text.

    Parameters
    ----------
    found : Alert
        The alert.
    body : str | None
        Description to use instead of the alert's.

    Returns
    -------
    AlertText
        Its text.
    """
    return AlertText(
        title=found.header,
        when='Now until Mon 02:00',
        body=found.description if body is None else body,
        also=(),
    )


def view_of(
    *, text: AlertText, selected: int = 0, page: int = 0
) -> AlertsView:
    """
    Build a view of two rows, the first active, the second coming.

    Parameters
    ----------
    text : AlertText
        Selected text.
    selected : int
        Selected row.
    page : int
        Page counter.

    Returns
    -------
    AlertsView
        The view.
    """
    rows = (
        AlertRow(headline=WESTERN.header, when='Now until Mon', active=True),
        AlertRow(
            headline=STRATHFIELD_UPDATE.header,
            when='From Sat 23:40, ongoing',
            active=False,
        ),
        AlertRow(headline='Third alert', when='Ongoing', active=True),
    )
    return AlertsView(
        origin='Strathfield',
        destination=None,
        clock='09:39',
        tag=NOT_PINNED,
        rows=rows,
        selected=selected,
        text=text,
        page=page,
    )


def row_box(*, frame: np.ndarray, row: int) -> np.ndarray:
    """
    Cut a row's box out of a frame.

    Parameters
    ----------
    frame : np.ndarray
        The frame.
    row : int
        Row index.

    Returns
    -------
    np.ndarray
        The row's pixels.
    """
    top = HEADER + row * ROW_H
    return frame[top:top + ROW_H, :ALERT_SPLIT]


def triangle_box(*, frame: np.ndarray, row: int) -> np.ndarray:
    """
    Cut the mark's box out of a row.

    Parameters
    ----------
    frame : np.ndarray
        The frame.
    row : int
        Row index.

    Returns
    -------
    np.ndarray
        The pixels at x 12-30 around the row's mark.
    """
    middle = HEADER + row * ROW_H + 24
    return frame[middle - 9:middle + 9, 12:30]


def test_selected_row_is_inverted() -> None:
    """The selected row is mostly ink and another row mostly paper."""
    text = text_of(found=WESTERN)
    frame = draw_alerts_view(view=view_of(text=text, selected=1))
    assert (row_box(frame=frame, row=1) == INK).mean() > 0.7
    assert (row_box(frame=frame, row=0) == INK).mean() < 0.3


def test_active_and_planned_marks() -> None:
    """An active mark is solid; a planned one is an outline."""
    text = text_of(found=WESTERN)
    frame = draw_alerts_view(view=view_of(text=text, selected=2))
    solid = triangle_box(frame=frame, row=0)
    outline = triangle_box(frame=frame, row=1)
    assert (solid[9:16, 7:11] == INK).all()
    assert (outline[10:13, 7:11] == PAPER).all()
    assert (outline == INK).any()


def test_long_text_pages_and_marker() -> None:
    """Long text gives pages, a later page differs and shows n/m."""
    text = text_of(found=WESTERN, body=WESTERN.description * 3)
    pages = pages_of(text=text, width=TEXT_WIDTH, height=TEXT_HEIGHT)
    assert len(pages) > 1
    first = draw_alerts_view(view=view_of(text=text, page=0))
    second = draw_alerts_view(view=view_of(text=text, page=1))
    assert (first[:, ALERT_SPLIT + 15:] != second[:, ALERT_SPLIT + 15:]).any()
    assert (first[452:, 730:] == INK).any()


def test_page_wraps() -> None:
    """A page counter past the last page draws the first page."""
    text = text_of(found=WESTERN, body=WESTERN.description * 3)
    pages = pages_of(text=text, width=TEXT_WIDTH, height=TEXT_HEIGHT)
    first = draw_alerts_view(view=view_of(text=text, page=0))
    wrapped = draw_alerts_view(view=view_of(text=text, page=len(pages)))
    assert np.array_equal(first, wrapped)


def test_short_text_no_marker() -> None:
    """One page draws no marker."""
    text = text_of(found=STRATHFIELD_UPDATE)
    pages = pages_of(text=text, width=TEXT_WIDTH, height=TEXT_HEIGHT)
    assert len(pages) == 1
    frame = draw_alerts_view(view=view_of(text=text))
    assert (frame[452:, 730:] == PAPER).all()


def test_pages_fit_their_height() -> None:
    """No page holds more lines than the height allows."""
    text = text_of(found=WESTERN, body=LONG)
    pages = pages_of(text=text, width=TEXT_WIDTH, height=TEXT_HEIGHT)
    assert all(pages)
    assert max(len(page) for page in pages) <= TEXT_HEIGHT // BODY_PITCH


def test_alerts_tab_active() -> None:
    """The rightmost tab, Alerts, is a paper box on the Alerts view."""
    text = text_of(found=WESTERN)
    frame = draw_alerts_view(view=view_of(text=text))
    clock = text_width(text='09:39', face=Face(weight=800, px=22))
    width = text_width(text='Alerts', face=Face(weight=700, px=12)) + 10
    right = WIDTH - 12 - clock - 4
    middle = int(right - width / 2)
    assert (frame[12, middle - 5:middle + 5] == PAPER).all()
    assert (frame[11:30, int(right - width) + 1:int(right)] == INK).any()
