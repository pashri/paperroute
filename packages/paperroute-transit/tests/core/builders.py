"""Builders shared by the core tests."""

from collections.abc import Sequence
from datetime import date

from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import (
    EIGHT,
    STALE_LIMIT,
    START,
    TEN_MINUTES,
)
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.strip_times import TripInputs

DIAGONAL = (
    PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
    PatternStop(
        arrives=EIGHT + TEN_MINUTES,
        departs=EIGHT + TEN_MINUTES,
        point=(1000.0, 1000.0),
    ),
)


def trip_inputs(
    *,
    departures: tuple[LiveDeparture, ...],
    patterns: dict[str, TripPattern | None],
) -> TripInputs:
    """
    Make trip inputs at 8:00 with no positions, stations or line kinds.

    Parameters
    ----------
    departures : tuple[LiveDeparture, ...]
        The departures, in order.
    patterns : dict[str, TripPattern | None]
        Loaded patterns by trip ID.

    Returns
    -------
    TripInputs
        The inputs.
    """
    return TripInputs(
        departures=departures,
        key=None,
        patterns=patterns,
        positions={},
        now=START,
        stale_after=STALE_LIMIT,
        zone=city_under_test().zone,
        station_of={},
        line_kinds=frozenset(),
    )


def named_pattern(
    *, route: str, names: Sequence[str], days: tuple[date, ...],
) -> TripPattern:
    """
    Make a pattern whose stops have only a station ID and a name.

    Parameters
    ----------
    route : str
        The route name.
    names : Sequence[str]
        Stop names in running order.
    days : tuple[date, ...]
        The days the pattern runs.

    Returns
    -------
    TripPattern
        The pattern; stop ``n`` has station ID ``S{n}``.
    """
    return TripPattern(
        trip_id='t',
        route=route,
        source='s',
        shape_id=None,
        stops=tuple(
            PatternStop(
                arrives=0,
                departs=0,
                point=None,
                station=f'S{index}',
                name=name,
                passes=False,
            )
            for index, name in enumerate(names)
        ),
        days=days,
    )
