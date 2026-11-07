"""Tests for the notice screen."""

from paperroute.core.messages import NoticeView
from paperroute.core.rebuild import SETTINGS_HINT
from paperroute.render.notice import DETAIL_FACE, draw_notice_view
from paperroute_device.render.canvas import HEIGHT, INK, WIDTH, text_width

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


def test_settings_hint_fits_the_notice() -> None:
    """The rebuild screen's Settings hint fits inside the border."""
    assert SETTINGS_HINT == 'Both knobs: hold to open Settings'
    width = text_width(text=SETTINGS_HINT, face=DETAIL_FACE)
    assert width <= WIDTH - 64
