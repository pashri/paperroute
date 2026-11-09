"""Reaches built from short station IDs."""

from collections.abc import Mapping, Sequence
from typing import Final

from hypothesis import strategies as st

from paperroute_transit.core.geo import Point
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RunTail,
    build_reach,
)
from paperroute_transit.core.reach_layout import LineExit

STATIONS: Final[tuple[str, ...]] = tuple(f'S{k}' for k in range(12))


def tail(
    after: str, trips: int, before: str = '',
) -> RunTail:
    """
    Make a tail from space-separated station IDs.

    Parameters
    ----------
    after : str
        Stations after the From.
    trips : int
        Trip-days.
    before : str
        Stations before the From.

    Returns
    -------
    RunTail
        The tail.
    """
    return RunTail(
        before=tuple(before.split()), after=tuple(after.split()), trips=trips,
    )


def reach_of(
    *,
    tails: Sequence[RunTail],
    points: Mapping[str, Point] | None = None,
    names: Mapping[str, str] | None = None,
    exits: Sequence[LineExit] = (),
) -> Reach:
    """
    Build the reach from C, naming each station by its ID.

    Parameters
    ----------
    tails : Sequence[RunTail]
        Tails through C.
    points : Mapping[str, Point] | None
        Station positions in metres.
    names : Mapping[str, str] | None
        Station names other than their IDs.
    exits : Sequence[LineExit]
        Ways off the line; their off stations are added to the places.

    Returns
    -------
    Reach
        The reach.
    """
    keys = {'C', *(each.off for each in exits)}.union(
        *(t.before + t.after for t in tails),
    )
    places = {
        key: StopPlace(
            stop_id=key,
            name=(names or {}).get(key, key),
            point=(points or {}).get(key),
        )
        for key in keys
    }
    return build_reach(data=ReachInput(
        origin=places['C'], tails=tuple(tails), places=places,
        station_of={key: key for key in keys},
        exits=tuple(exits),
    ))


def ids(*, reach: Reach) -> list[str]:
    """
    List a reach's station IDs from top to bottom.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    list[str]
        Station IDs.
    """
    return [row.place.stop_id for row in reach.rows]


@st.composite
def random_tails(draw: st.DrawFn) -> list[RunTail]:
    """
    Draw tails over 12 stations, each calling at a station once.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    list[RunTail]
        Tails from C.
    """
    stations = st.sampled_from(STATIONS)
    return draw(st.lists(
        st.builds(
            RunTail,
            before=st.lists(
                st.one_of(stations, st.just('C')), max_size=4,
            ).map(tuple),
            after=st.lists(stations, unique=True, max_size=8).map(tuple),
            trips=st.integers(min_value=1, max_value=9),
        ),
        max_size=8,
    ))
