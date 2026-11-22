"""The FROM stage's choices of kerb at a station with one across the road."""

from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum

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
from paperroute_transit.core.sides import KerbPair, Kerbs


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
