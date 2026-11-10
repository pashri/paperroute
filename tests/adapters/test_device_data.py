"""Tests for the Device page's Data section."""

from dataclasses import replace
from datetime import UTC
from typing import Final

import pytest

from paperroute_device.core.device_model import DeviceStatus
from paperroute_device_testkit.device_pages import FACTS, NOW, STATUS
from paperroute_sydney.city import SYDNEY_ZONE, SydneyFeed
from paperroute_sydney.sydney_feeds import (
    DAILY_LIMIT,
    FEEDS,
    POSITION_FEEDS,
    feed_label,
)
from paperroute_transit.adapters.device_data import DATA_SECTION, data_section
from paperroute_transit.core.panel_screens import TilesFacts
from tests.golden.scenes import GOLDEN
from tests.test_observers import APP_STATUS

TILES: Final[TilesFacts] = TilesFacts(built='2025-10-19T08:00:00+11:00')
SYDNEY_STATUS: Final[DeviceStatus] = replace(
    STATUS, facts=replace(FACTS, app=TILES),
)
TRAINS: Final[str] = POSITION_FEEDS[SydneyFeed.TRAIN][0]
BUSES: Final[str] = FEEDS[SydneyFeed.BUS]
FEEDS_SEEN: Final[dict[str, float]] = {TRAINS: NOW - 12, BUSES: NOW - 40}
OPEN: Final[str] = '    <section class="card setting" aria-labelledby="data">'


def section(*, feeds: dict[str, float] | None = None, **change: object) -> str:
    """
    Draw the section over the fixed status, with changes.

    Parameters
    ----------
    feeds : dict[str, float] | None
        When each feed last worked; the fixed two when None.
    **change : object
        Replacements for `data_section`'s arguments.

    Returns
    -------
    str
        The section's markup.
    """
    args: dict[str, object] = {
        'status': APP_STATUS,
        'facts': SYDNEY_STATUS.facts,
        'feeds': FEEDS_SEEN if feeds is None else feeds,
        'now': NOW,
        'label': feed_label,
        'daily_limit': DAILY_LIMIT,
        'zone': SYDNEY_ZONE,
        **change,
    }
    return data_section(**args)  # type: ignore[arg-type]


def test_data_section_matches_old_bytes() -> None:
    """The section is the one the golden Device page holds."""
    page = (GOLDEN / 'pages' / 'device.html').read_text(encoding='utf-8')
    start = page.index(OPEN)
    end = page.index('</section>\n', start) + len('</section>\n')
    assert f'    {section()}\n' == page[start:end]
    assert DATA_SECTION.startswith('<section class="card setting"')


def test_feed_label_is_escaped() -> None:
    """A feed's name is escaped where it is drawn."""
    text = section(feeds={'x': NOW - 1}, label=lambda feed: '<script>')
    assert '<th scope="row">&lt;script&gt;</th>' in text
    assert '<script>' not in text


@pytest.mark.parametrize(('change', 'value'), [
    ({'rebuilding': True}, 'rebuilding'),
    ({'rebuild_failed': True, 'timetable': None}, 'last build failed'),
    ({'timetable': None}, 'none yet'),
    (
        {'rebuild_failed': True},
        'version 6 · built 21 Oct 21:45 · last build failed',
    ),
])
def test_timetable_row(change: dict[str, object], value: str) -> None:
    """The Timetable row says what the app status holds."""
    text = section(status={**APP_STATUS, **change})
    assert f'<dd>{value}</dd>' in text


def test_row_values_are_escaped() -> None:
    """A timetable stamp that isn't a time is not drawn as markup."""
    text = section(status={**APP_STATUS, 'schema': '<b>'})
    assert '<b>' not in text
    assert 'version &lt;b&gt;' in text


@pytest.mark.parametrize(('facts', 'value'), [
    (replace(FACTS, app=TilesFacts(built=None)), 'none'),
    (replace(FACTS, app=None), 'none'),
    (None, '-'),
])
def test_map_tiles_row(facts: object, value: str) -> None:
    """The Map tiles row says when they were built, or that none are."""
    text = section(facts=facts)
    assert f'<dt>Map tiles</dt>\n          <dd>{value}</dd>' in text


def test_no_status_shows_dashes_and_the_limit_is_given() -> None:
    """Before the app reports, timetable and calls are dashes."""
    text = section(status=None, feeds={})
    assert '<dt>Timetable</dt>\n          <dd>-</dd>' in text
    assert '<dd>-</dd>\n        </div>\n      </dl>' in text
    assert '<td colspan="3">none yet</td>' in text
    assert 'of 1,234' in section(daily_limit=1234)


def test_feed_times_are_read_in_the_given_zone() -> None:
    """Each feed's last good time is drawn in the zone it is given."""
    sydney = section(feeds={TRAINS: NOW - 12})
    utc = section(feeds={TRAINS: NOW - 12}, zone=UTC)
    assert '<td>21:44:48</td><td>12 s</td>' in sydney
    assert '<td>10:44:48</td><td>12 s</td>' in utc
