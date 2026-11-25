"""Hang buses on roads with no strip rows off a hook named for the road."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.reach import ReachInput
from paperroute_transit.core.reach_layout import LineExit
from paperroute_transit.core.strip_approach import Approach
from paperroute_transit.core.strip_badges import Badge, Spot

HOOK_KEY: Final[str] = 'road:'  # prefix of a road hook's row ID
SLOT: Final[float] = 18.0  # pixels either side of a hook's middle
INWARD: Final[float] = 270.0  # wedge bearing along a hook to the track


def hook_station(*, road: str) -> str:
    """
    Name the row of a road's hook.

    Parameters
    ----------
    road : str
        The road.

    Returns
    -------
    str
        An ID no timetable station has.
    """
    return f'{HOOK_KEY}{road}'


def is_road_hook(*, station: str) -> bool:
    """
    Say whether a row is a road's hook.

    Parameters
    ----------
    station : str
        The row's station ID.

    Returns
    -------
    bool
        True for a row made by `hook_station`.
    """
    return station.startswith(HOOK_KEY)


def road_hooks(
    *, data: ReachInput, roads: Mapping[str, tuple[str, ...]],
) -> dict[str, str]:
    """
    Pick the places before the From whose buses have no row to sit by.

    Parameters
    ----------
    data : ReachInput
        The strip's runs and the stations facing its stops.
    roads : Mapping[str, tuple[str, ...]]
        The roads buses arrive at the From's parent by, by the place
        called at just before it.

    Returns
    -------
    dict[str, str]
        The hook row by place, for each place that is not a row on the
        strip and whose stop across the road is not one either; its hook
        is named for the place's first road.
    """
    rows = {
        station
        for tail in (*data.tails, *data.across_tails)
        for station in tail.after
    } | {data.origin.stop_id}
    return {
        place: hook_station(road=found[0])
        for place, found in sorted(roads.items())
        if found and place not in rows and data.facing.get(place) not in rows
    }


def with_road_hooks(
    *, data: ReachInput, roads: Mapping[str, tuple[str, ...]],
) -> ReachInput:
    """
    Add a hook below the From for each road with no strip rows.

    Parameters
    ----------
    data : ReachInput
        The strip's runs and the stations facing its stops.
    roads : Mapping[str, tuple[str, ...]]
        The roads buses arrive by (see `road_hooks`).

    Returns
    -------
    ReachInput
        The runs with one exit off the From per hooked road, named for
        the road with no route numbers, and the hooks by place.
    """
    hooks = road_hooks(data=data, roads=roads)
    rows = sorted(set(hooks.values()))
    return replace(
        data,
        exits=(
            *data.exits,
            *(
                LineExit(
                    junction=data.origin.stop_id, off=row, lines=(), trips=0,
                )
                for row in rows
            ),
        ),
        places={
            **data.places,
            **{
                row: StopPlace(
                    stop_id=row, name=row.removeprefix(HOOK_KEY), point=None,
                )
                for row in rows
            },
        },
        hooks=hooks,
    )


def hook_badge(
    *,
    pattern: TripPattern,
    calls: tuple[int, int],
    station_of: Mapping[str, str],
    approach: Approach,
    spots: Mapping[str, Spot],
    look: Marker,
) -> Badge | None:
    """
    Place a bus on the hook of the road it arrives at the From by.

    Parameters
    ----------
    pattern : TripPattern
        The bus's trip.
    calls : tuple[int, int]
        Its call at the From and the call it is at or last left (see
        `approach_calls`).
    station_of : Mapping[str, str]
        The station of each stop under one.
    approach : Approach
        The rows either side of the From and the road hooks.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.
    look : Marker
        The badge's route, age and liveness.

    Returns
    -------
    Badge | None
        A fixed spur badge on the hook of the place called at just before
        the From, its wedge pointing in along the hook: nearest the track
        at that place, further out before it. None when the bus is at a
        row or across the road from one, or that place has no visible
        hook.
    """
    origin, at = calls
    stop = pattern.stops[at].station
    here = station_of.get(stop, stop)
    spot = spots.get(approach.hooks.get(pattern.stops[origin - 1].station, ''))
    if spot is None or here in spots or approach.facing.get(here) in spots:
        return None
    x = spot.x - SLOT if at == origin - 1 else spot.x + SLOT
    return Badge(
        marker=replace(look, x=x, y=spot.y, bearing=INWARD),
        low=spot.y,
        high=spot.y,
        spur=True,
    )
