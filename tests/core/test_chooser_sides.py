"""Tests for the FROM stage's choices of kerb at a paired stop."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import place
from paperroute_transit.core.chooser import (
    ChooserState,
    Stage,
    StripRequest,
    pressed_from,
)
from paperroute_transit.core.chooser_lists import backed, pressed
from paperroute_transit.core.chooser_sides import (
    ONLY_HERE,
    OTHER_SIDE,
    SideChoice,
    choice_label,
    pressed_side,
    side_choices,
    side_reach,
    turned_from,
    with_line,
)
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.sides import KerbPair, Kerbs
from paperroute_transit.core.strip_view import line_reach
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


def test_turned_from_stays_on_the_last_choice_of_the_last_station() -> None:
    """Turning down past the last station's choices stays on the last."""
    pair = replace(QUIET, station='s4')
    last = replace(from_stage(pairs=(pair,)), line_cursor=4)
    after = turned_from(chooser=last, steps=5)
    assert (after.line_cursor, after.kerbs.side) == (4, 2)


def test_turned_from_returns_to_the_first_station_row() -> None:
    """Turning up past the first station's choices stops on its row."""
    pair = replace(QUIET, station='s0')
    first = replace(
        from_stage(pairs=(pair,), side=2), line_cursor=0,
    )
    after = turned_from(chooser=first, steps=-5)
    assert (after.line_cursor, after.kerbs.side) == (0, 0)


def test_backed_from_the_from_stage_clears_the_kerbs() -> None:
    """Going back to the lists forgets the line's paired kerbs."""
    chooser = from_stage(pairs=(QUIET,), side=1)
    back = backed(chooser=chooser)
    assert back is not None
    assert back.kerbs == Kerbs()


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


def test_side_reach_opens_a_quiet_stop_under_the_cursor() -> None:
    """A quiet pair's station means both sides; its kerbs follow it."""
    reach, cursor = side_reach(chooser=from_stage(pairs=(QUIET,)))
    names = [row.place.name for row in reach.rows]
    assert names[2:5] == [
        'Orchard Close, both sides',
        'only towards Emmanuel Street',
        'only towards Oakington',
    ]
    assert reach.rows[2].place.stop_id == 's2'
    assert [row.kind for row in reach.rows[3:5]] == [RowKind.PASSED] * 2
    assert [row.parent for row in reach.rows[3:5]] == [None, None]
    assert [row.parent for row in reach.rows[5:]] == [2, 5]
    assert (reach.stations, cursor) == (5, 2)


def test_side_reach_at_a_busy_stop_offers_both_sides_last() -> None:
    """A busy pair's station is this kerb; both sides comes last."""
    chooser = from_stage(pairs=(BUSY,), side=2)
    reach, cursor = side_reach(chooser=chooser)
    names = [row.place.name for row in reach.rows]
    assert names[2:5] == [
        'Orchard Close', 'only towards Oakington', 'both sides',
    ]
    assert cursor == 4


def test_side_reach_without_pairs_is_the_line() -> None:
    """With no pair at the cursor the strip is the plain line."""
    chooser = from_stage(pairs=())
    assert side_reach(chooser=chooser) == (line_reach(line=LINE), 2)


def test_choice_label_at_either_end_names_no_station_itself() -> None:
    """At the line's ends a kerb is named by its side."""
    assert choice_label(
        choice=SideChoice.ACROSS, line=LINE, cursor=0,
    ) == OTHER_SIDE
    assert choice_label(
        choice=SideChoice.HERE, line=LINE, cursor=4,
    ) == ONLY_HERE
