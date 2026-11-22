"""The Stop view of a both-sides pin, with the next bus from each kerb."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import Pin
from paperroute_transit.core.sides import both_sides
from paperroute_transit.core.stop_under import EachWay, Kerb
from paperroute_transit.core.stop_view import (
    HORIZON_TEXT,
    Row,
    StopInputs,
    StopView,
    build_stop_view,
    hero_departure,
    lead_index,
    listed_rows,
    row_count,
    under_number,
)

TOWARDS: Final[str] = 'TOWARDS'
THIS_SIDE: Final[str] = 'THIS SIDE'
OTHER_SIDE: Final[str] = 'OTHER SIDE'
NOTHING_THIS_SIDE: Final[str] = f'Nothing in {HORIZON_TEXT}'


def kerb_of(
    *,
    rows: Sequence[Row],
    items: Sequence[LiveDeparture],
    across: bool,
    end: str,
    side: str,
) -> Kerb[Row]:
    """
    Build the block for one kerb from its own departures.

    Parameters
    ----------
    rows : Sequence[Row]
        Rows of every departure in view, in time order.
    items : Sequence[LiveDeparture]
        The departures behind `rows`, in the same order.
    across : bool
        True for the kerb across the road.
    end : str
        The towards end for this kerb, or '' for none.
    side : str
        The label when `end` is ''.

    Returns
    -------
    Kerb[Row]
        The kerb's first bus that runs and what follows it, or a block
        with no hero when none does.
    """
    kept = [
        (item, row)
        for item, row in zip(items, rows, strict=True)
        if item.departure.facts.across == across
    ]
    own_items = [item for item, _ in kept]
    own_rows = [row for _, row in kept]
    label = TOWARDS if end else side
    lead = lead_index(rows=own_rows)
    if lead is None:
        return Kerb(label=label, end=end, hero=None, under=None)
    return Kerb(
        label=label,
        end=end,
        hero=own_rows[lead],
        under=under_number(items=own_items, rows=own_rows, lead=lead),
    )


def each_way(*, inputs: StopInputs, towards: Sequence[str]) -> StopView:
    """
    Build the Stop view with the next bus from each kerb.

    Parameters
    ----------
    inputs : StopInputs
        The view's inputs.
    towards : Sequence[str]
        The towards ends of this kerb and the kerb across, '' for none.

    Returns
    -------
    StopView
        The time-order view with `each_way` set and the list holding
        every other departure; the time-order view itself when no bus
        runs.
    """
    view = build_stop_view(inputs=inputs)
    if view.hero is None:
        return view
    items, rows = listed_rows(inputs=inputs)
    ends = (*towards, '', '')
    here = kerb_of(
        rows=rows, items=items, across=False, end=ends[0], side=THIS_SIDE,
    )
    far = kerb_of(
        rows=rows, items=items, across=True, end=ends[1], side=OTHER_SIDE,
    )
    heroes = (here.hero, far.hero)
    rest = [row for row in rows if not any(row is hero for hero in heroes)]
    shown = row_count(large=view.large, under_banner=view.banner is not None)
    offset = max(0, min(inputs.scroll, len(rest) - shown))
    return replace(
        view,
        rows=tuple(rest[offset : offset + shown]),
        scroll=offset,
        each_way=EachWay(here=here, across=far),
    )


def hero_station(*, view: StopView, pin: Pin, station: str) -> str:
    """
    Name the kerb the time-order hero leaves from.

    Parameters
    ----------
    view : StopView
        The time-order Stop view.
    pin : Pin
        The shown pin.
    station : str
        The station the board reads today.

    Returns
    -------
    str
        The opposite kerb's stop when the pin is both-sides, reads its
        own From, and the hero leaves from across the road; else
        `station`.
    """
    if not both_sides(pin=pin) or station != pin.origin.stop_id:
        return station
    hero = hero_departure(view=view)
    if hero is None or not hero.facts.across or pin.opposite is None:
        return station
    return pin.opposite.stop_id
