"""Tests for the ways trains leave a train's line."""

from paperroute_transit.core.train_exits import (
    EXIT_TRAINS,
    Tail,
    TrainExit,
    named_ends,
    train_exits,
    walk_order,
)
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun

LINE = ('C', 'S', 'R', 'K')


def run_of(*, route: str, stations: str, trains: int) -> WeekdayRun:
    """
    Make a weekday run from space-separated station IDs.

    Parameters
    ----------
    route : str
        Route short name.
    stations : str
        Station IDs called at, separated by spaces.
    trains : int
        Regular trains a weekday.

    Returns
    -------
    WeekdayRun
        The run.
    """
    return WeekdayRun(
        route=route, stations=tuple(stations.split()), trains=trains,
    )


def exits_of(
    *,
    runs: list[WeekdayRun],
    line: tuple[str, ...] = LINE,
    first_hops: frozenset[str] | None = None,
    loop_name: str = '',
    hops: dict[tuple[str, str], Hop] | None = None,
) -> tuple[TrainExit, ...]:
    """
    Work out the exits of runs from C.

    Parameters
    ----------
    runs : list[WeekdayRun]
        The runs through C.
    line : tuple[str, ...]
        The main line, with the From first.
    first_hops : frozenset[str] | None
        The main train's side.
    loop_name : str
        The name of a train that returns to C.
    hops : dict[tuple[str, str], Hop] | None
        Hops by their two stations.

    Returns
    -------
    tuple[TrainExit, ...]
        The exits.
    """
    return train_exits(
        network=TrainNetwork(hops=hops or {}, runs=tuple(runs)),
        origin='C',
        line=line,
        first_hops=first_hops,
        loop_name=loop_name,
    )


def junction_runs() -> list[WeekdayRun]:
    """
    Make the runs of a junction at R.

    Returns
    -------
    list[WeekdayRun]
        Six GA trains end at N1, two more GA trains run on to N2 with
        eighteen GB trains.
    """
    return [
        run_of(route='GA', stations='C S R N1', trains=6),
        run_of(route='GA', stations='C S R N1 N2', trains=2),
        run_of(route='GB', stations='C S R N1 N2', trains=18),
    ]


def test_an_exit_hangs_at_the_last_shared_station() -> None:
    """An exit toward ST hangs at S, with its end named."""
    found = exits_of(runs=[
        run_of(route='X', stations='C S ST', trains=8),
        run_of(route='X', stations='C S R K', trains=30),
    ])
    assert found == (
        TrainExit(junction='S', first='ST', names=('ST',), trains=8),
    )


def test_an_exit_at_the_from_hangs_at_the_from() -> None:
    """A way off the line at its first station hangs at the From."""
    found = exits_of(runs=[run_of(route='X', stations='C I', trains=10)])
    assert [(each.junction, each.first) for each in found] == [('C', 'I')]


def test_fewer_than_six_trains_earn_no_row() -> None:
    """A branch with five trains gets no exit."""
    assert EXIT_TRAINS == 6
    assert not exits_of(runs=[run_of(route='X', stations='C S ST', trains=5)])


def test_an_end_of_its_own_line_is_named() -> None:
    """N1 is named: six of 26 end there, but GA runs on past it rarely."""
    found = exits_of(runs=junction_runs())
    assert [(each.junction, each.trains) for each in found] == [('R', 26)]
    assert 'N1' in found[0].names


def test_a_thirty_percent_end_is_named() -> None:
    """Eight of 20 end at E, and no route ends there with few onward."""
    found = exits_of(runs=[
        run_of(route='A', stations='C S E', trains=8),
        run_of(route='A', stations='C S E F', trains=12),
    ])
    assert 'E' in found[0].names


def test_an_end_under_thirty_percent_is_not_named_by_share() -> None:
    """With six of 21 ending and the route running on, E gets no name."""
    runs = [
        run_of(route='A', stations='C S E', trains=6),
        run_of(route='A', stations='C S E F', trains=15),
    ]
    assert named_ends(
        tails=[
            Tail(route='A', stations=('E',), trains=6, loop=False),
            Tail(route='A', stations=('E', 'F'), trains=15, loop=False),
        ],
        runs=runs,
    ) == frozenset({'F'})


def test_the_busiest_end_names_an_exit_when_none_qualifies() -> None:
    """Ends of four and three trains give the exit the busier end."""
    found = exits_of(runs=[
        run_of(route='A', stations='C S X E1', trains=4),
        run_of(route='A', stations='C S X E2', trains=3),
    ])
    assert found[0].names == ('E1',)


def test_names_follow_the_busiest_branch_first() -> None:
    """A fork beyond the junction lists the branch of eight first."""
    found = exits_of(runs=[
        run_of(route='A', stations='C S R X N1', trains=6),
        run_of(route='B', stations='C S R X N2', trains=8),
    ])
    assert found[0].names == ('N2', 'N1')
    assert found[0].first == 'X'


def test_a_named_end_reached_through_another_is_in_walk_order() -> None:
    """N1 comes before N2 when the trains to N2 pass N1."""
    found = exits_of(runs=junction_runs())
    assert found[0].names == ('N1', 'N2')


def test_walk_order_visits_every_branch_without_recursion() -> None:
    """A long chain of tails is walked in order, busiest first."""
    chain = tuple(f's{index}' for index in range(5000))
    tails = [
        Tail(route='A', stations=(*chain, 'a'), trains=7, loop=False),
        Tail(route='B', stations=(*chain, 'b'), trains=9, loop=False),
    ]
    assert walk_order(tails=tails, named=frozenset({'a', 'b'})) == (
        'b', 'a',
    )


def test_exits_beyond_the_destination_are_kept() -> None:
    """An exit at the last station of the line is kept."""
    found = exits_of(runs=[run_of(route='X', stations='C S R K Z', trains=9)])
    assert [each.junction for each in found] == ['K']


def test_a_one_day_special_earns_nothing() -> None:
    """A run with no regular weekday trains adds nothing."""
    assert not exits_of(runs=[run_of(route='X', stations='C S ST', trains=0)])


def test_a_city_circle_tail_is_named_by_the_city() -> None:
    """A tail returning to the From ends at the loop's name."""
    found = exits_of(
        runs=[run_of(route='X', stations='C S T1 T2 C', trains=12)],
        loop_name='City Circle',
    )
    assert found == (
        TrainExit(
            junction='S', first='T1', names=('City Circle',), trains=12,
        ),
    )


def test_a_loop_tail_with_no_name_is_dropped() -> None:
    """With no loop name, a returning tail gives no exit."""
    assert not exits_of(
        runs=[run_of(route='X', stations='C S T1 T2 C', trains=12)],
    )


def test_the_reverse_loop_gets_no_exit() -> None:
    """A loop starting the other way from the line's second gets none."""
    assert not exits_of(
        runs=[run_of(route='X', stations='C T2 T1 C', trains=12)],
        loop_name='City Circle',
    )


def test_a_run_not_calling_at_the_from_is_ignored() -> None:
    """A run that misses C gives no exit."""
    assert not exits_of(runs=[run_of(route='X', stations='Q S ST', trains=9)])


def test_first_hops_keep_the_main_trains_side() -> None:
    """Tails whose first station is not in the set are dropped."""
    runs = [
        run_of(route='X', stations='C S ST', trains=8),
        run_of(route='X', stations='C Q QT', trains=8),
    ]
    found = exits_of(runs=runs, first_hops=frozenset({'Q'}))
    assert [each.first for each in found] == ['Q']


def test_groups_whose_via_sets_meet_are_merged() -> None:
    """Two tails leaving at one junction and meeting at M are one exit."""
    found = exits_of(runs=[
        run_of(route='A', stations='C S P M N1', trains=4),
        run_of(route='B', stations='C S Q M N1', trains=4),
    ])
    assert [(each.junction, each.trains) for each in found] == [('S', 8)]
    assert found[0].names == ('N1',)


def test_groups_with_apart_via_sets_stay_apart_busiest_first() -> None:
    """Exits at one junction go busiest first."""
    found = exits_of(runs=[
        run_of(route='A', stations='C S P', trains=7),
        run_of(route='B', stations='C S Q', trains=9),
    ])
    assert [each.first for each in found] == ['Q', 'P']


def test_a_tail_that_rejoins_the_line_hangs_at_its_last_shared() -> None:
    """A tail leaving the line and rejoining at R hangs at R."""
    found = exits_of(runs=[
        run_of(route='A', stations='C S X R Y', trains=8),
    ])
    assert [(each.junction, each.first) for each in found] == [('R', 'Y')]


def test_a_tail_ending_on_the_line_has_no_exit() -> None:
    """A tail whose last station is on the line gives none."""
    assert not exits_of(runs=[run_of(route='A', stations='C S X R', trains=8)])


def test_passed_stations_are_part_of_the_tail() -> None:
    """The first station of an exit can be one passed on the way."""
    found = exits_of(
        runs=[run_of(route='A', stations='C S T', trains=8)],
        hops={('S', 'T'): Hop(passed=('P',), inside=True)},
    )
    assert found[0].first == 'P'
