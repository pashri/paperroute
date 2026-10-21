"""Tests for the notice screen."""

from paperroute.core.messages import NoticeView
from paperroute.render.canvas import INK
from paperroute.render.notice import draw_notice_view

BORDER = 40


def test_notice_frame() -> None:
    """The title is inked in the middle third, clear of a 40 px border."""
    view = NoticeView(title='Shut down - safe to unplug')
    frame = draw_notice_view(view=view)
    assert frame.shape == (480, 800)
    assert (frame[160:320, 266:534] == INK).any()
    inner = frame[BORDER:-BORDER, BORDER:-BORDER]
    assert (frame == INK).sum() == (inner == INK).sum()
