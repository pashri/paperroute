"""Tests for the connection Stop view's model."""

from dataclasses import replace
from typing import Final

from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.connection_factories import (
    HOLLIS,
    PARRAMATTA,
    WESTMEAD,
    WORK,
    WORK_CHANGE,
    at,
    run,
    work_chains,
)
from paperroute_testkit.core.factories import STALE_LIMIT, live, place
from paperroute_transit.core.chains import Chain
from paperroute_transit.core.connection_map import sidebar_rows
from paperroute_transit.core.connection_strip import build_connection_strip
from paperroute_transit.core.connection_view import (
    NO_PAIRS,
    PAIR_ROWS,
    HeroLine,
    build_connection_view,
    chain_hero,
    chain_row,
    change_lines,
    route_chips,
    via_text,
)
from paperroute_transit.core.model import Change, Pin, Way
from paperroute_transit.core.rides import Ride
from paperroute_transit.core.stop_view import StopView, empty_text

CHAINS: Final[tuple[Chain, ...]] = work_chains()
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
TWO: Final[Pin] = Pin(
    origin=HOLLIS,
    destination=PARRAMATTA,
    kind=None,
    ways=(Way(changes=(
        WORK_CHANGE, Change(alight=WESTMEAD, board=WESTMEAD, allow=4),
    )),),
)
A0733: Final = run(departs=(7, 33), arrives=(7, 39), route='606',
                   trip_id='a0733')
B0746: Final = run(departs=(7, 46), arrives=(7, 57), route='665',
                   trip_id='b0746')
C0805: Final = run(departs=(8, 5), arrives=(8, 20), route='T1',
                   trip_id='c0805')
TWO_CHANGES: Final[Chain] = Chain(
    legs=tuple(live(item=item) for item in (A0733, B0746, C0805)), way=0,
)
DIRECT: Final[Chain] = Chain(
    legs=(live(item=run(
        departs=(7, 40), arrives=(8, 5), route='609', trip_id='d0740',
    )),),
    way=None,
)
LIVE_DIRECT: Final[Chain] = Chain(
    legs=(replace(DIRECT.legs[0], live=True),), way=None,
)
STAY: Final[Chain] = Chain(
    legs=(live(item=A0733), live(item=replace(A0733, departs=at(7, 39)))),
    way=0,
)


def test_hero_spells_out_steps() -> None:
    """The next chain counts down and spells out each step."""
    hero = chain_hero(
        chain=CHAINS[0],
        pin=WORK,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert hero.countdown == '3'
    assert hero.leaves_at == '7:33'
    assert [line.route for line in hero.lines] == ['606', None, None, '665']
    assert [line.text for line in hero.lines[1:3]] == [
        'change at Constitution Hill, North West Twy 7:46',
        'walk 150 m',
    ]
    assert hero.lines[0].noun == 'bus'
    assert (hero.lines[0].at, hero.lines[3].at) == ('7:33', '7:46')
    assert hero.arrives_at == '7:57'
    assert not hero.live


def test_hero_lists_each_change() -> None:
    """A two-change chain has three badge lines and two change lines."""
    hero = chain_hero(
        chain=TWO_CHANGES,
        pin=TWO,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    badges = [line for line in hero.lines if line.route is not None]
    changes = [
        line.text for line in hero.lines if line.text.startswith('change')
    ]
    assert [line.route for line in badges] == ['606', '665', 'T1']
    assert changes == [
        'change at Constitution Hill, North West Twy 7:46',
        'change at Westmead Station 8:05',
    ]
    assert 'walk 150 m' in [line.text for line in hero.lines]
    assert len(hero.lines) == 6
    assert hero.arrives_at == '8:20'


def test_direct_hero() -> None:
    """A direct trip says where it goes."""
    hero = chain_hero(
        chain=DIRECT, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE, city=SYDNEY,
    )
    assert hero.lines[1] == HeroLine(
        route=None, noun=None, text='direct to Westmead Station', at='',
    )
    assert len(hero.lines) == 2


def test_stay_on_folds_into_one_leg() -> None:
    """Two legs on one trip read as one stay-on leg."""
    hero = chain_hero(
        chain=STAY,
        pin=WORK,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert [line.route for line in hero.lines] == ['606', None]
    assert hero.lines[1].text == 'stay on to Westmead Station'
    row = chain_row(chain=STAY, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE)
    assert row.chips == ('606',)
    assert row.detail == 'stay on'


def test_chain_row_counts_down_when_live() -> None:
    """A live first leg counts down and keeps its clock time."""
    row = chain_row(
        chain=LIVE_DIRECT,
        pin=WORK,
        now=at(5, 30),
        zone=SYDNEY_ZONE,
    )
    assert row.countdown == '130'
    assert row.live
    assert row.leaves_at == '7:40'


def test_chain_row_timetable_keeps_the_clock() -> None:
    """A timetable-only first leg is not live."""
    row = chain_row(chain=DIRECT, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE)
    assert not row.live
    assert row.leaves_at == '7:40'


def test_every_pair_surface_gets_the_count() -> None:
    """The Stop list, the sidebar and the Strip pairs all count down."""
    chains = (LIVE_DIRECT, LIVE_DIRECT)
    now = at(7, 28)
    view = build_connection_view(
        stop_view=STOP, pin=WORK, chains=chains, scroll=0, now=now,
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    strip = build_connection_strip(
        stop_view=STOP,
        pin=WORK,
        chains=chains,
        selected=0,
        patterns={},
        positions={},
        now=now,
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    sidebar = sidebar_rows(pin=WORK, chains=chains, now=now, zone=SYDNEY_ZONE)
    assert view.rows[0].countdown == '12'
    assert sidebar[0].countdown == '12'
    assert strip.pairs[0].countdown == '12'


def test_row_at_one_stop() -> None:
    """A one-change row says where to change."""
    row = chain_row(chain=CHAINS[1], pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE)
    assert row.detail == 'at Constitution Hill, North West Twy'
    assert row.chips == ('606', '664')
    assert (row.leaves_at, row.arrives_at, row.minutes) == (
        '7:48', '8:12', 24,
    )


def test_row_via_two_stops() -> None:
    """A two-change row lists both boarding stops."""
    assert via_text(chain=TWO_CHANGES, pin=TWO) == (
        'via Constitution Hill, North West Twy, Westmead Station'
    )
    assert route_chips(chain=TWO_CHANGES, pin=TWO) == ('606', '665', 'T1')


def test_direct_row_single_chip() -> None:
    """A direct row has one chip and says direct."""
    row = chain_row(chain=DIRECT, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE)
    assert row.chips == ('609',)
    assert row.detail == 'direct'


def test_rows_after_hero() -> None:
    """The list holds the chains after the hero, scrolled."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, chains=CHAINS, scroll=0, now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert view.hero is not None
    assert [row.leaves_at for row in view.rows] == [
        '7:48', '8:03', '8:22', '8:42',
    ]
    scrolled = build_connection_view(
        stop_view=STOP, pin=WORK, chains=CHAINS, scroll=2, now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert scrolled.rows[0].leaves_at == '8:22'


def test_rows_capped_and_scroll_clamped() -> None:
    """At most five rows show, and a long scroll keeps one row."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, chains=CHAINS + CHAINS, scroll=0,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert len(view.rows) == PAIR_ROWS
    far = build_connection_view(
        stop_view=STOP, pin=WORK, chains=CHAINS, scroll=40, now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert [row.leaves_at for row in far.rows] == ['8:42']


def test_no_chains_text() -> None:
    """No chain says so, unless the board is still loading."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, chains=(), scroll=0, now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert view.hero is None
    assert not view.rows
    assert view.empty == 'No connections to Westmead Station in the next 3 hours'
    loading = empty_text(destination='Westmead Station', loaded=False)
    view = build_connection_view(
        stop_view=replace(STOP, empty=loading),
        pin=WORK,
        chains=(),
        scroll=0,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert view.empty == loading


def test_no_direct_trip_still_says_no_chains() -> None:
    """The direct list's own empty text does not replace the no-chain text."""
    direct = empty_text(destination='Hammers Rd', loaded=True)
    view = build_connection_view(
        stop_view=replace(STOP, empty=direct),
        pin=WORK,
        chains=(),
        scroll=0,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert view.empty == 'No connections to Westmead Station in the next 3 hours'


def test_no_chains_without_destination() -> None:
    """Without a To name the no-chain text names no place."""
    view = build_connection_view(
        stop_view=replace(STOP, destination=None),
        pin=WORK,
        chains=(),
        scroll=0,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert view.empty == NO_PAIRS == 'No connections in the next 3 hours'


def test_chrome_from_stop_view() -> None:
    """The banner, warning, pill and titles come from the Stop view."""
    view = build_connection_view(
        stop_view=STOP, pin=WORK, chains=CHAINS, scroll=0, now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert view.banner == 'Lift out of order'
    assert (view.warning, view.more, view.planned) == (True, 2, False)
    assert (view.origin, view.destination, view.clock) == (
        STOP.origin, STOP.destination, '7:30',
    )
    assert view.empty is None


def test_change_at_stops_no_distance_apart_has_no_walk() -> None:
    """Two stops with no distance between them say only the change."""
    ride = Ride(
        item=live(item=A0733),
        last=live(item=A0733),
        start=place('b'),
        end=place('c'),
        change=Change(alight=place('a'), board=place('b'), allow=2),
    )
    assert [line.text for line in change_lines(
        ride=ride,
        zone=SYDNEY_ZONE,
    )] == [
        'change at  7:33',
    ]
