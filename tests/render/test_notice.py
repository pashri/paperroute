"""Tests for the notice screen."""

from paperroute.core.messages import NoticeView
from paperroute.render.canvas import HEIGHT, INK
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


def test_notice_detail_line() -> None:
    """A detail adds ink below the title; no detail draws nothing more."""
    plain = draw_notice_view(view=NoticeView(title='Building the timetable'))
    detailed = draw_notice_view(
        view=NoticeView(
            title='Building the timetable',
            detail='This takes about 10 minutes',
        ),
    )
    below = slice(HEIGHT // 2 + 24, HEIGHT // 2 + 64)
    assert not (plain[below] == INK).any()
    assert (detailed[below] == INK).any()
    assert (detailed[: HEIGHT // 2 + 20] == plain[: HEIGHT // 2 + 20]).all()
