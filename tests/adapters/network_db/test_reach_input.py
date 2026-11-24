"""Tests for network db: reach input."""

from pathlib import Path

from paperroute_sydney.city import SydneyMode
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.reach import RunTail, StripKey
from tests.adapters.network_db.helpers import (
    merge_e2_into_r3,
    place,
    set_runs,
    tails_from,
)
from tests.helpers import schema_three


def test_reach_input_for_train_takes_every_train_run(
    network_db: Path,
) -> None:
    """A Train strip takes the runs of every train route."""
    tails = tails_from(path=network_db, station='C')
    assert sum(tail.trips for tail in tails) == 75
    assert ('L1', 'L2', 'J') in {tail.after for tail in tails}


def test_reach_input_for_bus_filters_by_route(network_db: Path) -> None:
    """A Bus strip takes only its own route's runs."""
    tails = tails_from(
        path=network_db, station='B2', kind=SydneyMode.BUS, routes=('811',),
    )
    assert tails == (RunTail(before=(), after=('B3',), trips=3),)


def test_reach_input_for_bus_with_no_routes_takes_every_bus_route(
    network_db: Path,
) -> None:
    """A Bus strip with no routes chosen takes every bus route's runs."""
    tails = tails_from(path=network_db, station='B2', kind=SydneyMode.BUS)
    assert set(tails) == {
        RunTail(before=(), after=('B3',), trips=3),
        RunTail(before=('B1',), after=(), trips=3),
    }


def test_reach_input_with_no_kind_takes_every_run(network_db: Path) -> None:
    """A strip with no kind takes buses and trains alike."""
    tails = tails_from(path=network_db, station='B2', kind=None)
    assert {tail.after for tail in tails} == {('B3',), ()}


def test_reach_input_maps_a_platform_to_its_station(
    network: SqliteNetworkRepository,
) -> None:
    """A platform From reads the runs of its station."""
    found = network.reach_input(
        key=StripKey(station='W1_1', kind=SydneyMode.TRAIN, routes=()),
        origin=place(stop_id='W1_1'),
    )
    assert found is not None
    assert found.origin.stop_id == 'W1'
    assert found.origin.name == 'W1 Stop'
    assert found.station_of['W1_1'] == 'W1'
    assert found.station_of['W1'] == 'W1'
    assert found.station_of['E2_1'] == 'E2'
    assert found.places['E2'].name == 'E2 Stop'
    assert found.tails


def test_reach_input_cuts_each_tail_at_the_turn_back(
    network_db: Path,
) -> None:
    """y1 runs on as y2 back to J; its tail stops at W2."""
    tails = tails_from(path=network_db, station='C')
    afters = {tail.after for tail in tails}
    assert ('J', 'W1', 'W2') in afters
    assert all(len(set(after)) == len(after) for after in afters)
    assert all('C' not in after for after in afters)


def test_reach_past_a_city_circle_loop(copy: Path) -> None:
    """A loop run gives a tail per pass, cut before its next revisit."""
    set_runs(path=copy, runs=[('C L1 L2 J C E1 E2', 6)])
    assert tails_from(path=copy, station='J') == (
        RunTail(before=('C', 'L1', 'L2'), after=('C', 'E1', 'E2'), trips=6),
    )
    assert set(tails_from(path=copy, station='C')) == {
        RunTail(before=(), after=('L1', 'L2', 'J'), trips=6),
        RunTail(before=('C', 'L1', 'L2', 'J'), after=('E1', 'E2'), trips=6),
    }


def test_line_strip_cuts_tails_off_the_line(
    network_db: Path, copy: Path,
) -> None:
    """A T1 strip at C cuts each tail before T2's L1 and L2."""
    tails = tails_from(path=network_db, station='C', routes=('T1',))
    assert sum(tail.trips for tail in tails) == 75
    assert not {'L1', 'L2'} & {
        station for tail in tails for station in tail.before + tail.after
    }
    set_runs(path=copy, runs=[('C L1 L2 J C E1 E2', 6)])
    assert set(tails_from(path=copy, station='C', routes=('T1',))) == {
        RunTail(before=(), after=(), trips=6),
        RunTail(before=('J',), after=('E1', 'E2'), trips=6),
    }


def test_line_strip_keeps_express_runs_whole(copy: Path) -> None:
    """Runs skipping stations of the line keep every station they call at."""
    set_runs(path=copy, runs=[('E2 C W1', 4), ('R3 R1 J C E2', 2)])
    assert set(tails_from(path=copy, station='C', routes=('T1',))) == {
        RunTail(before=('E2',), after=('W1',), trips=4),
        RunTail(before=('R3', 'R1', 'J'), after=('E2',), trips=2),
    }


def test_line_strip_cuts_at_the_first_station_off_the_line(
    copy: Path,
) -> None:
    """Each tail stops before its first station off the line, outward."""
    set_runs(path=copy, runs=[('W2 L2 J C E1 L1 E2', 3)])
    assert tails_from(path=copy, station='C', routes=('T1',)) == (
        RunTail(before=('J',), after=('E1',), trips=3),
    )


def test_line_strip_counts_an_alias_as_its_kept_station(copy: Path) -> None:
    """A station merged into one on the line counts as on the line."""
    merge_e2_into_r3(path=copy)
    set_runs(path=copy, runs=[('W1 J C E1 E2', 3)])
    assert tails_from(path=copy, station='C', routes=('T1',)) == (
        RunTail(before=('W1', 'J'), after=('E1', 'E2'), trips=3),
    )


def test_line_strip_without_line_stations_is_uncut(copy: Path) -> None:
    """A schema-3 file leaves a T1 strip's tails whole."""
    schema_three(path=copy)
    tails = tails_from(path=copy, station='C', routes=('T1',))
    assert ('L1', 'L2', 'J') in {tail.after for tail in tails}
    set_runs(path=copy, runs=[('C L1 L2 J C E1 E2', 6)])
    assert set(tails_from(path=copy, station='C', routes=('T1',))) == {
        RunTail(before=(), after=('L1', 'L2', 'J'), trips=6),
        RunTail(before=('C', 'L1', 'L2', 'J'), after=('E1', 'E2'), trips=6),
    }
