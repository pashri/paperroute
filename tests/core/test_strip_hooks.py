"""Tests for hanging buses on roads with no strip rows off a hook."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.reach_factories import reach_of, tail
from paperroute_transit.core.reach import ReachInput, RowKind, build_reach
from paperroute_transit.core.strip_hooks import (
    INWARD,
    SLOT,
    hook_station,
    road_hooks,
    with_road_hooks,
)
from paperroute_transit.core.strip_model import StripInputs
from tests.core.test_strip_approach import approaching, badges_of

MILITARY: Final[dict[str, tuple[str, ...]]] = {'P1': ('Military Rd',)}


def hooked_data(
    *, roads: dict[str, tuple[str, ...]], facing: dict[str, str],
) -> ReachInput:
    """
    Make the runs of `facing_reach` with road hooks added.

    Parameters
    ----------
    roads : dict[str, tuple[str, ...]]
        Roads by the place before the From.
    facing : dict[str, str]
        The station across the road from each stop before the From.

    Returns
    -------
    ReachInput
        The runs.
    """
    here = tail('B1 B2 B3', 6, before='P5 P4 P3 P2 P1')
    there = (tail('A1 A2 A3', 6, before='Q2 Q1'),)
    places = {
        row.place.stop_id: row.place
        for row in reach_of(tails=[here, *there]).rows
    }
    return with_road_hooks(
        data=ReachInput(
            origin=places['C'], tails=(here,), places=places,
            station_of={'x2': 'C'}, across_tails=there, facing=facing,
        ),
        roads=roads,
    )


def hooked(*, minutes: int, facing: dict[str, str]) -> StripInputs:
    """
    Make the approaching bus of `approaching` on a strip with hooks.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00 now; the bus is at P1 at 8:12.
    facing : dict[str, str]
        The station across the road from each stop before the From.

    Returns
    -------
    StripInputs
        The inputs.
    """
    reach = build_reach(data=hooked_data(roads=MILITARY, facing=facing))
    return replace(approaching(minutes=minutes, facing=facing), reach=reach)


def test_road_hooks_hang_buses_off_the_joining_row() -> None:
    """Military Rd buses ride a hook off the From, by how far out."""
    data = hooked_data(roads=MILITARY, facing={})
    assert data.hooks == {'P1': hook_station(road='Military Rd')}
    rows = build_reach(data=data).rows
    hook = next(row for row in rows if row.kind is RowKind.EXIT)
    assert hook.place.name == 'Military Rd'
    assert rows[hook.parent or 0].kind is RowKind.ORIGIN
    (near,), shown = badges_of(inputs=hooked(minutes=12, facing={}))
    (far,), _ = badges_of(inputs=hooked(minutes=6, facing={}))
    row = shown[hook_station(road='Military Rd')]
    assert (near.y, far.y) == (row.y, row.y)
    assert far.x - near.x == 2 * SLOT
    assert near.bearing == far.bearing == INWARD


def test_road_hooks_none_when_every_bus_has_a_row() -> None:
    """A place whose stop across the road is a row needs no hook."""
    data = hooked_data(roads=MILITARY, facing={'P1': 'A1'})
    assert not road_hooks(data=data, roads=MILITARY)
    assert not any(
        row.kind is RowKind.EXIT for row in build_reach(data=data).rows
    )
    assert not road_hooks(data=data, roads={'B1': ('High St',)})
