"""Tests for the FROM stage's choices of kerb at a paired stop."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import place
from paperroute_transit.core.chooser import (
    ChooserState,
    Stage,
    StripRequest,
    pressed,
    pressed_from,
)
from paperroute_transit.core.chooser_sides import (
    SideChoice,
    pressed_side,
    side_choices,
    turned_from,
    with_line,
)
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.sides import KerbPair, Kerbs
from tests.core.chooser_walks import at_towards

LINE: Final[tuple[StopPlace, ...]] = (
    place('s0', name='Oakington'),
    place('s1', name='Histon'),
    place('s2', name='Orchard Close'),
    place('s3', name='Mitcham'),
    place('s4', name='Emmanuel Street'),
)
QUIET: Final[KerbPair] = KerbPair(
    station='s2', other=place('x2', name='Orchard Close'), busy=False,
)
BUSY: Final[KerbPair] = replace(QUIET, busy=True)


def from_stage(
    *, pairs: tuple[KerbPair, ...], side: int = 0,
) -> ChooserState:
    """Open 606's first towards row on LINE, cursor on Orchard Close."""
    chooser, _ = pressed(chooser=at_towards(), reach=None)
    chooser = with_line(chooser=chooser, towards=11, line=LINE, pairs=pairs)
    return replace(
        chooser, line_cursor=2, kerbs=Kerbs(pairs=pairs, side=side),
    )


def test_side_choices_offers_both_first_at_a_quiet_stop() -> None:
    """A quiet pair offers both sides first, a busy one last."""
    assert side_choices(pair=QUIET) == (
        SideChoice.BOTH, SideChoice.HERE, SideChoice.ACROSS,
    )
    assert side_choices(pair=BUSY) == (
        SideChoice.HERE, SideChoice.ACROSS, SideChoice.BOTH,
    )
    assert side_choices(pair=None) == (SideChoice.HERE,)


def test_turned_from_walks_into_the_open_choices() -> None:
    """The cursor steps through a paired station's choices."""
    chooser = from_stage(pairs=(QUIET,))
    steps = [turned_from(chooser=chooser, steps=n) for n in (1, 2, 3)]
    assert [(c.line_cursor, c.kerbs.side) for c in steps] == [
        (2, 1), (2, 2), (3, 0),
    ]
    back = turned_from(chooser=steps[2], steps=-1)
    assert (back.line_cursor, back.kerbs.side) == (2, 0)


def test_turned_from_stops_at_both_ends() -> None:
    """The cursor stays on a row past either end of the line."""
    last = replace(from_stage(pairs=(QUIET,)), line_cursor=4)
    after = turned_from(chooser=last, steps=5)
    assert (after.line_cursor, after.kerbs.side) == (4, 0)
    first = replace(last, line_cursor=0)
    before = turned_from(chooser=first, steps=-3)
    assert (before.line_cursor, before.kerbs.side) == (0, 0)


def test_pressed_side_commits_both_sides_at_once() -> None:
    """Both sides makes the pin with the kerb across and its ends."""
    _, pin = pressed_side(chooser=from_stage(pairs=(QUIET,)))
    assert isinstance(pin, Pin)
    assert (pin.origin.stop_id, pin.opposite, pin.destination) == (
        's2', QUIET.other, None,
    )
    assert pin.towards == ('Emmanuel Street', 'Oakington')


def test_pressed_side_takes_the_other_kerb_to_the_to_stage() -> None:
    """The kerb across alone goes on to the TO stage from that kerb."""
    chooser = from_stage(pairs=(QUIET,), side=2)
    after, request = pressed_side(chooser=chooser)
    assert after.stage is Stage.TO and after.origin == QUIET.other
    assert isinstance(request, StripRequest)
    assert request.key.station == 'x2'


def test_pressed_side_at_a_busy_stop_keeps_this_kerb_first() -> None:
    """At a busy pair the station row is this kerb alone."""
    after, request = pressed_side(chooser=from_stage(pairs=(BUSY,)))
    assert after.origin is not None and after.origin.stop_id == 's2'
    assert isinstance(request, StripRequest)


def test_pressed_side_without_pairs_is_today() -> None:
    """With no pairs a press is the plain From press."""
    chooser = from_stage(pairs=())
    assert pressed_side(chooser=chooser) == pressed_from(chooser=chooser)
