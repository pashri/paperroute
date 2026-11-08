"""Tests for Sydney's board pages."""

import re
from importlib.resources import files

import pytest

from paperroute.adapters.web_board import (
    SYDNEY_NAV,
    SuggestPlug,
    sydney_status_line,
    sydney_suggest,
)
from paperroute_device.adapters.web_frame import nav_html
from paperroute_transit.adapters.board_head import BoardHead
from tests.planner_fakes import FakeTripPlanner


async def test_suggest_plug_closes_the_planner() -> None:
    """Closing the plug closes the Trip Planner."""
    planner = FakeTripPlanner()
    await SuggestPlug(planner=planner).close()
    assert planner.closed


async def test_suggest_plug_closes_without_a_planner() -> None:
    """Closing a plug with no Trip Planner does nothing."""
    await SuggestPlug(planner=None).close()


async def test_sydney_suggest_is_always_a_plug() -> None:
    """Replayed feeds still get the suggestion plug, with no planner."""
    plug = sydney_suggest(live=False)
    assert isinstance(plug, SuggestPlug)
    await plug.close()


def test_sydney_status_line_matches_old_js() -> None:
    """The status line reads as the page script wrote it, rounded half up."""
    status = {
        'frame_age_s': 2.5,
        'last_render_ms': 118.4,
        'feeds': {'v1/gtfs/realtime/buses': {'parse_ms': 41.5}},
    }
    blank = 'Frame age - s, render - ms, bus parse - ms'
    assert sydney_status_line(status=status) == (
        'Frame age 3 s, render 118 ms, bus parse 42 ms'
    )
    assert sydney_status_line(status={}) == blank
    assert sydney_status_line(status={'frame_age_s': True}) == blank


@pytest.mark.parametrize(('name', 'current'), [
    ('settings.html', '/settings'),
    ('pins.html', '/pins'),
    ('pin-edit.html', '/pins'),
])
def test_sydney_nav_drawn_into_board_templates(
    name: str, current: str
) -> None:
    """Each board template draws exactly the nav Sydney gives the kit."""
    text = (files('paperroute') / 'assets' / 'web' / name).read_text(
        encoding='utf-8',
    )
    page = BoardHead(nav=SYDNEY_NAV).fill(page=text, current=current)
    found = re.search(r'<nav\b.*?</nav>', page, flags=re.DOTALL)
    assert text.count('\n  {{nav}}\n') == 1
    assert found is not None
    assert found.group(0) == nav_html(items=SYDNEY_NAV, current=current)
