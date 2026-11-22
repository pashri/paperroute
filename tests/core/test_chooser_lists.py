"""Tests for the chooser's LISTS stage."""

from dataclasses import replace
from typing import Final

from paperroute_transit.core.chooser import ChooserState, Failure, Level
from paperroute_transit.core.chooser_lists import (
    preview_steps,
    previews_failed,
    with_previews,
)
from tests.core.chooser_walks import (
    BUS,
    BUS_606,
    GROUPS,
    KINDS,
    REPLACEMENT_GROUPS,
    ROUTES,
    TOWARDS,
    TRAIN,
    TRAIN_LINES,
)

GROUP_PREVIEWS: Final = ((), (ROUTES,))
ASKING: Final[ChooserState] = ChooserState(
    columns=(KINDS, replace(GROUPS, previews=())), cursors=(0, 1), focus=1,
)  # the GROUP column focused on 600s, its previews being read


def test_preview_steps_uses_each_rows_own_level() -> None:
    """A replacements row keeps its own level."""
    assert preview_steps(column=REPLACEMENT_GROUPS) == (
        (Level.GROUP, '1-99'),
        (Level.GROUP, '600s'),
        (Level.REPLACEMENTS, 'replacement'),
    )


def test_with_previews_keeps_them_and_shows_the_cursors_preview() -> None:
    """The focus keeps its previews and shows the cursor row's."""
    after = with_previews(chooser=ASKING, path=BUS, previews=GROUP_PREVIEWS)
    assert after.columns == (
        KINDS, replace(GROUPS, previews=GROUP_PREVIEWS), ROUTES,
    )
    assert (after.cursors, after.failed) == ((0, 1, 0), None)


def test_with_previews_marks_a_row_with_nothing_after_it_absent() -> None:
    """A row with an empty chain leaves the slot absent."""
    first = replace(ASKING, cursors=(0, 0))
    after = with_previews(chooser=first, path=BUS, previews=GROUP_PREVIEWS)
    assert (len(after.columns), after.failed) == (2, Failure.ABSENT)


def test_with_previews_ignores_a_reply_for_a_closed_column() -> None:
    """A reply that no longer fits changes nothing."""
    for path, previews in (
        (TRAIN, GROUP_PREVIEWS),
        (BUS, ((ROUTES,),)),
        (BUS_606[:2], GROUP_PREVIEWS),
    ):
        assert with_previews(
            chooser=ASKING, path=path, previews=previews,
        ) == ASKING
    done = with_previews(chooser=ASKING, path=BUS, previews=GROUP_PREVIEWS)
    assert with_previews(chooser=done, path=BUS, previews=((), ())) == done


def test_with_previews_for_an_earlier_column_leaves_the_screen() -> None:
    """An earlier column keeps the reply without changing the screen."""
    earlier = replace(
        ASKING, columns=(replace(KINDS, previews=()), GROUPS, ROUTES),
        cursors=(0, 1, 0), focus=2,
    )
    kinds = ((GROUPS,), (TRAIN_LINES,), (TOWARDS,))
    after = with_previews(chooser=earlier, path=(), previews=kinds)
    assert after.columns == (
        replace(KINDS, previews=kinds), GROUPS, ROUTES,
    )


def test_previews_failed_says_cant_load_for_the_focus() -> None:
    """A failed read for the focus drops its preview and fails."""
    after = previews_failed(chooser=ASKING, path=BUS)
    assert after.columns[1].previews is None
    assert after.failed is Failure.LOAD
    assert previews_failed(chooser=ASKING, path=TRAIN) == ASKING
    earlier = replace(
        ASKING, columns=(replace(KINDS, previews=()), GROUPS, ROUTES),
        cursors=(0, 1, 0), focus=2,
    )
    after = previews_failed(chooser=earlier, path=())
    assert after.columns == (KINDS, GROUPS, ROUTES)
    assert after.failed is None
