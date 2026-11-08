"""Tests for Sydney's board pages."""

import re
from importlib.resources import files

import pytest

from paperroute.adapters.web_board import (
    SYDNEY_NAV,
    SuggestPlug,
    sydney_suggest,
)
from paperroute_device.adapters.web_frame import nav_html
from paperroute_testkit.fakes import FakeTripPlanner


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


@pytest.mark.parametrize(('name', 'current'), [
    ('settings.html', '/settings'),
    ('pins.html', '/pins'),
    ('pin-edit.html', '/pins'),
])
def test_sydney_nav_matches_board_templates(
    name: str, current: str
) -> None:
    """The app's own templates carry exactly the nav Sydney gives the kit."""
    text = (files('paperroute') / 'assets' / 'web' / name).read_text(
        encoding='utf-8',
    )
    found = re.search(r'<nav\b.*?</nav>', text, flags=re.DOTALL)
    assert found is not None
    assert found.group(0) == nav_html(items=SYDNEY_NAV, current=current)
