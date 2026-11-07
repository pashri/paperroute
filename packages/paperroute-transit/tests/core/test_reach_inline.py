"""Tests for putting skipped stations back inline on a line."""

import pytest

from paperroute_testkit.core.reach_factories import ids, reach_of, tail
from paperroute_transit.core.reach_inline import inlined


def test_skipped_stations_that_rejoin_go_inline_in_order() -> None:
    """Stations an express skips sit on the line, in travel order."""
    reach = reach_of(tails=[tail('A K Z', 10), tail('A P Q K Z', 4)])
    assert ids(reach=reach) == ['C', 'A', 'P', 'Q', 'K', 'Z']
    assert [row.depth for row in reach.rows] == [0] * 6
    assert [row.parent for row in reach.rows] == [None, 0, 1, 2, 3, 4]


@pytest.mark.parametrize(('stopping', 'depth'), [(2, 0), (1, 1)])
def test_stopping_trains_need_a_quarter_of_the_express(
    stopping: int, depth: int,
) -> None:
    """Under a quarter of the express link's trips leaves a spur."""
    reach = reach_of(tails=[tail('A K Z', 8), tail('A P K Z', stopping)])
    assert reach.rows[ids(reach=reach).index('P')].depth == depth


@pytest.mark.parametrize(('skipped', 'depth'), [(6, 0), (7, 1)])
def test_at_most_six_skipped_stations_go_inline(
    skipped: int, depth: int,
) -> None:
    """A longer stopping run than six stations stays a spur."""
    stops = ' '.join(f'P{k}' for k in range(skipped))
    reach = reach_of(tails=[tail('A K', 10), tail(f'A {stops} K', 5)])
    assert reach.rows[ids(reach=reach).index('P0')].depth == depth


def test_branch_that_never_rejoins_stays_a_spur() -> None:
    """A run that leaves the line and never comes back hangs as a spur."""
    reach = reach_of(tails=[tail('A K', 10), tail('A R1 R2', 5)])
    assert reach.rows[ids(reach=reach).index('R1')].depth == 1


def test_run_rejoining_a_later_station_stays_a_spur() -> None:
    """Only a run back to the next main-line station goes inline."""
    reach = reach_of(tails=[tail('A B K', 10), tail('A D K', 5)])
    assert reach.rows[ids(reach=reach).index('D')].depth == 1


def test_inlined_takes_the_busiest_detour() -> None:
    """Between two ways round, the one with more trips goes inline."""
    succ = {
        'A': {'K': 8, 'P': 3, 'Q': 4},
        'P': {'K': 3},
        'Q': {'K': 4},
    }
    placed = {'A', 'K'}
    assert inlined(succ=succ, chain=('A', 'K'), placed=placed) == (
        'A', 'Q', 'K',
    )
    assert placed == {'A', 'K', 'Q'}


def test_nested_detours_insert_at_most_six_stations_per_link() -> None:
    """Ways round inside ways round never add more than six stations."""
    detour = [f'Q{k}' for k in range(6)]
    succ = {
        'A': {'K': 10, 'P': 6, detour[0]: 5},
        'P': {'K': 6},
        **{
            here: {there: 5}
            for here, there in zip(detour, [*detour[1:], 'P'])
        },
    }
    assert inlined(succ=succ, chain=('A', 'K'), placed={'A', 'K'}) == (
        'A', 'P', 'K',
    )
    shorter = {**succ, detour[4]: {'P': 5}}
    assert inlined(succ=shorter, chain=('A', 'K'), placed={'A', 'K'}) == (
        'A', *detour[:5], 'P', 'K',
    )
