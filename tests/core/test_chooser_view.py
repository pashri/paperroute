"""Tests for the route chooser screen's view-model."""

from paperroute.core.chooser import (
    Choice,
    Column,
    ColumnRequest,
    Level,
    opened,
    pressed,
    turned,
)
from paperroute.core.chooser_view import build_chooser_view, route_title
from paperroute.core.model import Pin
from paperroute_testkit.core.factories import place
from tests.core.chooser_walks import (
    BUS_606,
    GROUPS,
    TOWARDS,
    at_to,
    at_towards,
    fed,
    opened_at_kind,
)


def test_view_shows_last_three_columns_with_preview() -> None:
    chooser = opened_at_kind()
    chooser, _ = fed(chooser, ColumnRequest(path=BUS_606[:1]), GROUPS)
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    many = Column(level=Level.ROUTE, items=tuple(
        Choice(key=f'r{number}', label='Somewhere', badge=str(number))
        for number in range(600, 630)
    ))
    chooser, _ = fed(chooser, ColumnRequest(path=BUS_606[:2]), many)
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, request = turned(chooser=chooser, steps=20, reach=None)
    chooser, _ = fed(chooser, request, TOWARDS)
    view = build_chooser_view(chooser=chooser)
    assert [column.level for column in view.columns] == [
        Level.GROUP, Level.ROUTE, Level.TOWARDS,
    ]
    assert [column.focused for column in view.columns] == [
        False, True, False,
    ]
    routes = view.columns[1]
    assert (routes.first, routes.total, len(routes.items)) == (12, 30, 11)
    assert routes.items[routes.selected].badge == '620'
    assert routes.selected == 8
    assert view.columns[0].items[view.columns[0].selected].label == '600s'


def test_trail_and_titles() -> None:
    lists = at_towards()
    view = build_chooser_view(chooser=lists)
    assert (view.title, view.trail) == ('Choose a route', 'Bus · 600s · 606')
    assert view.empty is None
    choosing_from, _ = pressed(chooser=lists, reach=None)
    view = build_chooser_view(chooser=choosing_from)
    assert view.title == 'Choose a stop'
    assert view.trail == 'Bus · 600s · 606 · Parramatta'
    assert build_chooser_view(chooser=at_to()).title == 'Choose a destination'


def test_empty_text_while_loading_and_failed() -> None:
    chooser, request = opened(shown=Pin(origin=place('X')))
    assert build_chooser_view(chooser=chooser).empty == 'Loading…'
    chooser, _ = fed(chooser, request, None)
    view = build_chooser_view(chooser=chooser)
    assert view.empty == 'Routes need a timetable update'
    assert chooser.waiting is None


def test_failed_preview_blanks_only_that_column() -> None:
    chooser, _ = fed(opened_at_kind(), ColumnRequest(path=BUS_606[:1]), None)
    view = build_chooser_view(chooser=chooser)
    assert view.empty is None
    assert [column.level for column in view.columns] == [Level.KIND, None]
    assert view.columns[1].note == "Can't load"
    assert view.columns[0].note is None


def test_empty_preview_shows_a_note() -> None:
    chooser, _ = fed(
        opened_at_kind(), ColumnRequest(path=BUS_606[:1]),
        Column(level=Level.GROUP, items=()),
    )
    preview = build_chooser_view(chooser=chooser).columns[1]
    assert (preview.note, preview.total, preview.items) == (
        'Nothing here', 0, (),
    )


def test_route_title_names_the_route_then_the_towards_row() -> None:
    """The route number alone, then 'to' the towards row once pressed."""
    assert route_title(chooser=at_towards()) == '606'
    assert route_title(chooser=at_to()) == '606 to Parramatta'
