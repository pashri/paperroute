"""Tests for the connection Stop view's model."""

from dataclasses import replace
from typing import Final

from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.connection_factories import (
    HOLLIS,
    LEG1,
    LEG2,
    PARRAMATTA,
    WESTMEAD,
    WORK,
    WORK_CHANGE,
    at,
    run,
    work_chains,
)
from paperroute_testkit.core.factories import STALE_LIMIT, live, place
from paperroute_transit.core.chains import (
    Chain,
    Missed,
    Offers,
    chains_of,
    offers_after,
)
from paperroute_transit.core.connection_map import sidebar_rows
from paperroute_transit.core.connection_strip import build_connection_strip
from paperroute_transit.core.connection_view import (
    NO_PAIRS,
    PAIR_ROWS,
    ChainHero,
    HeroLine,
    PairRow,
    build_connection_view,
    chain_hero,
    chain_row,
    change_lines,
    route_chips,
    via_text,
)
from paperroute_transit.core.model import Change, Pin, Way
from paperroute_transit.core.names import NameRules
from paperroute_transit.core.rides import Ride
from paperroute_transit.core.stop_view import StopView, empty_text
from tests.core.test_chains import (
    C0750,
    CAMP_PIN,
    FIRST,
    FROM_CAMPBELLTOWN,
    NO_665,
    SECOND,
    THIRD,
    THREE_OR_TWO,
    TRAINS,
    TWO_PIN,
    camp_offers,
    express_plan,
    late_first,
    lived,
    three_then_two,
)
from tests.core.test_chains import work_chains as chains_from

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
B0801A: Final = run(departs=(8, 1), arrives=(8, 12), route='664',
                    trip_id='b0801a')
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
    assert view.empty == (
        'No connections to Westmead Station in the next 3 hours'
    )
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
    assert view.empty == (
        'No connections to Westmead Station in the next 3 hours'
    )


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


def test_a_chain_is_uncertain_when_any_leg_is() -> None:
    """One uncertain leg makes the row and the hero uncertain."""
    legs = TWO_CHANGES.legs
    unsure = replace(
        TWO_CHANGES,
        legs=(legs[0], replace(legs[1], uncertain=True), legs[2]),
    )
    now = at(7, 0)
    assert chain_row(
        chain=unsure,
        pin=TWO,
        now=now,
        zone=SYDNEY_ZONE,
    ).uncertain
    assert chain_hero(
        chain=unsure, pin=TWO, now=now, zone=SYDNEY_ZONE, city=SYDNEY,
    ).uncertain
    assert not chain_row(
        chain=TWO_CHANGES, pin=TWO, now=now, zone=SYDNEY_ZONE,
    ).uncertain

RULES: Final[NameRules] = NameRules(centres=('Here',))


def test_build_connection_view_carries_the_name_rules() -> None:
    """The Connection view copies the Stop view's name rules."""
    result = build_connection_view(
        stop_view=replace(STOP, name_rules=RULES),
        pin=WORK,
        chains=CHAINS,
        scroll=0,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert result.name_rules == RULES


def hero_of(chain: Chain) -> ChainHero:
    """Work's hero for a chain at 7:30."""
    return chain_hero(
        chain=chain, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE, city=SYDNEY,
    )


def folded_chain() -> Chain:
    """The late 7:33 whose missed change folded into a stay on."""
    through = run(
        departs=(7, 45), arrives=(7, 58), route='606', trip_id='a0733',
    )
    second = (live(item=through), *lived(LEG2[:1]))
    on_time = chains_from(first=lived(LEG1[:1]), second=second)
    kept = offers_after(offers=Offers(), pin=WORK, chains=on_time).kept
    late = (live(item=through, late=6), *lived(LEG2[:1]))
    chains = chains_from(first=late_first(6), second=late, kept=kept)
    return chains[0]


def test_easy_hero_marks_both_buses() -> None:
    hero = hero_of(Chain(
        legs=(live(item=A0733, late=4), live(item=B0746, late=2)), way=0,
    ))
    assert hero.mark == '+4'
    assert [line.mark for line in hero.lines] == ['', '', '', '+2']
    assert hero.lines[1] == HeroLine(
        route=None, noun=None,
        text='change at Constitution Hill, North West Twy 7:48',
    )
    assert hero.lines[3].at == '7:48'


def test_tight_hero_names_the_change_in_bold() -> None:
    hero = hero_of(Chain(
        legs=(live(item=A0733, late=4), live(item=B0746)), way=0,
    ))
    assert hero.lines[1] == HeroLine(
        route=None, noun=None,
        text='tight change at Constitution Hill, North West Twy',
        at='7:46', bold=True,
    )
    assert hero.lines[2].text == 'walk 150 m'


def test_missed_hero_names_the_next_bus() -> None:
    hero = hero_of(Chain(
        legs=(live(item=A0733, late=6), live(item=B0801A)), way=0,
        missed=Missed(leg=1, arrives=at(7, 57), later=True),
    ))
    assert hero.lines[1] == HeroLine(
        route=None, noun=None, text='change missed · next 664 at 8:01',
        bold=True,
    )
    assert (hero.lines[3].route, hero.lines[3].at) == ('664', '8:01')
    assert hero.arrives_at == '8:12'


def test_stranded_hero_says_change_missed_and_no_arrival() -> None:
    hero = hero_of(Chain(
        legs=(live(item=A0733, late=6), live(item=B0746)), way=0,
        missed=Missed(leg=1, arrives=at(7, 57), later=False),
    ))
    assert hero.lines[1] == HeroLine(
        route=None, noun=None, text='change missed', bold=True,
    )
    assert hero.lines[3].route == '665'
    assert hero.arrives_at == ''


def test_missed_stay_on_hero_says_so_in_bold() -> None:
    hero = hero_of(folded_chain())
    assert [line.text for line in hero.lines][-1] == 'change missed · stay on'
    assert hero.lines[-1].bold
    assert not any(line.text.startswith('stay on') for line in hero.lines)


def test_rows_carry_the_mark_the_word_and_the_struck_arrival() -> None:
    def row(chain: Chain) -> PairRow:
        return chain_row(
            chain=chain, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE,
        )

    tight = row(Chain(
        legs=(live(item=A0733, late=4), live(item=B0746)), way=0,
    ))
    assert (tight.mark, tight.tight, tight.was) == ('+4', True, '')
    missed = row(Chain(
        legs=(live(item=A0733, late=6), live(item=B0801A)), way=0,
        missed=Missed(leg=1, arrives=at(7, 57), later=True),
    ))
    assert (missed.tight, missed.was, missed.arrives_at) == (
        False, '7:57', '8:12',
    )
    stranded = row(Chain(
        legs=(live(item=A0733, late=6), live(item=B0746)), way=0,
        missed=Missed(leg=1, arrives=at(7, 57), later=False),
    ))
    assert (stranded.was, stranded.arrives_at) == ('7:57', '')
    plain = row(CHAINS[0])
    assert (plain.mark, plain.tight, plain.was) == ('', False, '')


def test_missed_stay_on_row_keeps_stay_on_and_strikes_old_arrival() -> None:
    row = chain_row(
        chain=folded_chain(), pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE,
    )
    assert (row.detail, row.was, row.tight) == ('stay on', '7:57', False)


def test_row_has_no_struck_arrival_when_the_first_trip_is_unlisted() -> None:
    row = chain_row(
        chain=Chain(
            legs=(live(item=A0733, late=6), live(item=B0801A)), way=0,
            missed=Missed(leg=1, arrives=None, later=True),
        ),
        pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE,
    )
    assert row.was == ''


def test_hero_of_a_shorter_fresh_plan_names_its_first_new_bus() -> None:
    hero = chain_hero(
        chain=three_then_two(),
        pin=THREE_OR_TWO,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert hero.lines[1] == HeroLine(
        route=None, noun=None, text='change missed · next T-Way at 7:50',
        bold=True,
    )
    assert hero.lines[-1].route == 'T-Way'


def test_hero_of_a_fresh_plan_on_another_way_names_its_first_new_bus() -> None:
    chains = chains_of(
        pin=CAMP_PIN,
        lists={
            FIRST: late_first(6),
            SECOND: NO_665,
            FROM_CAMPBELLTOWN: lived((C0750,)),
        },
        direct=(),
        now=at(7, 30),
        kept=camp_offers((C0750,)),
    )
    assert chains[0].way == 0
    hero = chain_hero(
        chain=chains[0],
        pin=CAMP_PIN,
        now=at(7, 30),
        zone=SYDNEY_ZONE,
        city=SYDNEY,
    )
    assert hero.lines[1] == HeroLine(
        route=None, noun=None, text='change missed · next 664 at 8:01',
        bold=True,
    )
    assert (hero.lines[-1].route, hero.lines[-1].at) == ('664', '8:01')


MISSED_DIRECT: Final[Chain] = replace(
    DIRECT, missed=Missed(leg=0, arrives=at(7, 57), later=True),
)


def test_missed_direct_hero_says_so_in_bold() -> None:
    hero = hero_of(MISSED_DIRECT)
    assert hero.lines[1:] == (
        HeroLine(
            route=None, noun=None, text='change missed · stay on', bold=True,
        ),
    )


def test_missed_direct_row_keeps_direct_and_strikes_the_old_arrival() -> None:
    row = chain_row(
        chain=MISSED_DIRECT, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE,
    )
    assert (row.detail, row.was, row.arrives_at, row.tight) == (
        'direct', '7:57', '8:05', False,
    )


def test_direct_trip_without_a_miss_is_unchanged() -> None:
    assert hero_of(DIRECT) == ChainHero(
        countdown='10',
        leaves_at='7:40',
        lines=(
            HeroLine(
                route='609', noun='bus', text='Parramatta Station', at='7:40',
            ),
            HeroLine(
                route=None, noun=None, text='direct to Westmead Station',
            ),
        ),
        arrives_at='8:05',
        live=False,
        uncertain=False,
        mark='',
    )
    assert chain_row(
        chain=DIRECT, pin=WORK, now=at(7, 30), zone=SYDNEY_ZONE,
    ) == PairRow(
        leaves_at='7:40',
        chips=('609',),
        detail='direct',
        arrives_at='8:05',
        minutes=25,
        countdown='10',
        live=False,
        uncertain=False,
        mark='',
        tight=False,
        was='',
    )


def stay_on_second_change_chain() -> Chain:
    """The 665 seven late: the fresh plan stays on it past Westmead."""
    through = run(
        departs=(7, 57), arrives=(8, 10), route='665', trip_id='b0746',
    )
    lists = {
        FIRST: lived(LEG1[:1]),
        SECOND: lived(LEG2[:1]),
        THIRD: (live(item=through), *lived(TRAINS)),
    }
    on_time = chains_of(pin=TWO_PIN, lists=lists, direct=(), now=at(7, 30))
    kept = offers_after(offers=Offers(), pin=TWO_PIN, chains=on_time).kept
    late = {
        **lists,
        SECOND: (live(item=LEG2[0], late=7),),
        THIRD: (live(item=through, late=7), *lived(TRAINS)),
    }
    return chains_of(
        pin=TWO_PIN, lists=late, direct=(), now=at(7, 30), kept=kept,
    )[0]


def test_missed_second_change_folded_into_a_stay_on_says_so() -> None:
    chain = stay_on_second_change_chain()
    assert chain.missed == Missed(leg=2, arrives=at(8, 8), later=True)
    hero = chain_hero(
        chain=chain, pin=TWO_PIN, now=at(7, 30), zone=SYDNEY_ZONE, city=SYDNEY,
    )
    texts = [line.text for line in hero.lines]
    assert texts.count('change missed · stay on') == 1
    line = hero.lines[texts.index('change missed · stay on')]
    assert line.bold
    assert hero.lines[texts.index('change missed · stay on') - 1].mark == '+7'
    row = chain_row(
        chain=chain, pin=TWO_PIN, now=at(7, 30), zone=SYDNEY_ZONE,
    )
    assert row.was == '8:08'


def test_fresh_plan_after_a_missed_change_shows_its_arrival_unstruck() -> None:
    """The hero names the next bus; the row strikes nothing."""
    chain = express_plan()
    hero = chain_hero(
        chain=chain, pin=TWO_PIN, now=at(7, 30), zone=SYDNEY_ZONE, city=SYDNEY,
    )
    assert hero.lines[1].text == 'change missed · next 665 at 7:48'
    assert hero.arrives_at == '8:08'
    row = chain_row(
        chain=chain, pin=TWO_PIN, now=at(7, 30), zone=SYDNEY_ZONE,
    )
    assert (row.arrives_at, row.was) == ('8:08', '')
