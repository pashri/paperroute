"""Tests for the FROM stage's box offering one direction or both."""

from dataclasses import replace

from paperroute_transit.core.chooser import Stage, StripRequest, pressed_from
from paperroute_transit.core.chooser_lists import backed, pressed
from paperroute_transit.core.chooser_sides import (
    SideChoice,
    choice_label,
    pressed_side,
    side_choices,
    turned_from,
    with_line,
)
from paperroute_transit.core.chooser_station import knob_backed
from paperroute_transit.core.model import Pin
from paperroute_transit.core.parent_sides import line_pairs
from paperroute_transit.core.sides import Kerbs
from tests.core.chooser_walks import (
    BUSY_PAIR,
    KERB_LINE,
    PARENT_KERBS,
    PARENT_PAIR,
    QUIET_PAIR,
    S2A,
    S2B,
    at_kerbs,
    at_towards,
)


def test_side_choices_offers_both_first_at_a_quiet_stop() -> None:
    """A quiet pair offers both sides first, a busy one last."""
    assert side_choices(pair=QUIET_PAIR) == (
        SideChoice.BOTH, SideChoice.HERE, SideChoice.ACROSS,
    )
    assert side_choices(pair=BUSY_PAIR) == (
        SideChoice.HERE, SideChoice.ACROSS, SideChoice.BOTH,
    )
    assert side_choices(pair=None) == (SideChoice.HERE,)


def test_turned_from_moves_one_station_a_detent() -> None:
    """With the box closed the knob walks the stations."""
    chooser = at_kerbs()
    assert [
        turned_from(chooser=chooser, steps=n).line_cursor
        for n in (1, 2, -1, 9, -9)
    ] == [3, 4, 1, 4, 0]
    assert turned_from(chooser=chooser, steps=1).kerbs.menu is None


def test_pressed_side_opens_the_box_on_a_paired_stop() -> None:
    """A press on a paired stop opens the box on its first row."""
    after, request = pressed_side(chooser=at_kerbs())
    assert (after.kerbs.menu, after.stage, request) == (0, Stage.FROM, None)


def test_turned_from_in_the_box_stops_at_both_ends() -> None:
    """In the box the knob stays among the station's choices."""
    chooser = at_kerbs(menu=0)
    assert [
        turned_from(chooser=chooser, steps=n).kerbs.menu
        for n in (1, 2, 5, -1)
    ] == [1, 2, 2, 0]
    assert turned_from(chooser=chooser, steps=5).line_cursor == 2


def test_pressed_side_in_the_box_pins_both_directions() -> None:
    """Both directions makes the pin with the kerb across and its ends."""
    _, pin = pressed_side(chooser=at_kerbs(menu=0))
    assert isinstance(pin, Pin)
    assert (pin.origin.stop_id, pin.opposite, pin.destination) == (
        's2', QUIET_PAIR.other, None,
    )
    assert pin.towards == ('Emmanuel Street', 'Oakington')


def test_pressed_side_in_the_box_takes_one_direction_on() -> None:
    """One kerb alone goes on to the TO stage from that kerb."""
    across, request = pressed_side(chooser=at_kerbs(menu=2))
    assert (across.stage, across.origin, across.kerbs.menu) == (
        Stage.TO, QUIET_PAIR.other, None,
    )
    assert isinstance(request, StripRequest) and request.key.station == 'x2'
    here, _ = pressed_side(chooser=at_kerbs(menu=1))
    assert here.origin is not None and here.origin.stop_id == 's2'


def test_pressed_side_at_a_busy_stop_lists_the_directions_first() -> None:
    """At a busy pair both directions comes last."""
    here, _ = pressed_side(chooser=at_kerbs(pairs=(BUSY_PAIR,), menu=0))
    assert here.origin is not None and here.origin.stop_id == 's2'
    _, pin = pressed_side(chooser=at_kerbs(pairs=(BUSY_PAIR,), menu=2))
    assert isinstance(pin, Pin) and pin.opposite == BUSY_PAIR.other


def test_pressed_side_without_a_pair_is_today() -> None:
    """With no pair a press is the plain From press."""
    chooser = at_kerbs(pairs=())
    assert pressed_side(chooser=chooser) == pressed_from(chooser=chooser)


def test_backed_from_the_from_stage_clears_the_kerbs() -> None:
    """Going back to the lists forgets the line's paired kerbs."""
    back = backed(chooser=at_kerbs(menu=None))
    assert back is not None
    assert back.kerbs == Kerbs()


def test_knob_backed_closes_the_box_and_keeps_the_list() -> None:
    """The first back closes the box; the next leaves the stage."""
    chooser = at_kerbs(menu=1)
    back = knob_backed(chooser=chooser)
    assert back == replace(chooser, kerbs=replace(chooser.kerbs, menu=None))
    assert back is not None
    lists = knob_backed(chooser=back)
    assert lists is not None and lists.stage is Stage.LISTS


def test_backed_from_the_to_stage_returns_to_the_closed_list() -> None:
    """Back from TO lands on the FROM list with the box closed."""
    after, _ = pressed_side(chooser=at_kerbs(menu=1))
    back = backed(chooser=after)
    assert back is not None
    assert (back.stage, back.kerbs.menu, back.line_cursor) == (
        Stage.FROM, None, 2,
    )


def test_choice_label_words_the_box_rows() -> None:
    """The rows name the far end each way."""
    assert [
        choice_label(choice=choice, line=KERB_LINE, cursor=2)
        for choice in side_choices(pair=QUIET_PAIR)
    ] == ['Both directions', 'Towards Emmanuel Street', 'Towards Oakington']


def test_choice_label_at_either_end_names_no_station_itself() -> None:
    """At the line's ends a kerb is named by its side."""
    assert choice_label(
        choice=SideChoice.ACROSS, line=KERB_LINE, cursor=0,
    ) == 'Only the other side'
    assert choice_label(
        choice=SideChoice.HERE, line=KERB_LINE, cursor=4,
    ) == 'Only this side'


def test_pressed_side_at_a_parent_pins_its_two_kerbs() -> None:
    """Both directions at a parent pins its here kerb and the other."""
    _, pin = pressed_side(chooser=at_kerbs(pairs=(PARENT_PAIR,), menu=0))
    assert isinstance(pin, Pin)
    assert (pin.origin, pin.opposite, pin.destination) == (S2A, S2B, None)
    assert pin.towards == ('Emmanuel Street', 'Oakington')


def test_pressed_side_at_a_parent_takes_one_kerb_on() -> None:
    """The towards rows at a parent take the here kerb or the other."""
    here, request = pressed_side(
        chooser=at_kerbs(pairs=(PARENT_PAIR,), menu=1),
    )
    assert (here.stage, here.origin) == (Stage.TO, S2A)
    assert isinstance(request, StripRequest) and request.key.station == 's2a'
    across, _ = pressed_side(chooser=at_kerbs(pairs=(PARENT_PAIR,), menu=2))
    assert across.origin == S2B


def test_pressed_side_on_a_loop_offers_the_parent_once_at_its_first_call() -> (
    None
):
    """A parent a loop calls at twice has one row, pinning its first pair."""
    loop = (*KERB_LINE[:4], KERB_LINE[2])
    pairs = line_pairs(line=loop, pairs={}, parents={'s2': PARENT_KERBS})
    chooser, _ = pressed(chooser=at_towards(), reach=None)
    chooser = with_line(chooser=chooser, towards=11, line=loop, pairs=pairs)
    assert chooser.line == KERB_LINE[:4]
    chooser = replace(
        chooser, line_cursor=2, kerbs=Kerbs(pairs=pairs, menu=0),
    )
    _, pin = pressed_side(chooser=chooser)
    assert isinstance(pin, Pin)
    assert (pin.origin, pin.opposite) == (S2A, S2B)
