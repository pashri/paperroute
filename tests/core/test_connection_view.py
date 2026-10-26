"""Tests for the connection Stop view's model."""

from dataclasses import replace
from typing import Final

from paperroute.core.connection_model import Pair, pairs_of
from paperroute.core.connection_view import (
    MODE_NOUNS,
    NO_PAIRS,
    PAIR_ROWS,
    build_connection_view,
    pair_hero,
    pair_row,
)
from paperroute.core.model import Mode
from paperroute.core.stop_view import StopView, empty_text
from tests.core.connection_factories import LEG1, LEG2, WORK, at, run
from tests.core.factories import live

PAIRS: Final[tuple[Pair, ...]] = pairs_of(
    first=tuple(live(item=item) for item in LEG1),
    second=tuple(live(item=item) for item in LEG2),
    direct=(),
    allow=7,
    now=at(7, 30),
)
STOP: Final[StopView] = StopView(
    origin='Hollis St Opp Constitution Hill Library',
    destination='Westmead Station',
    clock='7:30',
    hero=None,
    rows=(),
    banner='Lift out of order',
    empty=None,
    scroll=0,
    warning=True,
    more=2,
)
STAY: Final[Pair] = Pair(
    first=live(item=run(
        departs=(7, 40), arrives=(8, 5), route='609', trip_id='d0740',
    )),
    second=None,
)


def test_hero_spells_out_steps() -> None:
    """The next pair counts down and spells out each step."""
    hero = pair_hero(pair=PAIRS[0], pin=WORK, now=at(7, 30))
    assert hero.countdown == '3'
    assert hero.route == '606'
    assert hero.mode is Mode.BUS
    assert hero.leaves_at == '7:33'
    assert hero.steps == (
        'get off Hammers Rd Opp Sherwood St 7:39',
        'walk 150 m to Constitution Hill, North West Twy',
    )
    assert hero.onto == '665'
    assert hero.onto_mode is Mode.BUS
    assert hero.onto_at == '7:46'
    assert hero.arrives_at == '7:57'
    assert not hero.live


def test_rows_after_hero() -> None:
    """The list holds the pairs after the hero, scrolled."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, pairs=PAIRS, scroll=0, now=at(7, 30),
    )
    assert view.hero is not None
    assert [row.leaves_at for row in view.rows] == [
        '7:48', '8:03', '8:22', '8:42',
    ]
    first = view.rows[0]
    assert first.detail == 'change 7:54, bus 8:01'
    assert (first.route, first.onto) == ('606', '664')
    assert first.arrives_at == '8:12'
    assert first.minutes == 24
    scrolled = build_connection_view(
        stop_view=STOP, pin=WORK, pairs=PAIRS, scroll=2, now=at(7, 30),
    )
    assert scrolled.rows[0].leaves_at == '8:22'


def test_rows_capped_and_scroll_clamped() -> None:
    """At most five rows show, and a long scroll keeps one row."""
    many = PAIRS + PAIRS
    view = build_connection_view(
        stop_view=STOP, pin=WORK, pairs=many, scroll=0, now=at(7, 30),
    )
    assert len(view.rows) == PAIR_ROWS
    far = build_connection_view(
        stop_view=STOP, pin=WORK, pairs=PAIRS, scroll=40, now=at(7, 30),
    )
    assert [row.leaves_at for row in far.rows] == ['8:42']


def test_stay_row_and_hero() -> None:
    """Staying on reads as one step and one badge."""
    row = pair_row(pair=STAY)
    assert row.detail == 'stay on'
    assert row.onto is None
    assert row.minutes == 25
    hero = pair_hero(pair=STAY, pin=WORK, now=at(7, 30))
    assert hero.steps == ('stay on to Westmead Station',)
    assert hero.onto is None
    assert hero.onto_mode is None
    assert hero.arrives_at == '8:05'


def test_no_pairs_text() -> None:
    """No pair says so, unless the board is still loading."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, pairs=(), scroll=0, now=at(7, 30),
    )
    assert view.hero is None
    assert not view.rows
    assert view.empty == NO_PAIRS
    loading = empty_text(destination='Westmead Station', loaded=False)
    view = build_connection_view(
        stop_view=replace(STOP, empty=loading),
        pin=WORK,
        pairs=(),
        scroll=0,
        now=at(7, 30),
    )
    assert view.empty == loading


def test_no_direct_leg_one_still_says_no_pairs() -> None:
    """Leg 1's own empty text does not replace the no-pair text."""
    direct = empty_text(destination='Hammers Rd', loaded=True)
    view = build_connection_view(
        stop_view=replace(STOP, empty=direct),
        pin=WORK,
        pairs=(),
        scroll=0,
        now=at(7, 30),
    )
    assert view.empty == NO_PAIRS


def test_chrome_from_stop_view() -> None:
    """The banner, warning, pill and titles come from leg 1's view."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, pairs=PAIRS, scroll=0, now=at(7, 30),
    )
    assert view.banner == 'Lift out of order'
    assert (view.warning, view.more, view.planned) == (True, 2, False)
    assert (view.origin, view.destination, view.clock) == (
        STOP.origin, STOP.destination, '7:30',
    )
    assert view.empty is None


def test_mode_nouns_cover_modes() -> None:
    """Every mode has a noun."""
    assert set(MODE_NOUNS) == set(Mode)
    assert MODE_NOUNS[Mode.REGIONAL] == 'train'
    assert MODE_NOUNS[Mode.OTHER] == 'service'
