"""Tests for reading every row's preview in one job."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.fakes import FakeNetwork
from paperroute_transit.core.chooser import Choice, Column, Level
from paperroute_transit.core.chooser_lists import preview_steps
from paperroute_transit.core.chooser_previews import (
    MAX_COLLAPSED,
    previews_read,
    read_chain,
)
from paperroute_transit.core.messages import LoadPreviews, PreviewsLoaded
from tests.core.chooser_walks import (
    BUS,
    BUS_606,
    GROUPS,
    REPLACEMENT_LINES,
    REPLACEMENT_ROUTES,
    REPLACEMENTS_PATH,
    ROUTES,
    TOWARDS,
    WALK,
)

SINGLE: Final[Column] = Column(
    level=Level.ROUTE, items=(Choice(key='r1', label='One', badge='1'),),
)


def test_read_chain_reads_the_next_column() -> None:
    """The column after the path is read."""
    assert read_chain(network=WALK, path=BUS_606) == (TOWARDS,)


def test_read_chain_passes_single_row_columns() -> None:
    """Single-row columns are stored collapsed and read through."""
    assert read_chain(network=WALK, path=REPLACEMENTS_PATH) == (
        replace(REPLACEMENT_LINES, collapsed=True),
        replace(REPLACEMENT_ROUTES, collapsed=True),
        TOWARDS,
    )


def test_read_chain_of_a_missing_column_is_empty() -> None:
    """A path with no column gives an empty chain."""
    assert not read_chain(
        network=WALK, path=(*BUS, (Level.GROUP, '1-99')),
    )


def test_read_chain_stops_after_the_last_collapsed_column() -> None:
    """A catalogue that never ends is cut off."""
    chain = read_chain(network=FakeNetwork(column=SINGLE), path=BUS)
    assert chain == (
        *(replace(SINGLE, collapsed=True),) * MAX_COLLAPSED, SINGLE,
    )


def test_previews_read_answers_every_row_in_order() -> None:
    """Each row's chain is answered in row order."""
    effect = LoadPreviews(
        path=BUS, steps=preview_steps(column=GROUPS), timetable='v1',
    )
    assert previews_read(network=WALK, effect=effect) == PreviewsLoaded(
        path=BUS, previews=((), (ROUTES,)), timetable='v1',
    )
