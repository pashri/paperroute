"""Tests for a train kind's hops and weekday runs."""

from paperroute_transit.core.train_network import (
    OUTSIDE,
    Hop,
    TrainNetwork,
    expanded,
    from_origin,
    hop_of,
)


def network_of(*, hops: dict[tuple[str, str], Hop]) -> TrainNetwork:
    """
    Build a network with some hops and no runs.

    Parameters
    ----------
    hops : dict[tuple[str, str], Hop]
        Hops by their two stations.

    Returns
    -------
    TrainNetwork
        The network.
    """
    return TrainNetwork(hops=hops, runs=())


def test_from_origin_cuts_a_loop() -> None:
    """A run that comes back to the origin is cut there."""
    found = from_origin(stations=['C', 'T', 'W', 'C', 'R'], origin='C')
    assert found == (('T', 'W'), True)


def test_from_origin_keeps_a_run_that_does_not_loop() -> None:
    """A run with no repeat of the origin is kept whole."""
    found = from_origin(stations=['A', 'C', 'T', 'W'], origin='C')
    assert found == (('T', 'W'), False)


def test_from_origin_cuts_at_another_repeat() -> None:
    """A repeat of another station cuts the run, not on the origin."""
    found = from_origin(stations=['C', 'T', 'W', 'T'], origin='C')
    assert found == (('T', 'W'), False)


def test_from_origin_of_a_run_that_misses_the_origin() -> None:
    """A run that never calls at the origin gives nothing."""
    assert from_origin(stations=['A', 'B'], origin='C') == ((), False)


def test_expanded_inserts_passed_stations() -> None:
    """Each hop's passed stations sit between its two stations."""
    network = network_of(hops={
        ('A', 'B'): Hop(passed=('p', 'q'), inside=True),
        ('B', 'C'): Hop(passed=(), inside=True),
    })
    found = expanded(network=network, stations=['A', 'B', 'C'])
    assert found == (
        ('A', False), ('p', True), ('q', True), ('B', False), ('C', False),
    )


def test_expanded_of_no_stations_is_empty() -> None:
    """Nothing in, nothing out."""
    assert expanded(network=network_of(hops={}), stations=[]) == ()


def test_a_missing_hop_is_outside() -> None:
    """A pair with no stored hop reads as outside the track."""
    network = network_of(hops={})
    assert hop_of(network=network, a='A', b='B') is OUTSIDE
    assert OUTSIDE == Hop(passed=(), inside=False)
