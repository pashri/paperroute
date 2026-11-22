"""The FROM stage's choices of kerb at a station with one across the road."""

from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Final

from paperroute_transit.core.chooser import (
    ChooserRequest,
    ChooserState,
    Failure,
    Stage,
    chosen_pin,
    clamped,
    pressed_from,
)
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.reach import Reach, ReachRow, RowKind
from paperroute_transit.core.sides import BOTH_SUFFIX, KerbPair, Kerbs
from paperroute_transit.core.strip_view import line_reach

ONLY_TOWARDS: Final[str] = 'only towards {name}'
ONLY_HERE: Final[str] = 'only this side'
OTHER_SIDE: Final[str] = 'only the other side'
BOTH_LABEL: Final[str] = 'both sides'


class SideChoice(StrEnum):
    """What a press on a paired station can take."""

    HERE = 'here'
    ACROSS = 'across'
    BOTH = 'both'


def with_line(
    *,
    chooser: ChooserState,
    towards: int,
    line: tuple[StopPlace, ...] | None,
    pairs: Sequence[KerbPair] = (),
) -> ChooserState:
    """
    Take in the stations of the pressed towards row and its pairs.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    towards : int
        Towards ID the stations belong to.
    line : tuple[StopPlace, ...] | None
        Stations in running order, or None when they could not be loaded.
    pairs : Sequence[KerbPair]
        The kerbs across the road from those stations that have one.

    Returns
    -------
    ChooserState
        The chooser with the line, each station once at its first call (a
        loop calls at some twice), its pairs, and the cursor on its first
        station; unchanged unless it is picking a From for that towards
        row.
    """
    pressed_towards = chooser.towards
    if (
        chooser.stage is not Stage.FROM
        or pressed_towards is None
        or pressed_towards.key != str(towards)
    ):
        return chooser
    if line is None:
        return replace(chooser, failed=Failure.LOAD)
    places: dict[str, StopPlace] = {}
    for place in line:
        places.setdefault(place.stop_id, place)
    return replace(
        chooser,
        line=tuple(places.values()),
        line_cursor=0,
        kerbs=Kerbs(pairs=tuple(pairs)),
    )


def pair_at(*, chooser: ChooserState) -> KerbPair | None:
    """
    Find the pair of the station under the cursor.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage.

    Returns
    -------
    KerbPair | None
        The pair, or None when the station has none or the line is
        loading.
    """
    if not chooser.line:
        return None
    station = chooser.line[chooser.line_cursor].stop_id
    return next(
        (pair for pair in chooser.kerbs.pairs if pair.station == station),
        None,
    )


def side_choices(*, pair: KerbPair | None) -> tuple[SideChoice, ...]:
    """
    List a station's choices, top row first.

    Parameters
    ----------
    pair : KerbPair | None
        The station's pair, or None.

    Returns
    -------
    tuple[SideChoice, ...]
        Both sides first at a quiet pair and last at a busy one; this
        kerb alone when there is no pair.
    """
    if pair is None:
        return (SideChoice.HERE,)
    if pair.busy:
        return (SideChoice.HERE, SideChoice.ACROSS, SideChoice.BOTH)
    return (SideChoice.BOTH, SideChoice.HERE, SideChoice.ACROSS)


def detent(*, chooser: ChooserState, down: bool) -> ChooserState:
    """
    Move the cursor one row.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage with a line.
    down : bool
        True to move down.

    Returns
    -------
    ChooserState
        The chooser one row on: the station's next choice, else the next
        station's row; unchanged at either end of the line.
    """
    step = 1 if down else -1
    side = chooser.kerbs.side + step
    count = len(side_choices(pair=pair_at(chooser=chooser)))
    if 0 <= side < count:
        return replace(chooser, kerbs=replace(chooser.kerbs, side=side))
    cursor = clamped(
        value=chooser.line_cursor + step, count=len(chooser.line),
    )
    if cursor == chooser.line_cursor:
        return chooser
    return replace(
        chooser,
        line_cursor=cursor,
        kerbs=replace(chooser.kerbs, side=0),
    )


def turned_from(*, chooser: ChooserState, steps: int) -> ChooserState:
    """
    Turn the knob in the FROM stage.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage.
    steps : int
        Detents; positive moves down.

    Returns
    -------
    ChooserState
        The chooser moved, or unchanged while the line is loading.
    """
    if not chooser.line:
        return chooser
    for _ in range(abs(steps)):
        chooser = detent(chooser=chooser, down=steps > 0)
    return chooser


def kerb_ends(*, chooser: ChooserState) -> tuple[str, str]:
    """
    Name the two ends the cursor's station is between.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage with a line.

    Returns
    -------
    tuple[str, str]
        The last station's name, then the first's; each empty when the
        cursor is on that station itself.
    """
    last = len(chooser.line) - 1
    return (
        '' if chooser.line_cursor == last else chooser.line[-1].name,
        '' if chooser.line_cursor == 0 else chooser.line[0].name,
    )


def both_pin(*, chooser: ChooserState, pair: KerbPair) -> Pin:
    """
    Make the pin covering both kerbs at the cursor's station.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage with a line.
    pair : KerbPair
        The station's pair.

    Returns
    -------
    Pin
        The pin with no Destination, the kerb across and both ends.
    """
    station = chooser.line[chooser.line_cursor]
    pin = chosen_pin(
        chooser=replace(chooser, origin=station), destination=None,
    )
    return replace(
        pin, opposite=pair.other, towards=kerb_ends(chooser=chooser),
    )


def pressed_side(
    *, chooser: ChooserState,
) -> tuple[ChooserState, ChooserRequest | Pin | None]:
    """
    Handle a bottom-knob press in the FROM stage.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | Pin | None]
        The chooser and its request, or the pin for both sides.
    """
    pair = pair_at(chooser=chooser)
    choices = side_choices(pair=pair)
    choice = choices[
        clamped(value=chooser.kerbs.side, count=len(choices))
    ]
    if pair is None or choice is SideChoice.HERE:
        return pressed_from(chooser=chooser)
    if choice is SideChoice.ACROSS:
        return pressed_from(chooser=chooser, origin=pair.other)
    return chooser, both_pin(chooser=chooser, pair=pair)


def choice_label(
    *, choice: SideChoice, line: Sequence[StopPlace], cursor: int,
) -> str:
    """
    Word one choice of kerb.

    Parameters
    ----------
    choice : SideChoice
        The choice.
    line : Sequence[StopPlace]
        Stations in running order.
    cursor : int
        Index of the station the choice is under.

    Returns
    -------
    str
        The label; a kerb at either end of the line is named by its side.
    """
    if choice is SideChoice.BOTH:
        return BOTH_LABEL
    if choice is SideChoice.HERE:
        if cursor == len(line) - 1:
            return ONLY_HERE
        return ONLY_TOWARDS.format(name=line[-1].name)
    if cursor == 0:
        return OTHER_SIDE
    return ONLY_TOWARDS.format(name=line[0].name)


def choice_rows(
    *, chooser: ChooserState, choices: Sequence[SideChoice],
) -> list[ReachRow]:
    """
    Make the rows for some choices under the cursor's station.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage with a line.
    choices : Sequence[SideChoice]
        The choices to draw, in order.

    Returns
    -------
    list[ReachRow]
        One indented row per choice.
    """
    station = chooser.line[chooser.line_cursor].stop_id
    return [
        ReachRow(
            place=StopPlace(
                stop_id=f'{station}:{choice}',
                name=choice_label(
                    choice=choice,
                    line=chooser.line,
                    cursor=chooser.line_cursor,
                ),
                point=None,
            ),
            kind=RowKind.PASSED,
            depth=1,
            above=False,
            parent=None,
        )
        for choice in choices
    ]


def side_reach(*, chooser: ChooserState) -> tuple[Reach, int]:
    """
    Lay out the line with the cursor station's choices under it.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage with a line.

    Returns
    -------
    tuple[Reach, int]
        The reach and the cursor's row.
    """
    reach = line_reach(line=chooser.line)
    cursor = chooser.line_cursor
    choices = side_choices(pair=pair_at(chooser=chooser))
    if len(choices) == 1:
        return reach, cursor
    rows = list(reach.rows)
    station = rows[cursor]
    if choices[0] is SideChoice.BOTH:
        station = replace(
            station,
            place=replace(
                station.place, name=station.place.name + BOTH_SUFFIX,
            ),
        )
    extra = choice_rows(chooser=chooser, choices=choices[1:])
    rows[cursor] = station
    rows[cursor + 1:cursor + 1] = extra
    moved = [
        replace(row, parent=row.parent + len(extra))
        if row.parent is not None and row.parent > cursor
        else row
        for row in rows
    ]
    return (
        replace(reach, rows=tuple(moved)),
        cursor
        + clamped(value=chooser.kerbs.side, count=len(choices)),
    )
